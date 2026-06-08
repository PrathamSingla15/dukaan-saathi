"""Seam tests for the UI-agnostic orchestration layer (``dukaan.session``).

These pin the front-end contract WITHOUT any model/server: STT, the deepagents
loop, image OCR and TTS are all monkeypatched, so a turn flows through pure
orchestration. We prove:

* a text turn shapes the agent's reply into a ``TurnResult`` (intent badge, a
  best-effort dashboard snapshot, no error);
* an empty/failed STT short-circuits into a *clarification* turn and NEVER calls
  the agent;
* a failed OCR short-circuits into a *re-upload* turn;
* a staged write commits on "haan" and is dropped (stock untouched) on "nahi";
* the result is JSON-serialisable via ``to_dict``.

Shared-state hygiene: ``staging._PENDING`` is a process global, so every test
that stages clears its thread at start AND end.
"""

from __future__ import annotations

import json

import numpy as np

import dukaan.agent
import dukaan.db  # noqa: F401  (kept explicit per spec / for clarity)
import dukaan.normalize
import dukaan.ops as ops
import dukaan.session as S
import dukaan.staging as staging
import dukaan.stt
import dukaan.tts
from dukaan.normalize import DescribeResult
from dukaan.stt import TranscribeResult


def _item_with_qty(min_qty: int = 3) -> str:
    """Name of any seeded inventory item with at least ``min_qty`` in stock."""
    rows = dukaan.db.qx(
        "SELECT name FROM inv.inventory WHERE qty >= ? ORDER BY item_id LIMIT 1",
        (min_qty,),
    )
    assert rows, f"seed has no item with qty >= {min_qty}"
    return rows[0]["name"]


# --------------------------------------------------------------- 1. text turn
def test_text_turn(seeded_db, monkeypatch):
    """A typed turn: the agent's structured reply becomes a rendered TurnResult."""
    monkeypatch.setattr(dukaan.tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))
    staging.clear_pending(staging.current_thread())

    monkeypatch.setattr(
        dukaan.agent,
        "run_agent",
        lambda t, thread_id="default": {
            "reply": "आज की बिक्री ₹500 रही।",
            "messages": [],
            "tool_calls": ["get_dashboard"],
            "intent": "lookup",
            "pending": None,
        },
    )

    r = S.handle_turn(text="aaj bikri", thread_id="t1", tts=False)
    assert r.reply_text
    assert r.intent_badge == "lookup"
    assert isinstance(r.dashboard_snapshot, dict)
    assert r.error is None


# ----------------------------------------------- 2. empty STT -> clarification
def test_stt_empty_fallback_no_agent(seeded_db, monkeypatch):
    """A failed transcription short-circuits to a clarification; agent NOT called."""
    monkeypatch.setattr(dukaan.tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))
    staging.clear_pending(staging.current_thread())

    monkeypatch.setattr(
        dukaan.stt,
        "transcribe",
        lambda *a, **k: TranscribeResult("", "", 0.0, 1.0, False, "no_speech"),
    )

    def _boom(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(dukaan.agent, "run_agent", _boom)

    r = S.handle_turn(
        audio=(16000, np.zeros(8000, dtype=np.float32)), thread_id="t1", tts=False
    )
    assert r.clarification
    assert r.reply_text == r.clarification


# ------------------------------------------------- 3. failed OCR -> re-upload
def test_ocr_reupload(seeded_db, monkeypatch):
    """A failed image describe surfaces as a needs-reupload turn with a message."""
    monkeypatch.setattr(dukaan.tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))
    staging.clear_pending(staging.current_thread())

    monkeypatch.setattr(
        dukaan.normalize,
        "describe_for_agent",
        lambda *a, **k: DescribeResult("फोटो साफ़ नहीं आई — दोबारा भेजें।", False, "reupload"),
    )

    r = S.handle_turn(image=object(), thread_id="t1", tts=False)
    assert r.needs_reupload is True
    assert r.reply_text


# ----------------------------------------- 4. confirm pending commits / cancels
def test_confirm_pending_commits(seeded_db, monkeypatch):
    """"haan" commits the staged sale (stock -1); "nahi" leaves stock untouched."""
    monkeypatch.setattr(dukaan.tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))
    staging.clear_pending("t2")

    name = _item_with_qty(3)

    # --- "haan" path: real staged sale should decrement stock by 1 ---
    staging.stage_op("t2", "record_sale", {"item_name": name, "qty": 1}, "1 bika")
    before = ops.find_item(name)["qty"]
    r = S.confirm_pending("haan", thread_id="t2", tts=False)
    assert r.intent_badge == "write"
    assert ops.find_item(name)["qty"] == before - 1

    # --- "nahi" path: a fresh stage is dropped, stock unchanged, pending cleared ---
    after_yes = ops.find_item(name)["qty"]
    staging.stage_op("t2", "record_sale", {"item_name": name, "qty": 1}, "1 bika")
    r2 = S.confirm_pending("nahi", thread_id="t2", tts=False)
    assert ops.find_item(name)["qty"] == after_yes  # nothing written
    assert staging.get_pending("t2") is None  # batch dropped

    staging.clear_pending("t2")


# ------------------------------------------------------ 5. to_dict is JSON-able
def test_to_dict_jsonable(seeded_db, monkeypatch):
    """``TurnResult.to_dict()`` is plain-JSON serialisable (no ndarray leaks)."""
    monkeypatch.setattr(dukaan.tts, "synthesize", lambda *a, **k: (16000, np.zeros(1)))
    staging.clear_pending(staging.current_thread())

    monkeypatch.setattr(
        dukaan.agent,
        "run_agent",
        lambda t, thread_id="default": {
            "reply": "नमस्ते!",
            "messages": [],
            "tool_calls": [],
            "intent": "chat",
            "pending": None,
        },
    )

    r = S.handle_turn(text="hi", thread_id="t1", tts=False)
    # Must not raise:
    json.dumps(r.to_dict())
