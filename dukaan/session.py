"""UI-agnostic orchestration seam for Dukaan Saathi (Phase 5).

This is the single entry point any front-end — the teammate's Gradio UI today, a
future HF Space tomorrow — calls to drive one shopkeeper turn end to end. It does
NO heavy lifting itself: it merely *orchestrates* the existing modules (STT →
image OCR → the deepagents loop → staging/commit → TTS) and returns a structured
:class:`TurnResult` that the UI can render however it likes.

Design decisions:
- Pure orchestration: every capability already lives in another module; this file
  only wires them together and shapes the result. No SQL, no model code here.
- Import-light: no model load and no network at import time — heavy work happens
  lazily inside the modules we call (``stt`` / ``normalize`` / ``agent`` / ``tts``).
- Never crash the caller: :func:`handle_turn` / :func:`confirm_pending` wrap their
  bodies so a front-end always gets a :class:`TurnResult` with a Hindi message,
  never an exception.
- No Gradio import — this module must be usable from any UI (or none).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dukaan import agent, config, i18n, llm, normalize, ops, proactive, staging, stt, tts

# A short Hindi apology reused whenever the agent gives us nothing or we hit an
# unexpected error — kept here so the seam owns its own user-facing fallback.
_APOLOGY_HI = "माफ़ कीजिए, कुछ गड़बड़ हो गई। थोड़ी देर बाद फिर कोशिश करें।"


# --------------------------------------------------------------------------- models


@dataclass(frozen=True)
class PendingConfirmation:
    """A staged write batch awaiting the owner's yes/no.

    ``token`` is the staging ``batch_id`` to commit; ``prompt`` is the Hindi
    summary shown for confirmation. ``options`` / ``payload`` are seams for richer
    front-ends and default to empty.
    """

    token: str
    kind: str = "commit_write"
    prompt: str = ""
    options: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    """Everything a front-end needs to render one shopkeeper turn.

    ``reply_text`` is the Hindi reply to show/speak; ``reply_audio`` is the
    optional ``(sr, ndarray)`` TTS output (``None`` when TTS is off/empty).
    ``pending_confirmation`` is set when the agent staged a write that needs a
    yes/no. ``clarification`` / ``needs_reupload`` flag STT/OCR retries.
    ``dashboard_snapshot`` is a fresh best-effort snapshot for the side panel.
    """

    reply_text: str = ""
    reply_language: str = "hi"
    reply_audio: tuple | None = None
    detected_language: str = "hi"
    pending_confirmation: PendingConfirmation | None = None
    clarification: str | None = None
    needs_reupload: bool = False
    dashboard_snapshot: dict = field(default_factory=dict)
    intent_badge: str = "chat"
    tool_calls: list = field(default_factory=list)
    user_text: str = ""
    error: str | None = None
    # progress hint for streaming turns ("" | "read" | "write"): what the agent is
    # doing before the answer streams (a tool/DB call). UI shows it with the dots.
    status: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-able view of this result.

        Drops the raw ``reply_audio`` ndarray (replaced by a ``has_audio`` bool)
        and flattens ``pending_confirmation`` to its ``__dict__`` (or ``None``).
        """
        return {
            "reply_text": self.reply_text,
            "reply_language": self.reply_language,
            "has_audio": self.reply_audio is not None,
            "detected_language": self.detected_language,
            "pending_confirmation": (
                dict(self.pending_confirmation.__dict__)
                if self.pending_confirmation is not None
                else None
            ),
            "clarification": self.clarification,
            "needs_reupload": self.needs_reupload,
            "dashboard_snapshot": self.dashboard_snapshot,
            "intent_badge": self.intent_badge,
            "tool_calls": self.tool_calls,
            "user_text": self.user_text,
            "error": self.error,
            "status": self.status,
        }


# --------------------------------------------------------------------------- helpers


def dashboard_snapshot_struct() -> dict:
    """Best-effort structured dashboard snapshot for any front-end.

    Starts from :func:`ops.dashboard_snapshot` and adds the next-festival nudge
    (:func:`proactive.festival_nudge`) and a ``server_up`` health flag
    (:func:`llm.health`). Every piece is wrapped so a single failure never sinks
    the whole snapshot; a total failure returns ``{"error": str(e)}``.
    """
    try:
        try:
            snap = dict(ops.dashboard_snapshot())
        except Exception:
            snap = {}
        try:
            snap["festival"] = proactive.festival_nudge()
        except Exception:
            snap["festival"] = None
        try:
            snap["server_up"] = llm.health()
        except Exception:
            snap["server_up"] = False
        return snap
    except Exception as e:  # noqa: BLE001 — snapshot is always best-effort
        return {"error": str(e)}


