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


def handle_turn(
    *,
    audio=None,
    text=None,
    image=None,
    thread_id: str,
    tts: bool = True,
    mode: str = "auto",
) -> TurnResult:
    """Drive one shopkeeper turn (voice and/or image and/or text) end to end.

    Assembles the user message from whatever was provided — transcribing audio,
    OCR-describing an image, taking typed text — then runs the agent and shapes
    the reply (plus optional TTS and a fresh dashboard snapshot) into a
    :class:`TurnResult`. STT/OCR failures short-circuit into a clarification turn.
    Never raises: any unexpected error becomes a polite Hindi apology result.
    """
    try:
        # 1) defaults
        user_text = ""
        detected_lang = config.OWNER_LANG or "hi"

        # 2) voice in -> transcribe (short-circuit to a clarification on failure)
        if audio is not None:
            r = stt.transcribe(audio)
            detected_lang = r.language or detected_lang
            if not r.ok:
                clar = i18n.stt_retry_message(r.language, r.reason)
                return TurnResult(
                    reply_text=clar,
                    clarification=clar,
                    detected_language=detected_lang,
                    reply_audio=_tts_or_none(clar, tts),
                    dashboard_snapshot=dashboard_snapshot_struct(),
                    intent_badge="chat",
                )
            user_text = r.text
        # 3) else typed text
        elif text:
            user_text = (text or "").strip()

        # 4) image in -> OCR-describe and merge (re-upload short-circuit on failure)
        if image is not None:
            d = normalize.describe_for_agent(image)
            if not d.ok:
                return TurnResult(
                    reply_text=d.agent_text,
                    needs_reupload=True,
                    clarification=d.agent_text,
                    detected_language=detected_lang,
                    reply_audio=_tts_or_none(d.agent_text, tts),
                    dashboard_snapshot=dashboard_snapshot_struct(),
                    intent_badge="chat",
                )
            user_text = (
                (user_text + "\n" + d.agent_text).strip() if user_text else d.agent_text
            )

        # 5) nothing actionable -> ask the owner to repeat
        if not user_text.strip():
            clar = i18n.stt_retry_message(detected_lang, "empty")
            return TurnResult(
                reply_text=clar,
                clarification=clar,
                detected_language=detected_lang,
                reply_audio=_tts_or_none(clar, tts),
                dashboard_snapshot=dashboard_snapshot_struct(),
                intent_badge="chat",
            )

        # 5b) Deterministic confirm: if a write is already staged for this thread
        # and the owner's message is a clear yes/no, resolve it HERE instead of
        # relying on the model to re-call confirm_pending_tool. Makes
        # confirm-before-write robust end to end (the small LLM is only needed to
        # STAGE on the first turn; the commit/cancel never depends on it).
        if staging.get_pending(thread_id) and (_looks_yes(user_text) or _looks_no(user_text)):
            tr = confirm_pending(user_text, thread_id=thread_id, tts=tts)
            return TurnResult(
                reply_text=tr.reply_text,
                reply_audio=tr.reply_audio,
                detected_language=detected_lang,
                dashboard_snapshot=tr.dashboard_snapshot,
                intent_badge="write",
                user_text=user_text,
            )

        # 6) run the agent
        res = agent.run_agent(user_text, thread_id=thread_id)
        reply = (res.get("reply") or "").strip() or _APOLOGY_HI
        pend = res.get("pending")
        pc = (
            PendingConfirmation(token=pend["batch_id"], prompt=pend.get("summary_hi", ""))
            if pend
            else None
        )

        # 7) shape the successful turn
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
        "haal saral, dostana Hindi me batate ho."
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

    audio = _tts_or_none(text, tts)
    return {"text": text, "audio": audio, "parts": a}
