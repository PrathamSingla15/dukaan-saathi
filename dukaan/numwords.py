"""Spoken Hindi numbers.

MMS-TTS-hin cannot pronounce Latin digits ("528" comes out garbled), so before
synthesis we convert ₹-amounts and bare numbers into Devanagari number words
("₹528" → "पाँच सौ अट्ठाईस रुपये"). Indian numbering (हज़ार / लाख / करोड़).
"""

from __future__ import annotations

import re

# 0–99 (each Hindi number under 100 is irregular, so a full table).
_ONES = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चौवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पंचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]


def to_hindi_words(n: int) -> str:
    """Integer → Devanagari Hindi words (Indian system)."""
    if n < 0:
        return "माइनस " + to_hindi_words(-n)
    if n < 100:
        return _ONES[n]
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " सौ" + (" " + to_hindi_words(r) if r else "")
    if n < 100_000:
        t, r = divmod(n, 1000)
        return to_hindi_words(t) + " हज़ार" + (" " + to_hindi_words(r) if r else "")
    if n < 10_000_000:
        l, r = divmod(n, 100_000)
        return to_hindi_words(l) + " लाख" + (" " + to_hindi_words(r) if r else "")
    c, r = divmod(n, 10_000_000)
    return to_hindi_words(c) + " करोड़" + (" " + to_hindi_words(r) if r else "")


def _spoken(raw: str) -> str:
    raw = raw.replace(",", "")
    if not any(ch.isdigit() for ch in raw):
        return raw
    if "." in raw:
        ip, dp = raw.split(".", 1)
        out = to_hindi_words(int(ip or 0))
        dp = dp.rstrip("0")
        if dp:
            out += " दशमलव " + " ".join(_ONES[int(d)] for d in dp if d.isdigit())
        return out
    try:
        return to_hindi_words(int(raw))
    except ValueError:
        return raw


def digits_to_words(text: str) -> str:
    """Replace ₹-amounts and bare numbers in ``text`` with Hindi number words."""
    text = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)", lambda m: _spoken(m.group(1)) + " रुपये", text)
    text = text.replace("₹", " रुपये ")
    text = re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: _spoken(m.group(0)), text)
    return text