def _tts_or_none(text: str, tts_on: bool) -> tuple | None:
    """Synthesize ``text`` to ``(sr, ndarray)`` when ``tts_on`` and non-empty.

    Returns ``None`` when TTS is off or the text is empty, and swallows any
    synthesis failure (so a broken voice path never breaks a turn).
    """
    if not (tts_on and text):
        return None
    try:
        return tts.synthesize(text)
    except Exception:
        return None


# Tiny Hindi/Hinglish yes/no detectors for the confirm step. Substring-based and
# deliberately small — the agent already does the heavy intent work; this only
# reads a one-word confirm reply.
_YES_WORDS = ("haan", "haa", "ha ", "yes", "ok", "okay", "theek", "thik", "sahi", "kar do", "karo")
_NO_WORDS = ("nahi", "nahin", "na ", "no", "cancel", "rehne do", "mat")
# Hedges that contain a "nahi"/"na" but are NOT a refusal.
_HEDGES = ("pata nahi", "pata nahin", "shayad")


def _looks_yes(s) -> bool:
    """True when ``s`` reads as an affirmative ("haan / yes / theek / kar do")."""
    t = f" {str(s or '').strip().lower()} "
    if any(h in t for h in _HEDGES):
        return False
    return any(w in t for w in _YES_WORDS)


def _looks_no(s) -> bool:
    """True when ``s`` reads as a refusal ("nahi / no / cancel / rehne do").

    Hedges like "pata nahi" / "shayad" are explicitly NOT treated as a no.
    """
    t = f" {str(s or '').strip().lower()} "
    if any(h in t for h in _HEDGES):
        return False
    return any(w in t for w in _NO_WORDS)


# --------------------------------------------------------------------------- turns


def _prepare_turn(audio, text, image, thread_id, tts):
    """Assemble the user message (STT / OCR / typed) for one turn.

    Returns a ``(user_text, detected_lang)`` tuple to run the agent on, OR a
    finished :class:`TurnResult` that short-circuits the turn (STT/OCR retry,
    empty input, or a deterministic yes/no confirm). Shared by :func:`handle_turn`
    and :func:`handle_turn_stream` so their pre-agent behaviour stays identical.
    """
    user_text = ""
    detected_lang = config.OWNER_LANG or "hi"

    # voice in -> transcribe (short-circuit to a clarification on failure)
    if audio is not None:
        r = stt.transcribe(audio)
        detected_lang = r.language or detected_lang
        if not r.ok:
            clar = i18n.stt_retry_message(r.language, r.reason)
            return TurnResult(
                reply_text=clar, clarification=clar, detected_language=detected_lang,
                reply_audio=_tts_or_none(clar, tts),
                dashboard_snapshot=dashboard_snapshot_struct(), intent_badge="chat",
            )
        user_text = r.text
    elif text:
        user_text = (text or "").strip()

    # image in -> OCR-describe and merge (re-upload short-circuit on failure)
    if image is not None:
        d = normalize.describe_for_agent(image)
        if not d.ok:
            return TurnResult(
                reply_text=d.agent_text, needs_reupload=True, clarification=d.agent_text,
                detected_language=detected_lang, reply_audio=_tts_or_none(d.agent_text, tts),
                dashboard_snapshot=dashboard_snapshot_struct(), intent_badge="chat",
            )
        user_text = (user_text + "\n" + d.agent_text).strip() if user_text else d.agent_text

    # nothing actionable -> ask the owner to repeat
    if not user_text.strip():
        clar = i18n.stt_retry_message(detected_lang, "empty")
        return TurnResult(
            reply_text=clar, clarification=clar, detected_language=detected_lang,
            reply_audio=_tts_or_none(clar, tts),
            dashboard_snapshot=dashboard_snapshot_struct(), intent_badge="chat",
        )

    # Deterministic confirm: if a write is staged and the owner says a clear
    # yes/no, resolve it HERE (the small LLM is only needed to STAGE; commit/cancel
    # never depends on it).
    if staging.get_pending(thread_id) and (_looks_yes(user_text) or _looks_no(user_text)):
        tr = confirm_pending(user_text, thread_id=thread_id, tts=tts)
        return TurnResult(
            reply_text=tr.reply_text, reply_audio=tr.reply_audio, detected_language=detected_lang,
            dashboard_snapshot=tr.dashboard_snapshot, intent_badge="write", user_text=user_text,
        )

    return user_text, detected_lang


