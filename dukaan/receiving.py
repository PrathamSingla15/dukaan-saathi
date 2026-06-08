"""Challan-photo → auto-receive backend for Dukaan Saathi (UI-agnostic).

When a shop owner photographs a supplier bill (challan), this module turns it
into an editable list of stock-to-receive and then, only on an explicit commit,
writes that stock into inventory. It is deliberately split into a strict
three-call contract so any front-end (Gradio, WhatsApp, CLI) can drive it:

1. :func:`stage_receive` — READ-ONLY. Parse the photo (or accept already-parsed
   lines on a re-stage after edits), resolve each line against existing
   inventory (merge vs. new), estimate expiry where the challan gives none, and
   return an editable preview plus a Hindi readback. Touches no database rows.
2. *(UI step — not in this module)* the owner fixes any qty / rate / name.
3. :func:`commit_receive` — THE ONLY WRITE. Each edited line is pushed through
   :func:`dukaan.ops.record_purchase`, so FEFO lots, lot-merge, and inventory.qty
   recomputation all apply exactly as for a typed-in restock.

No model is loaded and no network is touched at import time — vision only runs
inside :func:`stage_receive` when an ``image`` is supplied, via
:func:`dukaan.normalize.parse_challan`.
"""

from __future__ import annotations

from typing import Any

from dukaan import i18n, normalize, ops


# ------------------------------------------------------------------ staging (read-only)

def _stage_line(line: dict) -> dict:
    """Resolve one raw challan/UI line into an editable :class:`StagedLine` dict.

    Resolution decides ``action``: a fuzzy match against existing inventory →
    ``"merge"`` (restock that item_id); otherwise ``"new"`` (create on commit).
    Expiry is estimated from the resolved item's category (or none) when the
    challan omits a date, and flagged via ``is_estimated``.
    """
    input_name = str(line.get("name", "")).strip()

    res = ops.resolve_item(input_name)
    matched = res.get("status") == "matched"
    item = res.get("item") if matched else None

    action = "merge" if matched else "new"
    item_id = item["item_id"] if matched else None
    category = item.get("category") if matched else None
    resolved_name = item["name"] if matched else input_name

    est_exp, is_est = ops.estimate_expiry(input_name, category)

    qty_raw = line.get("qty", 1)
    try:
        qty = int(float(qty_raw)) if qty_raw not in (None, "") else 1
    except (TypeError, ValueError):
        qty = 1

    rate_raw = line.get("rate")
    try:
        rate: float | None = float(rate_raw) if rate_raw not in (None, "") else None
    except (TypeError, ValueError):
        rate = None

    mrp_raw = line.get("mrp")
    try:
        mrp: float | None = float(mrp_raw) if mrp_raw not in (None, "") else None
    except (TypeError, ValueError):
        mrp = None

    return {
        "input_name": input_name,
        "resolved_name": resolved_name,
        "item_id": item_id,
        "action": action,
        "qty": qty,
        "unit": line.get("unit") or "",
        "rate": rate,
        "mrp": mrp,
        "hsn": line.get("hsn"),
        "estimated_expiry": est_exp,
        "is_estimated": is_est,
        "candidates": [c["name"] for c in res.get("candidates", [])],
    }


def stage_receive(
    image: Any = None,
    lines: list[dict] | None = None,
    supplier_hint: str | None = None,
) -> dict:
    """READ-ONLY: parse a challan (or accept edited lines) into an editable preview.

    If ``image`` is given, it is OCR'd via :func:`dukaan.normalize.parse_challan`;
    on OCR failure a reupload prompt is returned and nothing is staged. If
    ``lines`` are passed directly (a UI re-stage after the owner edited names /
    qty), those are used as-is and no vision runs. Either way each line is
    resolved (merge vs. new) and expiry-estimated.

    Returns ``{ok, error, supplier, date, total_cost, needs_confirmation, items,
    message}`` where ``items`` is a list of editable StagedLine dicts and
    ``message`` is a Hindi readback. No database rows are touched.
    """
    supplier = supplier_hint
    date = None

    if image is not None:
        r = normalize.parse_challan(image, supplier_hint=supplier_hint)
        if not r["ok"]:
            return {
                "ok": False,
                "error": "reupload",
                "message": i18n.ocr_retry_message(),
                "items": [],
            }
        lines = r["lines"]
        supplier = r.get("supplier") or supplier_hint
        date = r.get("date")

    lines = lines or []
    items = [_stage_line(l) for l in lines]

    total_cost = sum(i["qty"] * i["rate"] for i in items if i["rate"] is not None)

    sup_prefix = f"{supplier} se " if supplier else ""
    message = (
        f"{sup_prefix}{len(items)} cheezein aayi, "
        f"kul ₹{total_cost:.0f} — daal dun?"
    )

    return {
        "ok": True,
        "error": None,
        "supplier": supplier,
        "date": date,
        "total_cost": total_cost,
        "needs_confirmation": True,
        "items": items,
        "message": message,
    }


# ------------------------------------------------------------------ commit (the only write)

def commit_receive(staged_items: list[dict], supplier: str | None = None) -> dict:
    """THE ONLY WRITE: push each (edited) staged line into inventory.

    Each line is dispatched to :func:`dukaan.ops.record_purchase` — the sole DB
    writer for restocks — so FEFO lots, lot-merge, and the inventory.qty
    recompute all apply. A ``"merge"`` line passes its resolved ``item_id`` so it
    restocks the existing SKU; a ``"new"`` line lets ``record_purchase`` create
    the item. Lines whose writer returns falsy ``ok`` are collected into
    ``failed`` rather than aborting the batch.

    Returns ``{ok, received, failed, total_cost, message_hi}``.
    """
    staged_items = staged_items or []
    received: list[dict] = []
    failed: list[dict] = []
    total_cost = 0.0

    for line in staged_items:
        item_name = line.get("resolved_name") or line.get("input_name")
        rate = line.get("rate")
        try:
            qty = int(line.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1

        result = ops.record_purchase(
            item_name=item_name,
            qty=qty,
            purchase_price=rate,
            mrp=line.get("mrp"),
            supplier=supplier or line.get("supplier"),
            expiry_date=line.get("estimated_expiry"),
            resolved_item_id=line.get("item_id") if line.get("action") == "merge" else None,
        )

        if result.get("ok"):
            received.append(result)
            if rate is not None:
                total_cost += qty * float(rate)
        else:
            failed.append(result)

    n = len(received)
    if n and not failed:
        message_hi = f"{n} cheezein stock me daal di, kul ₹{total_cost:.0f}."
    elif n and failed:
        message_hi = (
            f"{n} cheezein stock me daal di (kul ₹{total_cost:.0f}); "
            f"{len(failed)} save nahi hui."
        )
    else:
        message_hi = "Kuch bhi stock me nahi daala gaya."

    return {
        "ok": not failed,
        "received": received,
        "failed": failed,
        "total_cost": total_cost,
        "message_hi": message_hi,
    }
