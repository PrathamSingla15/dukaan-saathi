"""Regression + analytics tests for the business layer (``ops``), two-DB edition.

Data-agnostic: the seed is research-generated, so tests pick sample items /
customers dynamically rather than hard-coding names. They pin behaviour that is
easy to break: new-item-vs-restock (must not fuzzy-match on a size token), sale
stock decrement / oversell warning, udhaar balance math, and the analytics.
"""

from __future__ import annotations

from dukaan import db, ops


def _an_item(min_qty: int = 6) -> dict:
    return db.qx("SELECT * FROM inv.inventory WHERE qty >= ? ORDER BY item_id LIMIT 1", (min_qty,))[0]


def _a_customer() -> str:
    return db.qx("SELECT name FROM txn.customers ORDER BY customer_id LIMIT 1")[0]["name"]


# ------------------------------------------------------ add_inventory: new vs restock
def test_add_inventory_new_item_not_matched_by_size_token(seeded_db):
    """REGRESSION: a brand-new item whose only overlap with existing items is a
    size/unit token ('500ml', '1kg', 'pack') must NOT fuzzy-match — it is new."""
    res = ops.add_inventory("Qwxyz9 Testbrand 500ml 1kg pack", 24, mrp=40, purchase_price=30)
    assert res["ok"] is True
    assert res["action"] == "new_item"
    assert ops.find_item("Qwxyz9 Testbrand 500ml 1kg pack") is not None


def test_add_inventory_restock_increments_existing(seeded_db):
    item = _an_item()
    res = ops.add_inventory(item["name"], 50)
    assert res["ok"] is True and res["action"] == "restock"
    assert res["new_qty"] == item["qty"] + 50
    assert ops.find_item(item["name"])["qty"] == item["qty"] + 50


# ----------------------------------------------------------------- record_sale
def test_record_sale_decrements_stock_and_reports_revenue(seeded_db):
    item = _an_item()
    res = ops.record_sale(item["name"], 3)
    assert res["ok"] is True
    assert res["remaining_stock"] == item["qty"] - 3
    assert res["revenue"] == round(3 * item["mrp"], 2)
    assert ops.find_item(item["name"])["qty"] == item["qty"] - 3


def test_record_sale_oversell_blocked(seeded_db):
    """R2: overselling is HARD-BLOCKED — ok False, no write, stock unchanged."""
    item = _an_item()
    res = ops.record_sale(item["name"], item["qty"] + 25)
    assert res["ok"] is False
    assert res["error"] == "insufficient_stock"
    assert res["available"] == item["qty"]
    assert ops.find_item(item["name"])["qty"] == item["qty"]  # untouched


# --------------------------------------------------------- udhaar balance math
def test_add_udhaar_then_payment_balance_math(seeded_db):
    cust = _a_customer()
    before = ops.customer_balance(cust)["balance"]
    res_add = ops.add_udhaar(cust, 100, items="Sabun")
    assert res_add["ok"] is True
    assert res_add["balance"] == round(before + 100, 2)
    res_pay = ops.record_payment(cust, 40)
    assert res_pay["ok"] is True
    assert res_pay["balance"] == round(before + 60, 2)


# -------------------------------------------------------------------- analytics
def test_stock_value_mrp_exceeds_cost(seeded_db):
    sv = ops.stock_value()
    assert sv["at_mrp"] > sv["at_cost"]
    assert sv["potential_margin"] > 0
    assert sv["item_count"] == db.counts()["items"]


def test_expiring_soon_structure(seeded_db):
    """With a wide window the perishables surface; each row carries days_left."""
    soon = ops.expiring_soon(days=400)
    assert len(soon) > 0
    assert all("days_left" in row and "expiry_date" in row for row in soon)


def test_slow_movers_is_list(seeded_db):
    movers = ops.slow_movers()
    assert isinstance(movers, list)
    assert all({"name", "qty"} <= set(r) for r in movers)


def test_pending_udhaar_has_outstanding(seeded_db):
    pending = ops.pending_udhaar()
    assert pending["total"] > 0
    assert pending["count"] >= 1
    assert all("overdue" in r for r in pending["customers"])


def test_sales_reference_real_inventory(seeded_db):
    """Cross-DB integrity: most sold item_names exist in the inventory catalog."""
    bad = db.qx(
        "SELECT COUNT(*) n FROM txn.sales s "
        "WHERE s.item_id IS NOT NULL AND s.item_id NOT IN (SELECT item_id FROM inv.inventory)"
    )[0]["n"]
    assert bad == 0
