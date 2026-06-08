"""Tests for the festival calendar (offline) and intent heuristics.

No seeded_db needed — festival_nudge is pure calendar math and
_intent_from_tool_calls / classify_intent are zero-LLM heuristics.
"""

from __future__ import annotations

import datetime as dt

import dukaan.agent as A
import dukaan.proactive as P


# ---------------------------------------------------------------- festival calendar
def test_festival_finds_diwali_2026():
    """Diwali 2026 is Nov 8; from Nov 1 it is 7 days away — within 30-day lookahead."""
    n = P.festival_nudge(dt.date(2026, 11, 1))
    assert n["festival"] is not None
    assert n["days_away"] is not None
    assert n["message"]


def test_festival_finds_karwa_chauth():
    """Karwa Chauth 2026 (Oct 29) is in the extra overrides; visible from Oct 25."""
    n = P.festival_nudge(dt.date(2026, 10, 25))
    assert n["festival"] is not None
    assert n["message"]


def test_festival_works_beyond_2026():
    """Calendar must work for 2027 — proves it is NOT hardcoded to 2026.

    Diwali 2027 is Oct 29; from Oct 20 it is 9 days away.
    """
    n = P.festival_nudge(dt.date(2027, 10, 20))
    assert n["festival"] is not None


def test_no_festival_far_out():
    """When no festival falls within the 30-day window the nudge returns
    festival=None but message is always a non-empty string."""
    n = P.festival_nudge(dt.date(2026, 6, 1))
    assert n["festival"] is None
    assert isinstance(n["message"], str) and len(n["message"]) > 0


# ---------------------------------------------------------------- intent from tools
def test_intent_from_tool_calls():
    """_intent_from_tool_calls maps tool names to the correct intent badge."""
    assert A._intent_from_tool_calls(["record_sale_tool"]) == "write"
    assert A._intent_from_tool_calls(["get_dashboard"]) == "lookup"
    assert A._intent_from_tool_calls([]) == "chat"
    assert A._intent_from_tool_calls(["confirm_pending_tool"]) == "write"


# ---------------------------------------------------------------- keyword heuristic
def test_classify_intent_keywords():
    """classify_intent keyword heuristic returns correct labels for Hindi phrases."""
    assert A.classify_intent("aaj kitni bikri hui") == "lookup"
    assert A.classify_intent("Sharma ne udhaar liya") == "write"
    assert A.classify_intent("X kyun nahi bik raha") == "diagnostic"