def handle_turn(
    *,
    audio=None,
    text=None,
    image=None,
    thread_id: str,
    tts: bool = True,
    mode: str = "auto",
    reply_lang: str = "en",
) -> TurnResult:
    """Drive one shopkeeper turn (voice and/or image and/or text) end to end.

    Assembles the user message (transcribe / OCR / typed), runs the agent, and
    shapes the reply (plus optional TTS and a fresh dashboard snapshot) into a
    :class:`TurnResult`. STT/OCR failures short-circuit into a clarification turn.
    Never raises: any unexpected error becomes a polite Hindi apology result.
    """
    try:
        prep = _prepare_turn(audio, text, image, thread_id, tts)
        if isinstance(prep, TurnResult):
            return prep
        user_text, detected_lang = prep

        res = agent.run_agent(user_text, thread_id=thread_id, reply_lang=reply_lang)
        reply = (res.get("reply") or "").strip() or _APOLOGY_HI
        pend = res.get("pending")
        pc = (PendingConfirmation(token=pend["batch_id"], prompt=pend.get("summary_hi", ""))
              if pend else None)
        return TurnResult(
            reply_text=reply,
            detected_language=detected_lang,
            reply_audio=_tts_or_none(reply, tts),
            pending_confirmation=pc,
            intent_badge=res.get("intent", "chat"),
            tool_calls=res.get("tool_calls", []),
            user_text=user_text,
            dashboard_snapshot=dashboard_snapshot_struct(),
            error=res.get("error"),
        )
    except Exception as e:  # noqa: BLE001 — the seam must never crash the front-end
        return TurnResult(
            reply_text=_APOLOGY_HI,
            error=str(e),
            dashboard_snapshot=dashboard_snapshot_struct(),
            intent_badge="chat",
        )


def handle_turn_stream(
    *,
    audio=None,
    text=None,
    image=None,
    thread_id: str,
    tts: bool = False,
    mode: str = "auto",
    reply_lang: str = "en",
):
    """Streaming variant of :func:`handle_turn` — a generator of :class:`TurnResult`.

    Yields the partial reply as the agent's answer streams in (each TurnResult has
    a longer ``reply_text``); the FINAL yield carries the complete reply plus
    ``pending_confirmation``, ``tool_calls``, ``intent_badge`` and a fresh
    ``dashboard_snapshot``. Non-agent turns (STT/OCR retry, empty, yes/no confirm)
    yield a single finished TurnResult. ``tts`` defaults off (the UI speaks on
    demand). Never raises.
    """
    try:
        prep = _prepare_turn(audio, text, image, thread_id, tts)
        if isinstance(prep, TurnResult):
            yield prep
            return
        user_text, detected_lang = prep

        partial = ""
        final: dict = {}
        for kind, payload in agent.stream_agent(user_text, thread_id=thread_id, reply_lang=reply_lang):
            if kind == "delta":
                partial = payload
                yield TurnResult(
                    reply_text=partial, detected_language=detected_lang,
                    user_text=user_text, intent_badge="chat",
                )
            elif kind == "status":
                # progress hint (tool/DB call) before any answer text streams
                yield TurnResult(
                    status=payload, detected_language=detected_lang,
                    user_text=user_text, intent_badge="chat",
                )
            else:  # "final"
                final = payload or {}

        reply = (final.get("reply") or partial or "").strip() or _APOLOGY_HI
        pend = final.get("pending")
        pc = (PendingConfirmation(token=pend["batch_id"], prompt=pend.get("summary_hi", ""))
              if pend else None)
        yield TurnResult(
            reply_text=reply,
            detected_language=detected_lang,
            reply_audio=_tts_or_none(reply, tts),
            pending_confirmation=pc,
            intent_badge=final.get("intent", "chat"),
            tool_calls=final.get("tool_calls", []),
            user_text=user_text,
            dashboard_snapshot=dashboard_snapshot_struct(),
            error=final.get("error"),
        )
    except Exception as e:  # noqa: BLE001 — the seam must never crash the front-end
        yield TurnResult(
            reply_text=_APOLOGY_HI,
            error=str(e),
            dashboard_snapshot=dashboard_snapshot_struct(),
            intent_badge="chat",
        )


