"""Two-pool integration test: Postgres NOTIFY publisher -> API LISTEN bridge.

Proves the actual cross-process delivery mechanism introduced by bu-01r64.1
(RFC 0022, about/legends-and-lore/rfcs/0022-cross-process-event-transport.md):
an event published via ``publish_fleet_event()`` on one asyncpg ``Pool``
(mimicking the daemon's own DB pool, running in the ``butlers-up``
container) is observed by ``run_fleet_events_listener()`` LISTENing via a
*separate* asyncpg ``Pool`` (mimicking the dashboard-api process's LISTEN
connection) and bridged onto the real in-process fleet event bus — the
exact same ``emit_event()``/``_events_ring`` that ``WS /api/events/stream``
serves. The two pools share nothing but the same physical Postgres
database, matching the real daemon-container vs dashboard-api-container
topology (docker-compose.yml) that made the bug possible in the first
place: same DB, different processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil

import asyncpg
import pytest

from butlers.api.fleet_events_bridge import run_fleet_events_listener
from butlers.fleet_events import publish_fleet_event
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def shared_db_url(postgres_container) -> str:
    """One real Postgres database shared by both pools below.

    LISTEN/NOTIFY is database-scoped (not schema/table-scoped), so both the
    "daemon" pool and the "api" pool must point at the same database for
    this test to prove anything — exactly like production, where every
    butler schema and the dashboard-api both live in one ``butlers``
    database (RFC 0006).
    """
    return create_migration_db(postgres_container, migration_db_name())


@pytest.fixture(autouse=True)
def _reset_bus():
    from butlers.api.routers.events import _reset_events_bus_for_tests

    _reset_events_bus_for_tests()
    yield
    _reset_events_bus_for_tests()


async def _wait_until(predicate, *, timeout_s: float = 10.0, interval_s: float = 0.05) -> None:
    elapsed = 0.0
    while not predicate():
        if elapsed >= timeout_s:
            raise AssertionError(f"condition not met within {timeout_s}s")
        await asyncio.sleep(interval_s)
        elapsed += interval_s


async def test_event_published_on_one_pool_arrives_via_the_other(shared_db_url):
    daemon_pool = await asyncpg.create_pool(shared_db_url, min_size=1, max_size=2)
    api_pool = await asyncpg.create_pool(shared_db_url, min_size=1, max_size=1)

    async def connect() -> asyncpg.Connection:
        # A dedicated connection acquired from the *second* pool and held for
        # the listener's lifetime, never released mid-test — mirrors
        # run_fleet_events_listener's real contract (LISTEN registrations are
        # connection-scoped, so the bridge never borrows-and-returns).
        return await api_pool.acquire()

    listener_task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.05)
    )
    try:
        from butlers.api.routers.events import _events_ring

        # Give the listener a moment to establish LISTEN before publishing —
        # a NOTIFY sent before LISTEN is registered is never delivered
        # (Postgres does not queue for not-yet-subscribed listeners).
        await asyncio.sleep(0.3)

        published = await publish_fleet_event(
            daemon_pool,
            "session",
            {"phase": "started", "session_id": "two-pool-test", "butler": "general"},
        )
        assert published is True

        await _wait_until(lambda: len(_events_ring) >= 1)

        event = _events_ring[-1]
        assert event["type"] == "session"
        assert event["data"] == {
            "phase": "started",
            "session_id": "two-pool-test",
            "butler": "general",
        }
    finally:
        listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener_task
        await daemon_pool.close()
        await api_pool.close()


async def test_multiple_events_across_types_all_arrive_in_order(shared_db_url):
    daemon_pool = await asyncpg.create_pool(shared_db_url, min_size=1, max_size=2)
    api_pool = await asyncpg.create_pool(shared_db_url, min_size=1, max_size=1)

    async def connect() -> asyncpg.Connection:
        return await api_pool.acquire()

    listener_task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.05)
    )
    try:
        from butlers.api.routers.events import _events_ring

        await asyncio.sleep(0.3)

        await publish_fleet_event(daemon_pool, "session", {"phase": "started"})
        await publish_fleet_event(daemon_pool, "spend", {"cost_usd": 0.01})
        await publish_fleet_event(daemon_pool, "approval", {"kind": "created"})
        await publish_fleet_event(daemon_pool, "notification", {"channel": "telegram"})
        await publish_fleet_event(
            daemon_pool,
            "ingestion",
            {"request_id": "ingestion-two-pool-test", "source_channel": "telegram_bot"},
        )

        await _wait_until(lambda: len(_events_ring) >= 5)

        types = [e["type"] for e in _events_ring]
        assert types == ["session", "spend", "approval", "notification", "ingestion"]
        assert _events_ring[-1]["data"] == {
            "request_id": "ingestion-two-pool-test",
            "source_channel": "telegram_bot",
        }
    finally:
        listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener_task
        await daemon_pool.close()
        await api_pool.close()


async def test_listener_reconnects_and_keeps_delivering_after_connection_drop(shared_db_url):
    """Regression guard: a killed LISTEN connection must not permanently
    silence the bridge (that would recreate the exact "Live indicator shows
    connected but nothing arrives" failure mode bu-01r64 exists to fix)."""
    daemon_pool = await asyncpg.create_pool(shared_db_url, min_size=1, max_size=2)
    api_pool = await asyncpg.create_pool(shared_db_url, min_size=2, max_size=2)

    connections: list[asyncpg.Connection] = []

    async def connect() -> asyncpg.Connection:
        conn = await api_pool.acquire()
        connections.append(conn)
        return conn

    listener_task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.05, reconnect_backoff_s=0.1)
    )
    try:
        from butlers.api.routers.events import _events_ring

        await _wait_until(lambda: len(connections) >= 1)
        await asyncio.sleep(0.3)

        await publish_fleet_event(daemon_pool, "session", {"phase": "started"})
        await _wait_until(lambda: len(_events_ring) >= 1)

        # Simulate the connection dying underneath the bridge (server
        # restart, network blip). Closing it directly deterministically
        # flips is_closed() -- more reliable in CI than racing a server-side
        # pg_terminate_backend against TCP RST propagation timing.
        await connections[0].close()

        # The bridge should notice (health poll) and reconnect.
        await _wait_until(lambda: len(connections) >= 2, timeout_s=15.0)
        await asyncio.sleep(0.3)

        published = await publish_fleet_event(
            daemon_pool, "session", {"phase": "ended", "session_id": "after-reconnect"}
        )
        assert published is True

        await _wait_until(lambda: len(_events_ring) >= 2, timeout_s=10.0)
        assert _events_ring[-1]["data"]["session_id"] == "after-reconnect"
    finally:
        listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener_task
        await daemon_pool.close()
        await api_pool.close()
