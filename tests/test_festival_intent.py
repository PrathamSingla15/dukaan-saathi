"""Tests for the festival calendar (offline) and intent heuristics.

No seeded_db needed — festival_nudge is pure calendar math and
_intent_from_tool_calls / classify_intent are zero-LLM heuristics.
"""

from __future__ import annotations

import datetime as dt

import dukaan.agent as A
import dukaan.proactive as P


# ---------------------------------------------------------------- festival calendar
def test_festival_finds_dhanteras_2026():
    """The comprehensive calendar now surfaces the whole Diwali cluster — from
    Nov 3 2026, Dhanteras (Nov 6) is the nearest festival. It was missing entirely
    from the old library-only calendar."""
    n = P.festival_nudge(dt.date(2026, 11, 3))
    assert n["festival"] is not None
    assert n["festival"]["name"] == "Dhanteras"
    assert n["days_away"] == 3
    assert n["message"]


def test_festival_finds_karwa_chauth_2026():
    """Karwa Chauth 2026 (Oct 29) is visible from Oct 25 — 4 days away."""
    n = P.festival_nudge(dt.date(2026, 10, 25))
    assert n["festival"] is not None
    assert n["festival"]["name"] == "Karwa Chauth"
    assert n["days_away"] == 4


def test_festival_works_2028():
    """Dataset covers 2028 (beyond the old 2026/2027 hardcode): from Oct 13 2028,
    Dhanteras (Oct 15) is the nearest festival — proves it is NOT hardcoded."""
    n = P.festival_nudge(dt.date(2028, 10, 13))
    assert n["festival"] is not None
    assert n["festival"]["name"] == "Dhanteras"


def test_festival_estimated_eid_flagged():
    """Eid is moon-sighting dependent → surfaces AND is flagged estimated.
    Eid-ul-Fitr 2026 is Mar 20; from Mar 16 it is 4 days away."""
    n = P.festival_nudge(dt.date(2026, 3, 16))
    assert n["festival"] is not None
    assert n["festival"]["name"].startswith("Eid")
    assert n["festival"].get("estimated") is True


def test_no_festival_far_out():
    """A genuine gap: from 2026-06-01 nothing falls within the 30-day window →
    festival=None, but message is always a non-empty string."""
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
