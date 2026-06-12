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


# ---------------------------------------------------------------- date formatting
# Locale-independent month / weekday names — we never rely on a system locale being
# installed on the Space. Month tables are indexed [month-1]; weekday tables use
# date.weekday() where Monday == 0. Used by the UI to write the date at the top of
# the ledger page (a real bahi-khata always does), in both English and Hindi.
_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
_EN_MONTHS_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                   "Sep", "Oct", "Nov", "Dec")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday")
_EN_WEEKDAYS_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HI_MONTHS = ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई",
              "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")
_HI_WEEKDAYS = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार",
                "शनिवार", "रविवार")
_HI_WEEKDAYS_ABBR = ("सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि")


def format_date_full(d) -> tuple[str, str]:
    """A long, ledger-style date as ``(english, hindi)``.

    e.g. ``date(2026, 6, 12)`` -> ``("Friday, 12 June 2026", "12 जून 2026, शुक्रवार")``.
    """
    wd, mo = d.weekday(), d.month - 1
    en = f"{_EN_WEEKDAYS[wd]}, {d.day} {_EN_MONTHS[mo]} {d.year}"
    hi = f"{d.day} {_HI_MONTHS[mo]} {d.year}, {_HI_WEEKDAYS[wd]}"
    return en, hi


def format_date_short(d) -> tuple[str, str]:
    """A compact date as ``(english, hindi)``.

    e.g. ``date(2026, 6, 12)`` -> ``("Fri, 12 Jun 2026", "शुक्र, 12 जून 2026")``.
    """
    wd, mo = d.weekday(), d.month - 1
    en = f"{_EN_WEEKDAYS_ABBR[wd]}, {d.day} {_EN_MONTHS_ABBR[mo]} {d.year}"
    hi = f"{_HI_WEEKDAYS_ABBR[wd]}, {d.day} {_HI_MONTHS[mo]} {d.year}"
    return en, hi


def format_month_day(d) -> tuple[str, str]:
    """Just the day + month as ``(english, hindi)``, e.g. ``("28 Aug", "28 अगस्त")``."""
    mo = d.month - 1
    return f"{d.day} {_EN_MONTHS_ABBR[mo]}", f"{d.day} {_HI_MONTHS[mo]}"


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
