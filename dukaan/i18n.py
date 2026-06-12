"""Localisation helpers for Dukaan Saathi.

Provides fallback / clarification strings shown to the shop owner when STT
fails, OCR is unclear, or the agent needs to disambiguate an entity.

Design decisions:
- Replies stay Hindi/Hinglish for now (a deliberate product choice; see the
  reply_lang_directive seam for going multilingual later).
- Every user-facing message is bilingual: Hindi + short English safety net.
- `lang` parameters are accepted everywhere so callers are future-proof, but
  are currently ignored beyond distinguishing hi/en family membership.
- No imports from other dukaan modules — this module must be importable first.
"""

from __future__ import annotations

# ------------------------------------------------------------------ lang registry

# ISO 639-1 code -> human-readable name (Romanised for logging / debug output).
LANG_NAMES: dict[str, str] = {
    "hi": "Hindi",
    "en": "English",
    "mr": "Marathi",
    "ta": "Tamil",
    "bn": "Bengali",
    "pa": "Punjabi",
    "gu": "Gujarati",
    "te": "Telugu",
    "ur": "Urdu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "or": "Odia",
    "ne": "Nepali",
}

# Languages written in Devanagari script.
_DEVANAGARI_CODES: frozenset[str] = frozenset({"hi", "mr", "ne"})


def is_devanagari_lang(code: str) -> bool:
    """Return True if `code` uses the Devanagari script (hi / mr / ne)."""
    return code.strip().lower() in _DEVANAGARI_CODES


# ---------------------------------------------------------------- STT retry

# Maps optional `reason` tag to a short Hindi phrase inserted into the message.
_STT_REASON_PHRASES: dict[str, str] = {
    "noise":    "शोर की वजह से",
    "low_conf": "आवाज़ धीमी रही",
    "no_speech": "कुछ सुनाई नहीं दिया",
}


def stt_retry_message(lang: str = "", reason: str = "") -> str:
    """Return a bilingual please-repeat message after an STT failure.

    Args:
        lang: Owner language ISO code (accepted, currently ignored — always Hindi).
        reason: Optional hint tag: "noise" | "low_conf" | "no_speech".
    """
    reason_phrase = _STT_REASON_PHRASES.get(reason.strip().lower(), "ठीक से सुनाई नहीं दिया")
    return (
        f"माफ़ कीजिए, {reason_phrase} — कृपया दोबारा बोलिए। "
        "(Sorry, please say that again.)"
    )


# ---------------------------------------------------------------- OCR retry

def ocr_retry_message(lang: str = "") -> str:
    """Return a bilingual please-resend message after an OCR failure.

    Args:
        lang: Owner language ISO code (accepted, currently ignored — always Hindi).
    """
    return (
        "फोटो साफ़ नहीं आई — कृपया दोबारा, साफ़ फोटो भेजिए। "
        "(Photo unclear — please re-upload a clearer photo.)"
    )


# ---------------------------------------------------------------- entity clarification

# Hindi label per entity kind.
_KIND_LABELS: dict[str, str] = {
    "item":     "item",       # keep English "item" — common Hinglish in kirana
    "customer": "ग्राहक",
}


def clarify_entity(kind: str, candidates: list[str], lang: str = "") -> str:
    """Return a numbered Hindi disambiguation prompt.

    Args:
        kind: "item" or "customer".
        candidates: List of candidate names/labels (1–N).
        lang: Owner language ISO code (accepted, currently ignored — always Hindi).

    Returns:
        A string like "कौन सा item? 1) Parle-G  2) Parle Marie"
        Never returns an empty string even if candidates is empty.
    """
    if not candidates:
        label = _KIND_LABELS.get(kind, kind)
        return f"कौन सा {label}? (कोई विकल्प नहीं मिला — दोबारा बताइए।)"

    label = _KIND_LABELS.get(kind, kind)
    numbered = "  ".join(f"{i}) {c}" for i, c in enumerate(candidates, 1))
    return f"कौन सा {label}? {numbered}"


# ---------------------------------------------------------------- TTS capability seam

# We voice only Hindi replies today (the reply language); Veena itself also speaks
# English/Hinglish. This set is a seam: widen it when replies go multilingual.
_SPEAKABLE_LANGS: frozenset[str] = frozenset({"hi"})


def speakable(lang: str = "hi") -> bool:
    """Return True if the current TTS engine can voice `lang`.

    Only "hi" returns True today; all other codes return False.
    """
    return lang.strip().lower() in _SPEAKABLE_LANGS


# ---------------------------------------------------------------- reply-language directive (stub)

def reply_lang_directive(lang: str = "hi", mode: str = "hindi_only") -> str:
    """Return a system-prompt snippet that sets the reply language.

    Currently a stub — the agent prompt already enforces Hindi, so callers
    are not required to use this.  Kept as a seam for future multilingual mode.

    Args:
        lang: Owner language ISO code.
        mode: "hindi_only" (default) or "same_as_user".

    Returns:
        "" when mode=="hindi_only" (agent prompt already handles this).
        A one-line directive string when mode=="same_as_user".
    """
    if mode == "hindi_only":
        return ""
    # future: same_as_user — instruct the model to reply in the owner's language.
    lang_name = LANG_NAMES.get(lang.strip().lower(), lang)
    return f"Reply to the user in {lang_name}."
