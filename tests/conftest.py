"""Shared pytest fixtures for the Dukaan Saathi suite (two-database edition).

The suite runs against throwaway SQLite files (tmp) so it never touches the real
demo databases. We monkeypatch both DB paths onto a tmp dir, then build fresh
seeded schemas from the research-generated seed modules.

No network / llama-server / ML model load happens at import — only ``llm.health()``
is probed once (3s timeout, False when down) to expose ``SERVER_UP`` for e2e gating.
"""

from __future__ import annotations

import pytest

from dukaan import config, db, llm

# True only when llama-server answers /health (safe + fast when offline).
SERVER_UP: bool = llm.health()


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """Point both databases at fresh, fully-seeded tmp files."""
    inv = tmp_path / "inventory.db"
    txn = tmp_path / "transactions.db"
    for target in (config, db.config):
        monkeypatch.setattr(target, "INVENTORY_DB_PATH", inv, raising=True)
        monkeypatch.setattr(target, "TRANSACTIONS_DB_PATH", txn, raising=True)

    db.init_db(reset=True, seed=True)
    yield (inv, txn)
