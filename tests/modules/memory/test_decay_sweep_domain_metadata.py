"""The decay sweep never removes a domain's own metadata keys (bu-pimdvx).

Health conditions keep their real status (active, managed, resolved) in
``metadata.status``; the sweep used to pop that key from every decaying fact in its
active, fading and expiry branches, so resolved and managed conditions read as active.
Only the sweep's own legacy marker (``status = 'fading'``, written before the validity
column and backfilled by mem_008) may be cleared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory import storage
from butlers.testing.fake_pool import AsyncCtx
from butlers.testing.records import row as _Row

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC)


class _Connection:
    def __init__(self, facts):
        self._facts = facts
        self.updates: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        if "memory_policies" in sql:
            return [
                _Row(
                    retention_class="operational",
                    min_retrieval_confidence=0.2,
                    archive_before_delete=False,
                )
            ]
        if "FROM facts" in sql:
            return self._facts
        return []

    async def execute(self, sql, *args):
        self.updates.append((sql, args))

    def transaction(self):
        return AsyncCtx(None)


class _Pool:
    def __init__(self, connection):
        self._connection = connection

    def acquire(self):
        return AsyncCtx(self._connection)


def _fact(*, confidence, validity="active", metadata):
    return _Row(
        id="fact-1",
        confidence=confidence,
        decay_rate=0.0001,
        last_confirmed_at=_NOW,
        created_at=_NOW,
        metadata=metadata,
        retention_class="operational",
        validity=validity,
    )


# (confidence, starting validity, validity the sweep must write or None for no write)
_BRANCHES = {
    "active-recovery": (0.9, "fading", "active"),
    "fading": (0.1, "active", "fading"),
    "expiry": (0.001, "active", "expired"),
}


async def _sweep(monkeypatch, fact):
    monkeypatch.setattr(storage, "_cascade_catalog_disownment", AsyncMock())
    connection = _Connection([fact])
    await storage.run_decay_sweep(_Pool(connection))
    return connection


@pytest.mark.parametrize("branch", sorted(_BRANCHES))
@pytest.mark.parametrize("status", ["resolved", "managed", "active", "in_progress"])
async def test_a_domain_status_survives_every_sweep_branch(monkeypatch, branch, status):
    confidence, validity, written = _BRANCHES[branch]
    metadata = {"name": "asthma", "status": status}

    connection = await _sweep(
        monkeypatch, _fact(confidence=confidence, validity=validity, metadata=metadata)
    )

    assert connection.updates, "the transition must still be written"
    for sql, args in connection.updates:
        if "metadata = $1" in sql:
            assert args[0]["status"] == status
            assert args[0]["name"] == "asthma"
    assert any(f"validity = '{written}'" in sql for sql, _ in connection.updates)


@pytest.mark.parametrize("branch", sorted(_BRANCHES))
async def test_the_sweeps_own_legacy_fading_marker_is_still_cleared(monkeypatch, branch):
    confidence, validity, _ = _BRANCHES[branch]
    metadata = {"name": "tea", "status": "fading"}

    connection = await _sweep(
        monkeypatch, _fact(confidence=confidence, validity=validity, metadata=metadata)
    )

    written = [args[0] for sql, args in connection.updates if "metadata = $1" in sql]
    assert written and all("status" not in item for item in written)
    assert all(item["name"] == "tea" for item in written)


async def test_a_healthy_active_fact_with_a_domain_status_is_not_rewritten(monkeypatch):
    connection = await _sweep(
        monkeypatch,
        _fact(confidence=0.9, metadata={"name": "asthma", "status": "managed"}),
    )

    assert connection.updates == []
