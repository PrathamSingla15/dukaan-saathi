"""Tests for robust item resolution / dedup (dukaan.resolve + ops.add_inventory).

Covers:
  - Exact-name match returns status=="matched" and score==100.0
  - Upper-case variant of an existing item restocks (no duplicate row)
  - Totally made-up item creates new_item and resolve returns "none"
  - candidates_item returns a list; normalize_item_name parses brand/base
"""

from __future__ import annotations

import pytest

from dukaan import db, ops
import dukaan.resolve as resolve


# ------------------------------------------------------------------ helpers
def _an_item() -> dict:
    """Return a real seeded inventory row with a non-trivial name."""
    rows = db.qx(
        "SELECT * FROM inv.inventory WHERE length(name) > 8 ORDER BY item_id LIMIT 1"
    )
    assert rows, "seeded DB must have at least one item"
    return rows[0]


# ------------------------------------------------------------------ tests
def test_exact_name_matched(seeded_db):
    """An exact seeded name must resolve to 'matched' with score==100.0."""
    item = _an_item()
    result = resolve.resolve_item(item["name"])
    assert result["status"] == "matched", (
        f"Expected 'matched' for '{item['name']}', got '{result['status']}'"
    )
    assert result["score"] == 100.0, (
        f"Expected score 100.0, got {result['score']}"
    )


def test_case_and_variant_merges(seeded_db):
    """Adding an existing item name uppercased must restock (merge), not create a duplicate row."""
    item = _an_item()
    original_name = item["name"]
    original_qty = int(item["qty"])

    # Add 5 units using the uppercased name — should detect as the same item.
    result = ops.add_inventory(original_name.upper(), 5)
    assert result["ok"] is True, f"add_inventory failed: {result}"
    assert result["action"] == "restock", (
        f"Expected 'restock' for upper-case variant of '{original_name}', "
        f"got action='{result['action']}'"
    )

    # The qty should have increased by 5.
    updated = ops.find_item(original_name)
    assert updated is not None, "Item disappeared after restock"
    assert updated["qty"] == original_qty + 5, (
        f"Expected qty={original_qty + 5}, got {updated['qty']}"
    )

    # Still only one row with that name (case-insensitive).
    count_rows = db.qx(
        "SELECT COUNT(*) AS n FROM inv.inventory WHERE lower(name) = lower(?)",
        (original_name,),
    )
    assert count_rows[0]["n"] == 1, (
        f"Expected exactly 1 row for '{original_name}', "
        f"got {count_rows[0]['n']} (duplicate created!)"
    )


def test_new_item_not_matched(seeded_db):
    """A completely invented name must resolve to 'none' against the seeded catalog,
    then create a new_item when added via add_inventory."""
    weird_name = "Qwzz9 Madeup Brand 500ml 1kg pack"

    # FIRST: verify the name has no match in the seeded catalog (spec requirement).
    resolve_before = resolve.resolve_item(weird_name)
    assert resolve_before["status"] == "none", (
        f"Expected 'none' before insertion for '{weird_name}', "
        f"got '{resolve_before['status']}'"
    )

    # THEN: add_inventory should create it as new_item (because resolve returned none).
    result = ops.add_inventory(weird_name, 7, mrp=40)
    assert result["ok"] is True, f"add_inventory failed: {result}"
    assert result["action"] == "new_item", (
        f"Expected 'new_item', got '{result['action']}'"
    )


def test_candidates_is_list(seeded_db):
    """candidates_item('dal', 5) returns a list; normalize_item_name parses brand/base."""
    cands = ops.candidates_item("dal", 5)
    assert isinstance(cands, list), (
        f"Expected list from candidates_item, got {type(cands)}"
    )
    # 'dal' should match several seeded dal items (toor dal, moong dal, masoor dal…)
    # We just require it returns a list (possibly empty on degenerate seeds).
    # Normalise a well-known branded name and check base contains the product keyword.
    parsed = resolve.normalize_item_name("TATA SALT 1KG")
    assert isinstance(parsed, dict), "normalize_item_name must return a dict"
    assert "base" in parsed, "Result must contain 'base' key"
    # The base should contain 'salt' (after the brand 'tata' is extracted and size stripped).
    assert "salt" in parsed["base"].lower(), (
        f"Expected 'salt' in base, got base='{parsed['base']}'"
    )