def confirm_pending(answer: str, *, thread_id: str, tts: bool = True) -> TurnResult:
    """Resolve a pending write from the owner's yes/no ``answer``.

    On yes, commit the staged batch (:func:`staging.commit_pending`) and speak its
    Hindi confirmation; on no, drop it (:func:`staging.clear_pending`); on an
    unclear answer, ask again. Always returns a ``write``-badged :class:`TurnResult`.
    """
    if _looks_yes(answer):
        r = staging.commit_pending(thread_id)
        msg = r.get("message_hi", "Theek hai.")
    elif _looks_no(answer):
        staging.clear_pending(thread_id)
        msg = "Theek hai, kuch nahi likha."
    else:
        msg = "Haan ya nahi boliye."
    return TurnResult(
        reply_text=msg,
        reply_audio=_tts_or_none(msg, tts),
        dashboard_snapshot=dashboard_snapshot_struct(),
        intent_badge="write",
    )


def _strip_maalik(s: str) -> str:
    """Scrub the 'maalik'/'मालिक' (master/owner) honorific the model sometimes adds.

    The briefing speaks *to* the shopkeeper; being addressed as 'master' is not
    wanted. We instruct the model against it in the system prompt and scrub the
    output here as a guarantee. Handles the common '<maalik> ji' vocative too.
    """
    if not s:
        return s
    s = re.sub(r"\b(maa?lik)\s+ji\b[\s,]*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"मालिक\s*जी[\s,]*", "", s)
    s = re.sub(r"\bmaa?lik\b\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"मालिक\s*", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.।!?])", r"\1", s)
    s = re.sub(r"^[\s,।.!?]+", "", s)   # tidy a sentence-initial leftover comma
    return s.strip()


def morning_briefing(*, tts: bool = False) -> dict:
    """Compose the "subah ka haal" briefing from the proactive checks.

    Runs every proactive agent (:func:`proactive.run_all`), joins their Hindi
    messages, then makes ONE best-effort LLM call to phrase a ~50-word friendly
    Hindi summary — falling back to the plain joined text if the LLM is down.
    Returns ``{"text", "audio", "parts"}`` (``audio`` honours ``tts``).
    """
    a = proactive.run_all()
    parts = [a["expiry"]["message"], a["udhaar"]["message"], a["festival"]["message"]]
    base = "\n".join(p for p in parts if p)

    system = (
        "Tum ek kirana dukaan ke samajhdar sahayak ho jo dukaandaar ko subah ka "
        "haal saral, dostana Hindi me batate ho. Dukaandaar ko kabhi 'maalik' ya "
        "'malik' mat kaho, hamesha 'aap' kaho ya seedha baat karo."
    )
    prompt = (
        "Niche aaj subah ke kuch tathya diye hain (expiry, udhaar, tyohaar). "
        "Inse ek chhoti (lagbhag 50 shabd) friendly Hindi 'subah ka haal' likho — "
        "sirf wahi baat jo tathya me hai, koi nayi baat mat jodo.\n\n"
        f"{base}"
    )
    try:
        text = (llm.complete(prompt, system=system) or "").strip() or base
    except Exception:
        text = base
    text = _strip_maalik(text)

    audio = _tts_or_none(text, tts)
    return {"text": text, "audio": audio, "parts": a}


def speak(text: str) -> tuple | None:
    """Synthesize arbitrary text to ``(sr, ndarray)`` for on-demand playback.

    Backs the UI's per-message speaker button: replies are silent by default and
    the owner taps to hear one. Never raises — returns ``None`` on empty/failure.
    """
    return _tts_or_none(text, True)


def speak_stream(text: str):
    """Stream TTS for ``text`` as ``(sr, ndarray)`` chunks, sentence by sentence.

    Same job as :func:`speak` but a generator: the speaker button plays the FIRST
    sentence within ~1-2s instead of waiting for the whole reply to synthesize.
    Yields nothing on empty input; never raises (a broken voice path just ends the
    stream).
    """
    if not (text and str(text).strip()):
        return
    try:
        for chunk in tts.synthesize_stream(text):
            if chunk is not None:
                yield chunk
    except Exception:  # noqa: BLE001 — never break the UI on the voice path
        return


def decode_audio(path):
    """Decode a recorded audio file (incl. browser webm/opus) to ``(sr, ndarray)``.

    Thin seam over :func:`stt.decode_audio_file` so the front-end never imports
    ``stt`` directly. Returns ``None`` when no decoder can read the file.
    """
    return stt.decode_audio_file(path)
