"""Tests for the LangChain tool layer (``dukaan.tools``), two-DB edition.

Tools are exercised through the public ``.invoke({...})`` entry point (covering
arg coercion). No LLM involved — tools wrap the deterministic ``ops`` / ``db`` layer.
"""

from __future__ import annotations

from dukaan import db, ops, tools


def test_every_tool_has_name_and_description():
    assert tools.TOOLS, "TOOLS registry should not be empty"
    for t in tools.TOOLS:
        assert getattr(t, "name", None), f"tool missing name: {t!r}"
        assert getattr(t, "description", None), f"tool {t.name!r} missing description"


def test_query_database_returns_item_name(seeded_db):
    name = db.qx("SELECT name FROM inv.inventory ORDER BY item_id LIMIT 1")[0]["name"]
    out = tools.query_database.invoke({"sql": "SELECT name FROM inv.inventory ORDER BY item_id LIMIT 5"})
    assert isinstance(out, str)
    assert name in out


def test_query_database_blocks_delete_without_raising(seeded_db):
    before = db.qx("SELECT COUNT(*) AS n FROM txn.sales")[0]["n"]
    out = tools.query_database.invoke({"sql": "DELETE FROM sales"})
    assert isinstance(out, str)
    assert any(w in out.lower() for w in ("error", "blocked", "forbidden", "allowed", "read")), out
    assert db.qx("SELECT COUNT(*) AS n FROM txn.sales")[0]["n"] == before


def test_record_sale_tool_stages_then_confirms(seeded_db):
    """Confirm-before-write (R1): the tool STAGES the sale (no write yet); a
    follow-up 'haan' via confirm_pending_tool commits it."""
    from dukaan import staging
    staging.clear_pending(staging.current_thread())
    item = db.qx("SELECT * FROM inv.inventory WHERE qty >= 5 ORDER BY item_id LIMIT 1")[0]
    out = tools.record_sale_tool.invoke({"item_name": item["name"], "qty": 2})
    assert isinstance(out, str) and ("haan" in out.lower() or "nahi" in out.lower())
    assert ops.find_item(item["name"])["qty"] == item["qty"]  # staged, not written
    tools.confirm_pending_tool.invoke({"decision": "haan"})
    assert ops.find_item(item["name"])["qty"] == item["qty"] - 2  # committed
    staging.clear_pending(staging.current_thread())
