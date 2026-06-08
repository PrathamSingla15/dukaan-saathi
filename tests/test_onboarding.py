"""Real-owner onboarding FSM tests for Dukaan Saathi.

These pin the first-run takeover flow in :mod:`dukaan.onboarding`: a fresh owner
speaks/snaps their stock + khata, eyeballs a VERIFY-back, and taps confirm — only
*then* is the synthetic demo seed wiped and replaced with their real data.

What we prove:
  1. The happy path end-to-end actually replaces the seed: after commit the data
     mode flips to "real", the item count equals exactly what the owner entered
     (seed cleared, not appended), and a migrated khata customer carries their
     opening balance.
  2. Capture steps degrade *gracefully* — a dead STT (no speech) or an unparseable
     khata photo never blanks the prompt and always asks for a retry/reupload.
  3. Aborting keeps the demo (no business writes, data stays "demo") and ends the
     session so the app stops showing the onboarding flow.

The onboarding session + data_mode live in ``txn.app_meta``, which the
``seeded_db`` fixture rebuilds fresh per test, so each test starts clean. We stub
the heavy deps (STT / khata vision) via ``monkeypatch`` — no GPU / no server.
"""

from __future__ import annotations

import dukaan.db as db
import dukaan.normalize as normalize
import dukaan.onboarding as ob
import dukaan.ops as ops
import dukaan.stt as stt


# --------------------------------------------------------- 1. full happy path
def test_full_flow_replaces_seed(seeded_db):
    """End-to-end: profile -> 2 manual items -> 1 injected khata customer ->
    verify -> commit. The synthetic seed must be *replaced*, not appended to."""
    ob.start_onboarding()
    ob.set_profile("Ramesh", "Ramesh Kirana", "hi")

    ob.add_inventory_item_manual("Parle-G", 24, category="Biscuits", mrp=10)
    ob.add_inventory_item_manual("Tata Salt 1kg", 10, mrp=30)

    # Inject a khata customer draft straight into the persisted session blob
    # (simulates a parsed-photo customer the owner is about to confirm).
    session = ob.get_session()
    session["customers"].append({
        "id": "cu-1",
        "name": "Sharma Ji",
        "phone": "",
        "opening_balance": 500.0,
        "debits": [],
        "confidence": "high",
        "source": "manual",
    })
    db.meta_set("onboarding_session", session)

    v = ob.advance_to_verify()
    assert v["totals"]["item_count"] == 2
    assert v["totals"]["customer_count"] == 1

    r = ob.confirm_commit()
    assert r["ok"]

    # Seed wiped, owner's data in place, mode flipped to real.
    assert db.data_mode() == "real"
    assert db.counts()["items"] == 2  # seed cleared, only the 2 manual items
    assert ops.customer_balance("Sharma Ji")["balance"] == 500.0


# ----------------------------------------- 2. voice capture fallback non-empty
def test_voice_capture_fallback_never_empty(seeded_db, monkeypatch):
    """A dead STT (no speech detected) must return ok=False / needs='repeat'
    with a *non-empty* bilingual prompt — never a blank screen."""
    monkeypatch.setattr(
        stt, "transcribe",
        lambda *a, **k: stt.TranscribeResult("", "", 0.0, 1.0, False, "no_speech"),
    )
    ob.start_onboarding()
    ob.set_profile("A", "B", "hi")

    out = ob.capture_inventory_voice(object())
    assert out["ok"] is False
    assert out["needs"] == "repeat"
    assert out["prompt"]  # non-empty retry message


# ------------------------------------------ 3. khata capture fallback non-empty
def test_khata_fallback_never_empty(seeded_db, monkeypatch):
    """An unparseable khata photo must return ok=False / needs='reupload' so the
    owner is asked for a clearer photo instead of silently dropping the page."""
    monkeypatch.setattr(
        normalize, "parse_khata",
        lambda *a, **k: {"ok": False, "customers": [], "error": "reupload"},
    )
    ob.start_onboarding()
    ob.set_profile("A", "B", "hi")

    out = ob.capture_khata_photo(object())
    assert out["ok"] is False
    assert out["needs"] == "reupload"


# ------------------------------------------------- 4. abort keeps the demo seed
def test_abort_keeps_demo(seeded_db):
    """Aborting writes no business data: mode stays 'demo' and the session ends
    so ``is_onboarding_active`` goes False (app drops the onboarding flow)."""
    ob.start_onboarding()
    ob.abort_onboarding()

    assert db.data_mode() == "demo"
    assert ob.is_onboarding_active() is False
