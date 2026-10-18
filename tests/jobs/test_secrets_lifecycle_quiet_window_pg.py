"""bu-kqnum.3.8 AC5: concurrent scans in one quiet window leave one pending envelope.

Real Postgres (migrated ``core`` chain): the dedup is the transaction-scoped advisory lock in
``replace_pending_deferred_notification``, so it can only be proven against a database.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.core.temporal.delivery_db import latest_status_matching_line
from butlers.db import register_jsonb_codec
from butlers.jobs import secrets_lifecycle as lifecycle
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_WINDOW = lifecycle._QuietWindow(
    start=datetime(2026, 10, 18, 22, 0, tzinfo=UTC),
    deliver_at=datetime(2099, 10, 19, 7, 0, tzinfo=UTC),
)
_CONCURRENT_SCANS = 8


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str):
    created = await asyncpg.create_pool(
        migrated_db_url,
        min_size=_CONCURRENT_SCANS,
        max_size=_CONCURRENT_SCANS,
        init=register_jsonb_codec,
    )
    await created.execute("TRUNCATE deferred_notifications")
    yield created
    await created.close()


class _Db:
    def __init__(self, pool):
        self._pool = pool

    def pool(self, name):
        assert name == "switchboard"
        return self._pool


def _marker(window=_WINDOW) -> str:
    return lifecycle._window_marker("http://dash", window)


async def _park(db, window=_WINDOW, labels=("Alpha", "Beta")):
    snapshots = [
        lifecycle.CredentialSnapshot(
            key=f"s:{label}", family="system", label=label, state="expired"
        )
        for label in labels
    ]
    marker = _marker(window)
    return await lifecycle._enqueue_deferred_envelope(
        db,
        channel="telegram",
        message=lifecycle._compose_window_message(snapshots, marker),
        recipient="chat-1",
        dedup_marker=marker,
        deliver_at=window.deliver_at,
        atomic=True,
    )


async def _statuses(pool) -> list[str]:
    rows = await pool.fetch("SELECT status FROM deferred_notifications ORDER BY deferred_at")
    return [row["status"] for row in rows]


async def test_concurrent_scans_in_one_window_leave_one_pending_envelope(pool):
    db = _Db(pool)

    refs = await asyncio.gather(*(_park(db) for _ in range(_CONCURRENT_SCANS)))

    assert all(refs)
    statuses = await _statuses(pool)
    assert statuses.count("pending") == 1
    assert statuses.count("cancelled") == _CONCURRENT_SCANS - 1
    survivor = await pool.fetchrow(
        "SELECT message, deliver_at FROM deferred_notifications WHERE status = 'pending'"
    )
    assert "Alpha" in survivor["message"] and "Beta" in survivor["message"]
    assert survivor["deliver_at"] == _WINDOW.deliver_at


async def test_a_later_scan_supersedes_the_pending_envelope(pool):
    db = _Db(pool)
    await _park(db, labels=("Alpha",))

    await _park(db, labels=("Alpha", "Beta"))

    assert await _statuses(pool) == ["cancelled", "pending"]
    message = await pool.fetchval(
        "SELECT message FROM deferred_notifications WHERE status='pending'"
    )
    assert "Beta" in message


async def test_a_different_window_keeps_its_own_pending_envelope(pool):
    db = _Db(pool)
    next_night = lifecycle._QuietWindow(
        start=datetime(2026, 10, 19, 22, 0, tzinfo=UTC),
        deliver_at=datetime(2099, 10, 20, 7, 0, tzinfo=UTC),
    )
    await _park(db)

    await _park(db, window=next_night)

    assert (await _statuses(pool)).count("pending") == 2


async def test_a_credential_retry_supersede_does_not_cancel_the_aggregate(pool):
    db = _Db(pool)
    await _park(db)

    cancelled = await lifecycle._supersede_pending_retries(
        pool, lifecycle._focus_fragment("s:Alpha")
    )

    assert cancelled == 0
    assert await _statuses(pool) == ["pending"]


async def test_the_parked_envelope_status_follows_the_flush(pool):
    db = _Db(pool)
    marker = _marker()
    assert (
        await latest_status_matching_line(pool, butler_name="switchboard", line_token=marker)
        is None
    )
    await _park(db)
    assert await lifecycle._parked_envelope_status(db, marker) == "pending"

    await pool.execute(
        "UPDATE deferred_notifications SET status = 'delivered' WHERE status = 'pending'"
    )

    assert await lifecycle._parked_envelope_status(db, marker) == "delivered"
