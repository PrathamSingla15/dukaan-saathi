"""End-to-end smoke tests through the full agent (needs a live llama-server).

Skipped automatically when the server is down, so ``pytest`` still collects and
runs the offline suite. Exercises the real Gemma-4 deepagents loop against the
two-database backend: a Hindi lookup and a Hindi udhaar write.
"""

from __future__ import annotations

import pytest

from dukaan import agent, llm, ops

pytestmark = pytest.mark.skipif(not llm.health(), reason="llama-server not up")


def test_agent_lookup_returns_nonempty_reply(seeded_db):
    """A Hindi sales-lookup turn returns a non-empty natural-language reply."""
    result = agent.run_agent("aaj kitni bikri hui?", thread_id="test_lookup")
    assert isinstance(result, dict)
    assert isinstance(result["reply"], str) and result["reply"].strip()


def test_agent_write_adds_udhaar(seeded_db):
    """A Hindi udhaar command must raise the (new) customer's balance by ~₹50."""
    name = "Tester Singh"  # unique → no pre-existing balance
    before = ops.customer_balance(name)
    before_bal = before["balance"] if before else 0.0

    agent.run_agent(f"{name} ne 50 rupaye ka udhaar liya", thread_id="test_write")

    after = ops.customer_balance(name)
    assert after is not None
    # Tolerant: the model may round / phrase amounts loosely.
    assert before_bal + 40 <= after["balance"] <= before_bal + 60
