"""Input normalisation + image OCR for Dukaan Saathi.

Three jobs, all feeding cleaned text or structured data to the agent:

* `ocr_image()` — read a photo via Gemma-4's vision path; returns a structured
  OcrResult (never silently empty).
* `describe_for_agent()` — wraps OCR text in a Hindi lead-in for the agent;
  returns a structured DescribeResult.
* `parse_challan()` — extract structured line-items from a supplier bill photo.
* `parse_khata()` — extract structured customer-credit entries from a ledger photo.
* `normalize_text()` — near-passthrough whitespace tidy-up for text/STT input.
  We deliberately do NOT translate; the agent reasons in Hindi.

No model is loaded at import time — Gemma is only touched inside `ocr_image` /
`parse_challan` / `parse_khata`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from dukaan import i18n, llm

# ------------------------------------------------------------------ constants

OCR_HEADER = "ग्राहक ने एक फोटो भेजी है। उसमें ये जानकारी है:\n"

# Generic OCR prompt (mixed EN/HI; terse for compact model output).
_OCR_PROMPT = (
    "You are reading a photo from an Indian kirana (general) store — it could be "
    "a supplier bill/invoice, a product label, or a handwritten note (Hindi/Hinglish/English). "
    "इस फोटो से काम की जानकारी निकालो / extract the useful facts as a short plain-text list: "
    "item names, quantities (qty), prices/MRP (₹), supplier name, and any dates/expiry. "
    "For a product label, give: product name + MRP + expiry/best-before date. "
    "Sirf jo dikh raha hai wahi likho — koi extra baat add mat karo. "
    "Output only the plain-text list, no preamble."
)

# Challan prompt: return ONLY one JSON object of line items (one-shot grounded).
_CHALLAN_PROMPT = (
    "You are reading a supplier bill / challan / invoice from an Indian kirana store. "
    "It may be printed or handwritten (Hindi / Hinglish / English), and digits may be "
    "Hindi (०१२३४) or English.\n"
    "Return ONLY one valid JSON object (no preamble, no explanation, no markdown fences):\n"
    '{"supplier": "<name or null>", "date": "<YYYY-MM-DD or null>", '
    '"items": [{"name": "<string>", "qty": <number>, "unit": "<string or null>", '
    '"rate": <number or null>, "mrp": <number or null>, "hsn": "<string or null>"}]}\n'
    "Rules:\n"
    "- Output ONE object per line item on the bill — read every row, top to bottom.\n"
    "- Transcribe each item name exactly as written (keep its original script/spelling).\n"
    "- qty, rate and mrp MUST be numbers (not strings); convert Hindi digits to English numbers.\n"
    "- rate = per-unit purchase price; mrp = printed retail price; omit either if not shown.\n"
    "- Omit / null any field you cannot clearly read — do NOT guess.\n"
    "- supplier and date are null unless clearly printed/written.\n"
    "Example output:\n"
    '{"supplier": "Sri Ram Traders", "date": "2026-06-10", "items": ['
    '{"name": "Parle-G 100g", "qty": 24, "unit": "pcs", "rate": 4.5, "mrp": 5, "hsn": null}, '
    '{"name": "Tata Salt 1kg", "qty": 10, "unit": "pkt", "rate": 22, "mrp": 28, "hsn": null}]}\n'
    "Output nothing except the JSON object."
)

# Khata prompt: return ONLY a JSON object for a handwritten credit ledger.
_KHATA_PROMPT = (
    "You are reading a HANDWRITTEN customer-credit ledger (khata / bahi). "
    "Return ONLY valid JSON with this structure (no preamble, no explanation, no markdown):\n"
    '{"customers": [{"name": "<string>", "phone": "<string or null>", '
    '"opening_balance": <number or null>, '
    '"debits": [{"amount": <number>, "items": "<string or null>", "date": "<string or null>"}]}]}\n'
    "Rules:\n"
    "- All monetary amounts MUST be numbers (not strings).\n"
    "- Omit fields not visible; do not guess.\n"
    "- Output nothing except the JSON object."
)

# ------------------------------------------------------------------ dataclasses

@dataclass(frozen=True)
class OcrResult:
    """Structured result from `ocr_image`."""
    text: str
    ok: bool
    doc_kind: str = "unknown"
    reason: str = ""


@dataclass(frozen=True)
class DescribeResult:
    """Structured result from `describe_for_agent`."""
    agent_text: str
    ok: bool
    reason: str = ""


# ------------------------------------------------------------------ JSON salvage

def _extract_json_array(text: str) -> list[dict]:
    """Salvage a JSON array from messy small-model output.

    Strips ```json fences, then finds the first [...] block and parses it.
    Returns [] on any failure.
    """
    # Strip code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Find first '[' ... matching ']'
    start = cleaned.find("[")
    if start == -1:
        return []
    # Walk to find matching bracket
    depth = 0
    end = -1
    for i, ch in enumerate(cleaned[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return []
    try:
        result = json.loads(cleaned[start : end + 1])
        if isinstance(result, list):
            return [d for d in result if isinstance(d, dict)]
        return []
    except (json.JSONDecodeError, ValueError):
        return []


def _extract_json_obj(text: str) -> dict:
    """Salvage a JSON object from messy small-model output.

    Strips ```json fences, then finds the first {...} block and parses it.
    Returns {} on any failure.
    """
    # Strip code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Find first '{' ... matching '}'
    start = cleaned.find("{")
    if start == -1:
        return {}
    depth = 0
    end = -1
    for i, ch in enumerate(cleaned[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return {}
    try:
        result = json.loads(cleaned[start : end + 1])
        if isinstance(result, dict):
            return result
        return {}
    except (json.JSONDecodeError, ValueError):
        return {}


# ------------------------------------------------------------------ OCR core

def ocr_image(
    image: Any,
    hint: str | None = None,
    doc_kind: str = "auto",
) -> OcrResult:
    """Read a kirana photo via Gemma-4 vision; return a structured OcrResult.

    Picks the prompt by `doc_kind`:
    - "challan" → _CHALLAN_PROMPT (returns raw JSON text, still wrapped in OcrResult)
    - "khata"   → _KHATA_PROMPT
    - "generic" / "auto" / anything else → _OCR_PROMPT

    Never returns empty silently — ok=False with a reason on every failure.
    """
    if doc_kind == "challan":
        prompt = _CHALLAN_PROMPT
        resolved_kind = "challan"
    elif doc_kind == "khata":
        prompt = _KHATA_PROMPT
        resolved_kind = "khata"
    else:
        prompt = _OCR_PROMPT
        resolved_kind = "generic"

    if hint and hint.strip():
        prompt = f"{prompt}\n\nExtra context / hint: {hint.strip()}"

    try:
        raw = llm.vision_extract(image, prompt).strip()
    except Exception:
        return OcrResult("", ok=False, doc_kind=resolved_kind, reason="vision_error")

    if not raw:
        return OcrResult("", ok=False, doc_kind=resolved_kind, reason="empty")
    if len(raw) < 3:
        return OcrResult("", ok=False, doc_kind=resolved_kind, reason="too_short")

    return OcrResult(raw, ok=True, doc_kind=resolved_kind)


def describe_for_agent(
    image: Any,
    hint: str | None = None,
    doc_kind: str = "auto",
) -> DescribeResult:
    """OCR the image and wrap it in a Hindi lead-in for the agent's user message.

    On OCR success  → DescribeResult(OCR_HEADER + text, ok=True).
    On OCR failure  → DescribeResult(ocr_retry_message, ok=False, reason=ocr.reason).
    """
    ocr = ocr_image(image, hint=hint, doc_kind=doc_kind)
    if ocr.ok:
        return DescribeResult(OCR_HEADER + ocr.text, ok=True)
    return DescribeResult(
        agent_text=i18n.ocr_retry_message(),
        ok=False,
        reason=ocr.reason,
    )


# ------------------------------------------------------------------ challan parser

def _challan_line(d: dict) -> dict | None:
    """Coerce a raw line-item dict to typed fields; return None to drop the line."""
    name = str(d.get("name", "")).strip()
    if not name:
        return None  # drop lines with no name

    qty_raw = d.get("qty", 1)
    try:
        qty = int(float(qty_raw)) if qty_raw not in (None, "") else 1
    except (TypeError, ValueError):
        qty = 1

    unit_raw = d.get("unit")
    unit = str(unit_raw).strip() if unit_raw not in (None, "") else ""

    rate_raw = d.get("rate")
    try:
        rate: float | None = float(rate_raw) if rate_raw not in (None, "") else None
    except (TypeError, ValueError):
        rate = None

    mrp_raw = d.get("mrp")
    try:
        mrp: float | None = float(mrp_raw) if mrp_raw not in (None, "") else None
    except (TypeError, ValueError):
        mrp = None

    hsn_raw = d.get("hsn")
    hsn: str | None = str(hsn_raw).strip() if hsn_raw not in (None, "") else None

    line: dict = {"name": name, "qty": qty}
    if unit:
        line["unit"] = unit
    if rate is not None:
        line["rate"] = rate
    if mrp is not None:
        line["mrp"] = mrp
    if hsn:
        line["hsn"] = hsn
    return line


def parse_challan(image: Any, supplier_hint: str | None = None) -> dict:
    """Extract structured line items from a supplier bill / challan photo.

    Returns::

        {
            "ok": bool,
            "supplier": str | None,
            "date": str | None,
            "lines": [...],     # list of coerced line dicts
            "raw": str,         # raw model output
            "error": str | None # "reupload" when ok=False
        }
    """
    prompt = _CHALLAN_PROMPT
    if supplier_hint and supplier_hint.strip():
        prompt = f"{prompt}\n\nExpected supplier name hint: {supplier_hint.strip()}"

    try:
        raw = llm.vision_extract(image, prompt, max_tokens=2048,
                                 response_format={"type": "json_object"}).strip()
    except Exception:
        return {"ok": False, "supplier": None, "date": None,
                "lines": [], "raw": "", "error": "reupload"}

    if not raw:
        return {"ok": False, "supplier": None, "date": None,
                "lines": [], "raw": raw, "error": "reupload"}

    # Try to parse as object with "items" array first, then fall back to bare array.
    obj = _extract_json_obj(raw)
    if obj and "items" in obj and isinstance(obj["items"], list):
        raw_lines = obj["items"]
        supplier_val = obj.get("supplier") or None
        date_val = obj.get("date") or None
    else:
        # Maybe the model returned a bare JSON array
        raw_lines = _extract_json_array(raw)
        supplier_val = None
        date_val = None

    lines = [l for d in raw_lines if (l := _challan_line(d)) is not None]

    if not lines:
        return {"ok": False, "supplier": supplier_val, "date": date_val,
                "lines": [], "raw": raw, "error": "reupload"}

    return {
        "ok": True,
        "supplier": supplier_val,
        "date": date_val,
        "lines": lines,
        "raw": raw,
        "error": None,
    }


# ------------------------------------------------------------------ khata parser

def parse_khata(image: Any) -> dict:
    """Extract structured customer-credit entries from a handwritten ledger photo.

    Returns::

        {
            "ok": bool,
            "customers": [...],  # as returned by the model (list of dicts)
            "raw": str,
            "error": str | None  # "reupload" when ok=False
        }
    """
    try:
        raw = llm.vision_extract(image, _KHATA_PROMPT, max_tokens=2048,
                                 response_format={"type": "json_object"}).strip()
    except Exception:
        return {"ok": False, "customers": [], "raw": "", "error": "reupload"}

    if not raw:
        return {"ok": False, "customers": [], "raw": raw, "error": "reupload"}

    obj = _extract_json_obj(raw)
    customers = obj.get("customers") if isinstance(obj, dict) else None
    if not isinstance(customers, list) or len(customers) == 0:
        return {"ok": False, "customers": [], "raw": raw, "error": "reupload"}

    return {"ok": True, "customers": customers, "raw": raw, "error": None}


# ------------------------------------------------------------------ text normalise

def normalize_text(text: str) -> str:
    """Near-passthrough cleanup: trim + collapse internal whitespace. No translation."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
