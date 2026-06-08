"""Tests for the two-database data layer: seed integrity, the SELECT guard, and
cross-DB reads through the attached ``inv`` / ``txn`` surface.

The ``run_select`` / ``is_safe_select`` guard is the only path LLM-generated SQL
takes, so its allow/deny behaviour is security-critical and tested hard.
"""

from __future__ import annotations

import pytest

from dukaan import db


# ----------------------------------------------------------------------- seed
def test_seed_counts(seeded_db):
    """The research-generated seed must land a rich, realistic dataset."""
    c = db.counts()
    assert c["items"] >= 100        # ~130-160 SKUs
    assert c["suppliers"] >= 8
    assert c["customers"] >= 20
    assert c["sales"] >= 500        # a few thousand over ~120 days
    assert c["ledger"] >= 10        # udhaar entries


# --------------------------------------------------------------- is_safe_select
@pytest.mark.parametrize("sql", ["SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x"])
def test_is_safe_select_allows_reads(sql):
    ok, _ = db.is_safe_select(sql)
    assert ok is True


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales(qty) VALUES (1)",
        "UPDATE inventory SET qty = 0",
        "DELETE FROM sales",
        "DROP TABLE sales",
        "ATTACH DATABASE 'x.db' AS y",
        "PRAGMA table_info(sales)",
        "SELECT 1; DROP TABLE sales",  # stacked statement smuggling a write
    ],
)
def test_is_safe_select_blocks_writes(sql):
    ok, reason = db.is_safe_select(sql)
    assert ok is False
    assert isinstance(reason, str) and reason


# ------------------------------------------------------------------ run_select
def test_run_select_inventory(seeded_db):
    res = db.run_select("SELECT name, qty FROM inv.inventory ORDER BY name LIMIT 10")
    assert res["ok"] is True and res["error"] is None
    assert "name" in res["columns"]
    assert len(res["rows"]) == 10


def test_run_select_cross_db_join(seeded_db):
    """The agent's key capability: JOIN inv.* with txn.* in one read query."""
    res = db.run_select(
        "SELECT i.name, SUM(s.qty) sold FROM txn.sales s "
        "JOIN inv.inventory i ON i.item_id = s.item_id GROUP BY i.item_id "
        "ORDER BY sold DESC LIMIT 5"
    )
    assert res["ok"] is True
    assert len(res["rows"]) >= 1
    assert "name" in res["columns"] and "sold" in res["columns"]


def test_run_select_rejects_delete(seeded_db):
    before = db.qx("SELECT COUNT(*) AS n FROM txn.sales")[0]["n"]
    res = db.run_select("DELETE FROM sales")
    assert res["ok"] is False and res["rows"] == []
    after = db.qx("SELECT COUNT(*) AS n FROM txn.sales")[0]["n"]
    assert after == before  # guard prevented the write
