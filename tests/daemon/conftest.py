"""Explicit custody infrastructure boundary for existing simulated daemon tests.

Tests that replace the database/pool already skip migrations and PostgreSQL.
They must not accidentally contact the host database when startup adds a new
anchor. Real Database/pool instances still run actual custody startup. Neither
branch manufactures an admission, source, principal, or currentness verdict.
Owning custody SQL/transport proofs use their migrated fixtures elsewhere.
"""

from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def _custody_startup_for_simulated_database(monkeypatch):
    from butlers.core import custody_lifecycle

    actual_start = custody_lifecycle.start_daemon_custody

    async def start(daemon):
        database = daemon.db
        if isinstance(database, Mock) or isinstance(getattr(database, "pool", None), Mock):
            assert daemon._custody_runtime is None
            return
        await actual_start(daemon)

    monkeypatch.setattr(custody_lifecycle, "start_daemon_custody", start)
