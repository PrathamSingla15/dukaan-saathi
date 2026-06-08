"""Tests for the FEFO batch / lots layer in Dukaan Saathi.

Covers: backfill idempotency, FEFO drain order, oversell guard,
lot-merge on same expiry, estimate_expiry, and expiring_soon keys.
"""

from __future__ import annotations

from dukaan import db, ops


# ------------------------------------------------------------------ helpers
def _an_item(min_qty: int = 6) -> dict:
    return db.qx(
        "SELECT * FROM inv.inventory WHERE qty >= ? ORDER BY item_id LIMIT 1",
        (min_qty,),
    )[0]


# -------------------------------------------------------- test 1: backfill
def test_backfill_one_lot_per_item(seeded_db):
    """Every inventory row has >=1 lot; qty == SUM(lot qty_remaining).
    Second call to backfill_lots() returns 0 (idempotent)."""
    # Every item must have at least one lot.
    no_lot = db.qx(
        "SELECT COUNT(*) n FROM inv.inventory i "
        "WHERE NOT EXISTS (SELECT 1 FROM inv.inventory_lots l WHERE l.item_id = i.item_id)"
    )[0]["n"]
    assert no_lot == 0, f"{no_lot} inventory items have no lot at all"

    # inventory.qty must equal the sum of its open lots (zero drift).
    drift = db.qx(
        "SELECT COUNT(*) drift FROM inv.inventory i "
        "WHERE i.qty != COALESCE("
        "  (SELECT SUM(l.qty_remaining) FROM inv.inventory_lots l WHERE l.item_id = i.item_id), "
        "  0)"
    )[0]["drift"]
    assert drift == 0, f"{drift} items have qty != SUM(lots.qty_remaining)"

    # Calling backfill_lots() again must be a no-op.
    n = db.backfill_lots()
    assert n == 0, f"backfill_lots() should return 0 on a fully-seeded db, got {n}"


# -------------------------------------------------- test 2: FEFO drain order
def test_fefo_drains_earliest_first(seeded_db):
    """Two lots with different expiry dates; selling 12 units must drain the
    earlier lot first (10 units), then take 2 from the later lot."""
    # Create two lots for a brand-new item.
    r1 = ops.record_purchase("ZTest FEFO Item", 10, purchase_price=20,
                              expiry_date="2026-07-01")
    assert r1["ok"] is True
    r2 = ops.record_purchase("ZTest FEFO Item", 5, purchase_price=20,
                              expiry_date="2026-09-01")
    assert r2["ok"] is True

    # Sell 12 — should drain 10 from July lot, then 2 from September lot.
    sale = ops.record_sale("ZTest FEFO Item", 12)
    assert sale["ok"] is True
    assert sale["remaining_stock"] == 3

    # sold_from must list the July lot first (took 10) then September lot (took 2).
    sf = sale["sold_from"]
    assert len(sf) == 2
    assert sf[0]["expiry_date"] == "2026-07-01"
    assert sf[0]["took"] == 10
    assert sf[1]["expiry_date"] == "2026-09-01"
    assert sf[1]["took"] == 2

    # Only the September lot should remain open, with qty_remaining == 3.
    item = ops.find_item("ZTest FEFO Item")
    open_lots = ops.lots_for_item(item["item_id"], open_only=True)
    assert len(open_lots) == 1
    assert open_lots[0]["expiry_date"] == "2026-09-01"
    assert open_lots[0]["qty_remaining"] == 3


# ----------------------------------------------- test 3: oversell blocked
def test_oversell_blocked(seeded_db):
    """Selling more than available stock is hard-blocked (ok False); the
    inventory.qty must remain unchanged and available == qty."""
    item = _an_item()
    original_qty = item["qty"]

    res = ops.record_sale(item["name"], original_qty + 50)
    assert res["ok"] is False
    assert res["error"] == "insufficient_stock"
    assert res["available"] == original_qty

    # Stock must be completely untouched.
    assert ops.find_item(item["name"])["qty"] == original_qty


# ------------------------------------------- test 4: same-expiry lot merges
def test_same_expiry_merges(seeded_db):
    """Two purchases of the same item + same expiry_date collapse into exactly
    one lot with qty_remaining equal to their combined quantity."""
    ops.record_purchase("ZMerge Item", 4, expiry_date="2026-12-01")
    ops.record_purchase("ZMerge Item", 4, expiry_date="2026-12-01")

    item = ops.find_item("ZMerge Item")
    assert item is not None

    lots = ops.lots_for_item(item["item_id"])
    assert len(lots) == 1, (
        f"Expected exactly 1 merged lot, got {len(lots)}: {lots}"
    )
    assert lots[0]["qty_remaining"] == 8
    assert lots[0]["expiry_date"] == "2026-12-01"


# ----------------------------------------- test 5: estimate_expiry accuracy
def test_estimate_expiry(seeded_db):
    """A known perishable seed item yields (iso_str, True);
    a non-perishable (Tata Salt 1kg) yields (None, False)."""
    # Pick a seed item that has a real expiry_date (definitely perishable).
    perishable_row = db.qx(
        "SELECT name, category FROM inv.inventory "
        "WHERE expiry_date IS NOT NULL LIMIT 1"
    )
    assert perishable_row, "No perishable items found in seed — check seed_inventory"
    name = perishable_row[0]["name"]
    cat = perishable_row[0]["category"]

    exp, is_est = ops.estimate_expiry(name, cat)
    assert exp is not None, f"estimate_expiry({name!r}, {cat!r}) returned None expiry"
    assert is_est is True, "is_estimated should be True for a perishable"
    # Basic sanity: must be a valid ISO date string (YYYY-MM-DD).
    parts = exp.split("-")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"expiry {exp!r} is not a valid ISO date"
    )

    # Non-perishable: Tata Salt 1kg has shelf_life_days=None in the catalog.
    exp_none, is_est_none = ops.estimate_expiry("Tata Salt 1kg", "Staples")
    assert exp_none is None
    assert is_est_none is False


# --------------------------------------- test 6: expiring_soon key contract
def test_expiring_soon_has_is_estimated(seeded_db):
    """expiring_soon(days=400) returns rows that each contain
    days_left, expiry_date, and is_estimated keys."""
    rows = ops.expiring_soon(days=400)
    assert len(rows) > 0, "No expiring items found — seed may need perishables"

    required = {"days_left", "expiry_date", "is_estimated"}
    for row in rows:
        missing = required - set(row.keys())
        assert not missing, (
            f"Row for {row.get('name')!r} is missing keys: {missing}"
        )
