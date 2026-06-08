"""handle_turn resolves a staged write from a yes/no WITHOUT the agent.

Proves the deterministic confirm-before-write turn-2 path: once a write is staged
for a thread, a plain "haan" sent through session.handle_turn commits it directly
(the small LLM is only needed to STAGE on turn 1, never to commit).
"""
from __future__ import annotations

import numpy as np

from dukaan import agent, db, ops, session, staging, tts


def test_handle_turn_deterministic_confirm(seeded_db, monkeypatch):
    monkeypatch.setattr(tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))

    def _boom(*a, **k):
        raise AssertionError("agent.run_agent must NOT be called on a confirm turn")

    monkeypatch.setattr(agent, "run_agent", _boom)

    tid = "det-confirm"
    staging.clear_pending(tid)
    item = db.qx("SELECT name, qty FROM inv.inventory WHERE qty >= 3 ORDER BY item_id LIMIT 1")[0]
    staging.stage_op(tid, "record_sale", {"item_name": item["name"], "qty": 1}, "1 bika")

    # "haan" through handle_turn commits the staged sale deterministically.
    r = session.handle_turn(text="haan", thread_id=tid, tts=False)
    assert r.intent_badge == "write"
    assert ops.find_item(item["name"])["qty"] == item["qty"] - 1
    assert staging.get_pending(tid) is None

    # And "nahi" on a fresh stage cancels without writing (still no agent call).
    staging.stage_op(tid, "record_sale", {"item_name": item["name"], "qty": 2}, "2 bika")
    before = ops.find_item(item["name"])["qty"]
    session.handle_turn(text="nahi", thread_id=tid, tts=False)
    assert ops.find_item(item["name"])["qty"] == before  # unchanged
    assert staging.get_pending(tid) is None
    staging.clear_pending(tid)
