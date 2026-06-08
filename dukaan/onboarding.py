"""First-run onboarding for the *real* shop owner — a backend finite-state machine.

Dukaan Saathi ships with a rich synthetic seed so the demo is lively from the
first second. When a real owner takes over, they don't want to type their whole
shop into a form: they speak a rough stock list, snap a photo of a supplier
bill, snap their handwritten khata (bahi), eyeball a VERIFY-back view, and tap
confirm. Only *then* do we wipe the synthetic seed and replace it with their data.

This module is that flow as a pure backend FSM — no Gradio, no widgets. Every
public call returns a JSON-able **step-view** dict the UI layer can render, and
the whole session (collected drafts + current state) is persisted as a single
JSON blob in ``txn.app_meta`` (via :func:`dukaan.db.meta_get` / ``meta_set``) so
it survives a process restart and resumes exactly where it left off.

Crucial invariant: **nothing touches inventory / customers / ledger until
:func:`confirm_commit`.** Capture steps only append rows to the in-memory (well,
meta-persisted) draft lists. The single write path is :func:`_commit`, which
clears the demo data and replays the drafts through :mod:`dukaan.ops`.

No model is loaded and no network is hit at import time — STT / vision / LLM are
only touched inside the capture calls that need them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from enum import Enum
from typing import Any

from dukaan import db, i18n, llm, normalize, ops, stt

# ============================================================================ state

_META_KEY = "onboarding_session"


class OnboardingState(str, Enum):
    """Lifecycle of an onboarding session (str-valued so it JSON-serialises raw)."""

    PROFILE = "profile"
    ROUGH_INVENTORY = "rough_inventory"
    KHATA = "khata"
    VERIFY = "verify"
    COMMITTING = "committing"
    DONE = "done"
    ABORTED = "aborted"


# Terminal states: a session in either is "not active" and never resumed.
_TERMINAL = {OnboardingState.DONE.value, OnboardingState.ABORTED.value}

# Coarse step index per state (for a UI progress bar / stepper).
_STEP_INDEX = {
    OnboardingState.PROFILE.value: 0,
    OnboardingState.ROUGH_INVENTORY.value: 1,
    OnboardingState.KHATA.value: 2,
    OnboardingState.VERIFY.value: 3,
    OnboardingState.COMMITTING.value: 3,
    OnboardingState.DONE.value: 4,
    OnboardingState.ABORTED.value: 4,
}

# ---------------------------------------------------------------------- prompts

# Cheap text->JSON prompt for a rough spoken/typed stock utterance.
_ROUGH_ITEMS_PROMPT = (
    "You are helping a small Indian kirana (general store) owner do a rough first "
    "stock-take. The owner said the following (Hindi / Hinglish / English):\n"
    "---\n{utterance}\n---\n"
    "Convert it into a JSON array of stock items. Each element MUST be an object "
    'with exactly these keys: {{"name": "<item name>", "qty": <integer>, '
    '"category": "<short category or empty string>"}}.\n'
    "Rules:\n"
    "- qty MUST be an integer (default 1 if no count is stated).\n"
    "- Keep the owner's product wording (e.g. 'Parle-G', 'Tata Salt 1kg').\n"
    "- Do NOT invent items that were not mentioned.\n"
    "- Output ONLY the JSON array, no preamble, no markdown."
)

# Vision prompt for a rough stock photo (a shelf / a scribbled list).
_INVENTORY_PROMPT = (
    "extract a rough stock list from this photo as JSON array [{name,qty}]"
)


# ============================================================================ meta

def _get_meta() -> dict | None:
    """Return the persisted session blob (or ``None`` when no session exists)."""
    sess = db.meta_get(_META_KEY, None)
    return sess if isinstance(sess, dict) else None


def _set_meta(session: dict) -> None:
    """Persist the session blob to ``txn.app_meta``."""
    db.meta_set(_META_KEY, session)


def _clear_meta() -> None:
    """Forget the persisted session (set the blob to null)."""
    db.meta_set(_META_KEY, None)


def _new_session() -> dict:
    """A fresh session at PROFILE with empty drafts and zeroed id counters."""
    return {
        "state": OnboardingState.PROFILE.value,
        "profile": {"owner_name": "", "shop_name": "", "language": ""},
        "items": [],
        "customers": [],
        "counters": {"it": 0, "cu": 0},
    }


def _next_id(session: dict, kind: str) -> str:
    """Mint the next stable draft id (``it-N`` / ``cu-N``) and bump the counter."""
    counters = session.setdefault("counters", {"it": 0, "cu": 0})
    counters[kind] = int(counters.get(kind, 0)) + 1
    prefix = "it" if kind == "it" else "cu"
    return f"{prefix}-{counters[kind]}"


# ============================================================================ views

def _step_index(state: str) -> int:
    return _STEP_INDEX.get(state, 0)


def _view(session: dict, *, ok: bool = True, needs: str | None = None,
          prompt: str | None = None, message: str | None = None) -> dict:
    """Build a standard step-view dict from the live session.

    Always exposes the current state, a coarse step index, and the live draft
    lists so the UI can re-render after every call. ``needs`` / ``prompt`` carry
    a retry signal (e.g. STT/OCR failed) without ever blanking the draft view.
    """
    view: dict[str, Any] = {
        "ok": ok,
        "state": session.get("state"),
        "step_index": _step_index(session.get("state", "")),
        "drafts": {
            "items": list(session.get("items", [])),
            "customers": list(session.get("customers", [])),
        },
        "needs": needs,
        "prompt": prompt,
    }
    if message is not None:
        view["message"] = message
    return view


def _totals(session: dict) -> dict:
    """Aggregate counts/sums shown on the VERIFY screen."""
    items = session.get("items", [])
    customers = session.get("customers", [])
    total_units = 0
    for it in items:
        try:
            total_units += int(it.get("qty") or 0)
        except (TypeError, ValueError):
            pass
    opening_total = 0.0
    for cu in customers:
        try:
            opening_total += float(cu.get("opening_balance") or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "item_count": len(items),
        "total_units": total_units,
        "customer_count": len(customers),
        "opening_balance_total": round(opening_total, 2),
    }


def _verify_view(session: dict) -> dict:
    """The VERIFY-back screen: full draft lists + roll-up totals."""
    return {
        "ok": True,
        "state": OnboardingState.VERIFY.value,
        "step_index": _step_index(OnboardingState.VERIFY.value),
        "items": list(session.get("items", [])),
        "customers": list(session.get("customers", [])),
        "totals": _totals(session),
    }


# ============================================================================ parsing

def _extract_json(raw: str, default: Any) -> Any:
    """Salvage the first JSON ``[..]``/``{..}`` from messy small-model output.

    Strips ```json fences, then returns the first balanced array/object whose
    type matches ``default`` (list vs dict). Returns ``default`` on any failure.
    """
    if not raw:
        return default
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    want_list = isinstance(default, list)
    open_ch, close_ch = ("[", "]") if want_list else ("{", "}")
    start = cleaned.find(open_ch)
    if start == -1:
        return default
    depth = 0
    end = -1
    for i, ch in enumerate(cleaned[start:], start):
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return default
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return default
    if want_list:
        return parsed if isinstance(parsed, list) else default
    return parsed if isinstance(parsed, dict) else default


def _coerce_qty(value: Any, fallback: int = 1) -> int:
    """Best-effort positive-ish integer from model/manual qty input."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _regex_rough_items(text: str) -> list[dict]:
    """Tiny offline fallback: pull ``<qty> <name>`` (or ``<name> <qty>``) pairs.

    Used only when the LLM call fails entirely — keeps onboarding moving without
    a model. Splits on commas / "aur" / "and" / newlines, then looks for a number
    next to a name in each chunk.
    """
    items: list[dict] = []
    chunks = re.split(r"[,\n]|\baur\b|\band\b", text, flags=re.IGNORECASE)
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        # leading qty: "24 Parle-G"
        m = re.match(r"^(\d+)\s+(.*)$", c)
        if not m:
            # trailing qty: "Parle-G 24"
            m2 = re.match(r"^(.*?)\s+(\d+)$", c)
            if m2:
                name, qty = m2.group(1).strip(), _coerce_qty(m2.group(2))
            else:
                name, qty = c, 1
        else:
            qty, name = _coerce_qty(m.group(1)), m.group(2).strip()
        if name:
            items.append({"name": name, "qty": qty, "category": ""})
    return items


def _parse_rough_items(text: str) -> list[dict]:
    """Convert a rough Hindi/Hinglish stock utterance into ``[{name,qty,category}]``.

    One cheap LLM call (salvaged via :func:`_extract_json`); on any LLM failure
    or empty/garbled result, fall back to a small regex parser so the owner is
    never stuck. Each returned dict has string ``name``, int ``qty``, str
    ``category``.
    """
    text = (text or "").strip()
    if not text:
        return []
    raw_items: list = []
    try:
        raw = llm.complete(_ROUGH_ITEMS_PROMPT.format(utterance=text))
        raw_items = _extract_json(raw, [])
    except Exception:
        raw_items = []
    if not raw_items:
        raw_items = _regex_rough_items(text)

    out: list[dict] = []
    for d in raw_items:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name", "")).strip()
        if not name:
            continue
        out.append({
            "name": name,
            "qty": _coerce_qty(d.get("qty", 1)),
            "category": str(d.get("category", "") or "").strip(),
        })
    return out


# ============================================================================ row builders

def _append_item(session: dict, *, name: str, qty: Any, category: str = "",
                 mrp: float = 0.0, est_expiry: str | None = None,
                 confidence: str = "high", source: str = "manual") -> dict:
    """Build an ItemRow, append it to the draft list, and return it."""
    row = {
        "id": _next_id(session, "it"),
        "name": str(name).strip(),
        "qty": _coerce_qty(qty),
        "category": str(category or "").strip(),
        "mrp": float(mrp or 0.0),
        "est_expiry": est_expiry,
        "confidence": confidence,
        "source": source,
    }
    session.setdefault("items", []).append(row)
    return row


def _append_customer(session: dict, *, name: str, phone: str = "",
                     opening_balance: float = 0.0, debits: list | None = None,
                     confidence: str = "high", source: str = "photo") -> dict:
    """Build a CustomerRow, append it to the draft list, and return it."""
    row = {
        "id": _next_id(session, "cu"),
        "name": str(name).strip(),
        "phone": str(phone or "").strip(),
        "opening_balance": float(opening_balance or 0.0),
        "debits": list(debits or []),
        "confidence": confidence,
        "source": source,
    }
    session.setdefault("customers", []).append(row)
    return row


def _norm_debits(raw: Any) -> list[dict]:
    """Coerce parsed khata debits into ``[{amount,items,date}]`` (skip junk)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for d in raw:
        if not isinstance(d, dict):
            continue
        amt_raw = d.get("amount")
        try:
            amount = float(amt_raw)
        except (TypeError, ValueError):
            continue
        out.append({
            "amount": amount,
            "items": (str(d["items"]).strip() if d.get("items") not in (None, "") else None),
            "date": (str(d["date"]).strip() if d.get("date") not in (None, "") else None),
        })
    return out


# ============================================================================ public API

def start_onboarding(resume: bool = True) -> dict:
    """Begin (or resume) onboarding; return the current step-view.

    With ``resume=True`` and an existing persisted session, that session is
    returned untouched so a reload picks up mid-flow. Otherwise a fresh session
    is created at PROFILE and persisted before the view is returned.
    """
    if resume:
        existing = _get_meta()
        if existing is not None:
            return _view(existing)
    session = _new_session()
    _set_meta(session)
    return _view(session)


def get_session() -> dict | None:
    """Return the raw persisted session blob (or ``None`` if none exists)."""
    return _get_meta()


def is_onboarding_active() -> bool:
    """True iff we're still on demo data AND a non-terminal session exists.

    This is the gate the app uses to decide whether to show the onboarding flow
    instead of the normal dashboard.
    """
    if db.data_mode() != "demo":
        return False
    session = _get_meta()
    return bool(session) and session.get("state") not in _TERMINAL


def set_profile(owner_name: str, shop_name: str, language: str) -> dict:
    """Store the owner/shop profile and advance PROFILE -> ROUGH_INVENTORY.

    Both ``owner_name`` and ``shop_name`` must be non-empty; ``language`` is
    free-form (ISO code or label) and may be blank.
    """
    session = _get_meta() or _new_session()
    owner = (owner_name or "").strip()
    shop = (shop_name or "").strip()
    if not owner or not shop:
        return _view(
            session, ok=False,
            message="Owner aur shop ka naam dono zaroori hai. (Owner & shop name required.)",
        )
    session["profile"] = {
        "owner_name": owner,
        "shop_name": shop,
        "language": (language or "").strip(),
    }
    session["state"] = OnboardingState.ROUGH_INVENTORY.value
    _set_meta(session)
    return _view(session)


def capture_inventory_voice(audio, language: str | None = None) -> dict:
    """Transcribe a rough spoken stock list and append the parsed items.

    On STT failure returns ``needs="repeat"`` with a non-empty bilingual prompt
    (never blanks the prompt). On success, each parsed item becomes an ItemRow
    with ``source="voice"`` and ``confidence="low"`` (voice stock-takes are rough).
    """
    session = _get_meta() or _new_session()
    r = stt.transcribe(audio, language)
    if not r.ok:
        return _view(
            session, ok=False, needs="repeat",
            prompt=i18n.stt_retry_message(r.language, r.reason),
        )
    for it in _parse_rough_items(r.text):
        _append_item(session, name=it["name"], qty=it["qty"], category=it["category"],
                     confidence="low", source="voice")
    _set_meta(session)
    return _view(session)


def capture_inventory_photo(image) -> dict:
    """Extract a rough stock list from a photo and append the parsed items.

    On an empty/garbled extraction returns ``needs="reupload"`` with a bilingual
    re-upload prompt. On success, each item becomes an ItemRow with
    ``source="photo"`` and ``confidence="low"``.
    """
    session = _get_meta() or _new_session()
    try:
        raw = llm.vision_extract(image, _INVENTORY_PROMPT)
    except Exception:
        raw = ""
    data = _extract_json(raw, [])
    rows = [d for d in data if isinstance(d, dict) and str(d.get("name", "")).strip()]
    if not rows:
        return _view(
            session, ok=False, needs="reupload",
            prompt=i18n.ocr_retry_message(),
        )
    for d in rows:
        _append_item(session, name=d.get("name", ""), qty=d.get("qty", 1),
                     category=str(d.get("category", "") or "").strip(),
                     confidence="low", source="photo")
    _set_meta(session)
    return _view(session)


def add_inventory_item_manual(name: str, qty, category: str = "",
                              mrp: float = 0.0) -> dict:
    """Append one manually-entered ItemRow (``source="manual"``, high confidence)."""
    session = _get_meta() or _new_session()
    _append_item(session, name=name, qty=qty, category=category, mrp=mrp,
                 confidence="high", source="manual")
    _set_meta(session)
    return _view(session)


def capture_khata_photo(image) -> dict:
    """Parse a handwritten khata photo and append a CustomerRow per customer.

    On parse failure returns ``needs="reupload"`` with a bilingual prompt. On
    success, each parsed customer becomes a CustomerRow with ``source="photo"``;
    their opening balance and any dated debits are carried through verbatim for
    the owner to verify before commit.
    """
    session = _get_meta() or _new_session()
    r = normalize.parse_khata(image)
    if not r.get("ok"):
        return _view(
            session, ok=False, needs="reupload",
            prompt=i18n.ocr_retry_message(),
        )
    for c in r.get("customers", []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        try:
            opening = float(c.get("opening_balance") or 0.0)
        except (TypeError, ValueError):
            opening = 0.0
        _append_customer(
            session, name=name, phone=str(c.get("phone") or "").strip(),
            opening_balance=opening, debits=_norm_debits(c.get("debits")),
            confidence="low", source="photo",
        )
    _set_meta(session)
    return _view(session)


def edit_draft_row(kind: str, row_id: str, patch: dict) -> dict:
    """Patch fields on the draft row with id ``row_id``; return a refreshed view.

    ``kind`` is ``"item"`` or ``"customer"``. Only known row fields are written;
    numeric fields (qty/mrp/opening_balance) are coerced. Unknown id -> ``ok:False``.
    If the session is on VERIFY, the refreshed VERIFY view (with recomputed
    totals) is returned; otherwise a normal step-view.
    """
    session = _get_meta()
    if session is None:
        return {"ok": False, "state": None, "step_index": 0,
                "drafts": {"items": [], "customers": []}, "needs": None,
                "prompt": None, "message": "Koi onboarding session nahi mila."}

    bucket = "items" if kind == "item" else "customers"
    row = next((r for r in session.get(bucket, []) if r.get("id") == row_id), None)
    if row is None:
        return _refresh_view(session, ok=False,
                             message=f"Row '{row_id}' nahi mila.")

    for key, value in (patch or {}).items():
        if key not in row or key == "id":
            continue
        if key in ("qty",):
            row[key] = _coerce_qty(value, row.get("qty", 0))
        elif key in ("mrp", "opening_balance"):
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                continue
        elif key == "debits":
            row[key] = _norm_debits(value)
        else:
            row[key] = value
    _set_meta(session)
    return _refresh_view(session)


def delete_draft_row(kind: str, row_id: str) -> dict:
    """Remove the draft row with id ``row_id``; return a refreshed view."""
    session = _get_meta()
    if session is None:
        return {"ok": False, "state": None, "step_index": 0,
                "drafts": {"items": [], "customers": []}, "needs": None,
                "prompt": None, "message": "Koi onboarding session nahi mila."}
    bucket = "items" if kind == "item" else "customers"
    before = session.get(bucket, [])
    after = [r for r in before if r.get("id") != row_id]
    session[bucket] = after
    _set_meta(session)
    if len(after) == len(before):
        return _refresh_view(session, ok=False, message=f"Row '{row_id}' nahi mila.")
    return _refresh_view(session)


def _refresh_view(session: dict, *, ok: bool = True,
                  message: str | None = None) -> dict:
    """Return the VERIFY view when on VERIFY, else a normal step-view.

    Edits/deletes happen both during capture and on the VERIFY screen, so the
    caller gets back whichever shape matches the current state.
    """
    if session.get("state") == OnboardingState.VERIFY.value:
        view = _verify_view(session)
        view["ok"] = ok
        if message is not None:
            view["message"] = message
        return view
    return _view(session, ok=ok, message=message)


def advance_to_verify() -> dict:
    """Move ROUGH_INVENTORY / KHATA -> VERIFY and return the VERIFY view."""
    session = _get_meta() or _new_session()
    session["state"] = OnboardingState.VERIFY.value
    _set_meta(session)
    return _verify_view(session)


def get_verify_view() -> dict:
    """Return the VERIFY view without changing state (for a re-render/reload)."""
    session = _get_meta() or _new_session()
    return _verify_view(session)


def confirm_commit() -> dict:
    """Replace the synthetic seed with the owner's drafts (requires VERIFY).

    Flips the session to COMMITTING, runs :func:`_commit` (clear demo data ->
    replay drafts -> set data-mode "real"), and on success flips to DONE and
    forgets the session blob. On error the session is rolled back to VERIFY so
    the owner can fix a row and retry. Returns
    ``{ok, message_hi, summary}`` on success, ``{ok:False, error, ...}`` on failure.
    """
    session = _get_meta()
    if session is None:
        return {"ok": False, "error": "no_session",
                "message_hi": "Koi onboarding session nahi mila."}
    if session.get("state") != OnboardingState.VERIFY.value:
        return {"ok": False, "error": "not_in_verify", "state": session.get("state"),
                "message_hi": "Pehle saari jaankari verify karein."}

    session["state"] = OnboardingState.COMMITTING.value
    _set_meta(session)

    report = _commit(session)
    if not report.get("ok"):
        # Roll back to VERIFY so the owner can correct and retry.
        session["state"] = OnboardingState.VERIFY.value
        _set_meta(session)
        view = _verify_view(session)
        view["ok"] = False
        view["error"] = report.get("error", "commit_failed")
        view["report"] = report
        return view

    item_n = report["committed"]["items"]
    cust_n = report["committed"]["customers"]
    session["state"] = OnboardingState.DONE.value
    _set_meta(session)
    _clear_meta()  # consumed: forget the draft blob

    msg = (f"Ho gaya! {item_n} item aur {cust_n} customer save kar diye. "
           f"Ab aapka asli data dikhega. "
           f"(Done — {item_n} items and {cust_n} customers saved.)")
    return {"ok": True, "message_hi": msg,
            "summary": {"items": item_n, "customers": cust_n}}


def abort_onboarding(keep_demo: bool = True) -> dict:
    """Abandon onboarding without writing any business data.

    Marks the session ABORTED and forgets the blob. ``keep_demo`` is honoured by
    leaving the data-mode untouched (still "demo") — we never write business rows
    here regardless, so the synthetic seed is simply kept.
    """
    session = _get_meta()
    if session is not None:
        session["state"] = OnboardingState.ABORTED.value
        _set_meta(session)
    _clear_meta()
    return {"ok": True, "state": OnboardingState.ABORTED.value, "kept_demo": bool(keep_demo),
            "message_hi": "Onboarding band kar diya. (Onboarding cancelled.)"}


# ============================================================================ commit

def _commit(session: dict) -> dict:
    """Clear the demo seed and replay the drafts through :mod:`dukaan.ops`.

    Order is deliberate and best-effort (the two SQLite databases are written
    independently, so this is **not** atomic — documented here): wipe business
    rows (keep suppliers), write all items, then all customers, and finally flip
    the data-mode to "real". Per-row failures are collected into ``errors`` and
    do not abort the rest; the flip to "real" runs in a ``finally`` so a partial
    migration still leaves the owner on their own (non-demo) data.

    Returns ``{ok, committed:{items,customers}, errors:[...], error?}``.
    """
    errors: list[dict] = []
    items_done = 0
    customers_done = 0

    db.clear_business_data(keep_suppliers=True)

    try:
        # ---- inventory ----
        for row in session.get("items", []):
            try:
                if row.get("est_expiry"):
                    exp = row["est_expiry"]
                else:
                    exp, _ = ops.estimate_expiry(row.get("name"), row.get("category"))
                ops.add_inventory(
                    row.get("name", ""),
                    int(_coerce_qty(row.get("qty", 0), 0)),
                    mrp=(row.get("mrp") or None),
                    category=(row.get("category") or None),
                    expiry_date=(row.get("est_expiry") or exp),
                )
                items_done += 1
            except Exception as e:  # noqa: BLE001 — best-effort per row
                errors.append({"kind": "item", "id": row.get("id"),
                               "name": row.get("name"), "error": str(e)})

        # ---- customers / khata ----
        for row in session.get("customers", []):
            try:
                name = row.get("name", "")
                ops.find_or_create_customer(name, row.get("phone") or None)
                debits = row.get("debits") or []
                if debits:
                    for d in debits:
                        ops.add_udhaar(name, float(d["amount"]),
                                       items=d.get("items"), due_date=d.get("date"))
                elif float(row.get("opening_balance") or 0.0) > 0:
                    ops.add_udhaar(name, float(row["opening_balance"]),
                                   items="Opening balance (khata migration)",
                                   due_date=None)
                customers_done += 1
            except Exception as e:  # noqa: BLE001 — best-effort per row
                errors.append({"kind": "customer", "id": row.get("id"),
                               "name": row.get("name"), "error": str(e)})
    except Exception as e:  # noqa: BLE001 — unexpected; report but still flip mode
        errors.append({"kind": "fatal", "error": str(e)})
    finally:
        # Two-DB writes are non-atomic; we still move off demo data so the owner
        # isn't left staring at the synthetic seed after a partial migration.
        db.set_data_mode("real")

    return {
        "ok": not errors,
        "committed": {"items": items_done, "customers": customers_done},
        "errors": errors,
        **({"error": "commit_partial"} if errors else {}),
    }
