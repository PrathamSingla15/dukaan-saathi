"""Tests for challan auto-receive (stage_receive / commit_receive).

Pass lines directly — NO vision/GPU/server needed.
"""

from __future__ import annotations

import dukaan.normalize as normalize_mod
from dukaan import db, ops
from dukaan import receiving


# ------------------------------------------------------------------ helpers

def _pick_existing_item(min_qty: int = 6) -> dict:
    """Return the first seeded inventory row with qty >= min_qty."""
    rows = db.qx(
        "SELECT * FROM inv.inventory WHERE qty >= ? ORDER BY item_id LIMIT 1",
        (min_qty,),
    )
    assert rows, "seeded DB should have items with qty >= %d" % min_qty
    return rows[0]


# ------------------------------------------------------------------ 1. stage classifies merge and new

def test_stage_classifies_merge_and_new(seeded_db):
    item = _pick_existing_item()
    existing_name = item["name"]

    st = receiving.stage_receive(
        lines=[
            {"name": existing_name, "qty": 6, "rate": 10.0, "mrp": 15.0},
            {"name": "Newbie Item ZZ", "qty": 3, "rate": 5.0},
        ]
    )

    assert st["ok"] is True

    staged_items = st["items"]
    assert len(staged_items) == 2

    merge_line = staged_items[0]
    assert merge_line["action"] == "merge", (
        f"Expected 'merge' for existing item '{existing_name}', got '{merge_line['action']}'"
    )
    assert merge_line["item_id"] is not None, "merge line must carry item_id"

    new_line = staged_items[1]
    assert new_line["action"] == "new", (
        f"Expected 'new' for 'Newbie Item ZZ', got '{new_line['action']}'"
    )
    assert new_line["item_id"] is None, "new line must have item_id=None before commit"

    expected_total = 6 * 10.0 + 3 * 5.0
    assert st["total_cost"] == expected_total, (
        f"total_cost expected {expected_total}, got {st['total_cost']}"
    )


# ------------------------------------------------------------------ 2. stage is read-only

def test_stage_is_read_only(seeded_db):
    item = _pick_existing_item()
    original_qty = int(item["qty"])
    existing_name = item["name"]

    receiving.stage_receive(
        lines=[
            {"name": existing_name, "qty": 6, "rate": 10.0, "mrp": 15.0},
            {"name": "Newbie Item ZZ", "qty": 3, "rate": 5.0},
        ]
    )

    after = ops.find_item(existing_name)
    assert after is not None
    assert int(after["qty"]) == original_qty, (
        f"stage_receive must not write: qty was {original_qty}, now {after['qty']}"
    )


# ------------------------------------------------------------------ 3. commit writes via lots

def test_commit_writes_via_lots(seeded_db):
    item = _pick_existing_item()
    original_qty = int(item["qty"])
    existing_name = item["name"]

    st = receiving.stage_receive(
        lines=[
            {"name": existing_name, "qty": 6, "rate": 10.0, "mrp": 15.0},
            {"name": "Newbie Item ZZ", "qty": 3, "rate": 5.0},
        ]
    )
    assert st["ok"] is True

    r = receiving.commit_receive(st["items"])

    assert r["ok"] is True, f"commit_receive failed: {r}"
    assert len(r["received"]) == 2, (
        f"expected 2 received, got {len(r['received'])}: {r}"
    )

    # existing item qty must have grown by 6
    updated = ops.find_item(existing_name)
    assert updated is not None
    assert int(updated["qty"]) == original_qty + 6, (
        f"Expected qty {original_qty + 6}, got {updated['qty']}"
    )

    # new item must now exist
    new_item = ops.find_item("Newbie Item ZZ")
    assert new_item is not None, "'Newbie Item ZZ' should exist after commit"

    # new item must have at least one open lot
    new_item_id = new_item["item_id"]
    lots = ops.lots_for_item(new_item_id)
    assert len(lots) > 0, "Newbie Item ZZ must have at least one lot after commit"


# ------------------------------------------------------------------ 4. bad image triggers reupload

def test_stage_reupload_on_bad_image(seeded_db, monkeypatch):
    """When parse_challan signals failure, stage_receive must return ok=False,
    error=='reupload', and a non-empty message — without touching the DB."""

    monkeypatch.setattr(
        normalize_mod,
        "parse_challan",
        lambda image, supplier_hint=None: {"ok": False, "error": "reupload", "lines": []},
    )

    result = receiving.stage_receive(image=object())

    assert result["ok"] is False, "should be False on bad image"
    assert result["error"] == "reupload"
    assert result.get("message"), "message must be non-empty for the owner to act on"
