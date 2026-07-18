"""Postgres NOTIFY publisher -> dashboard LISTEN bridge integration coverage.

The two-pool tests keep their publisher and listener in one Python process.
They prove PostgreSQL delivery between independent connections and the bridge's
in-process event-bus handoff, but deliberately do *not* claim an OS-process or
WebSocket end-to-end proof.

``test_calendar_and_chronicler_child_processes_reach_websocket`` closes that
specific coverage gap without a shared Compose stack: Calendar and Chronicler
run in separate child Python processes against an isolated testcontainer
database; the dashboard side runs the real LISTEN bridge and
``WS /api/events/stream`` route. It therefore proves the production transport
boundary and WebSocket frame delivery, while leaving full Compose/container
wiring outside this focused harness's claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from butlers.api.fleet_events_bridge import run_fleet_events_listener
from butlers.fleet_events import publish_fleet_event
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def shared_db_url(postgres_container) -> str:
    """One migrated Postgres database shared by all producer/listener tests.

    LISTEN/NOTIFY is database-scoped (not schema/table-scoped), so both the
    child-process producers and dashboard listener must point at the same
    database for this test to prove anything — exactly like production, where
    every butler schema and the dashboard-api both live in one ``butlers``
    database (RFC 0006). The Chronicler chain is migrated so its child-process
    fixture adapter writes a real projection row before it publishes.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


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


class _ReadyListenerConnection:
    """Connection wrapper that exposes a deterministic LISTEN-ready signal."""

    def __init__(self, connection: asyncpg.Connection, ready: asyncio.Event) -> None:
        self._connection = connection
        self._ready = ready

    async def add_listener(self, channel: str, callback: Any) -> None:
        await self._connection.add_listener(channel, callback)
        self._ready.set()

    def is_closed(self) -> bool:
        return self._connection.is_closed()

    async def close(self) -> None:
        await self._connection.close()


def _dashboard_events_lifespan(shared_db_url: str):
    """Run the production listener alongside only the production events route."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        listener_ready = asyncio.Event()

        async def connect() -> _ReadyListenerConnection:
            connection = await asyncpg.connect(shared_db_url)
            return _ReadyListenerConnection(connection, listener_ready)

        listener_task = asyncio.create_task(
            run_fleet_events_listener(
                connect,
                health_poll_interval_s=0.02,
                reconnect_backoff_s=0.02,
            )
        )
        try:
            await asyncio.wait_for(listener_ready.wait(), timeout=5.0)
        except BaseException:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task
            raise

        try:
            yield
        finally:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task

    return lifespan


async def _run_external_producer(producer: str, shared_db_url: str) -> int:
    """Run one real producer path in a fresh OS process and return its PID."""

    environment = dict(os.environ)
    environment["BUTLERS_FLEET_EVENTS_TEST_DATABASE_URL"] = shared_db_url
    repository_root = Path(__file__).resolve().parents[2]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.fleet_events_external_producer",
        producer,
        cwd=repository_root,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AssertionError(f"{producer} child producer did not finish") from exc

    assert process.returncode == 0, stderr.decode(errors="replace")
    result = json.loads(stdout)
    assert process.pid is not None
    assert result == {"producer": producer, "pid": process.pid}
    return process.pid


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


@pytest.mark.timeout(30)
async def test_calendar_and_chronicler_child_processes_reach_websocket(shared_db_url):
    """Prove both production producers cross an OS-process boundary to WS.

    This intentionally starts neither ``scripts/compose.sh`` nor a dashboard
    container. The testcontainer database is isolated, each producer is a
    fresh child Python process, and the dashboard side uses the real LISTEN
    bridge plus the real WebSocket route. Frontend cache invalidation remains
    covered separately by ``event-cache-registry.test.ts``; this test proves
    the preceding transport and WebSocket frame boundary.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from butlers.api.routers.events import router

    dashboard_app = FastAPI(lifespan=_dashboard_events_lifespan(shared_db_url))
    dashboard_app.include_router(router)
    dashboard_process_id = os.getpid()

    with TestClient(dashboard_app) as client:
        with client.websocket_connect("/api/events/stream") as websocket:
            snapshot = json.loads(websocket.receive_text())
            assert snapshot["type"] == "snapshot"
            assert snapshot["events"] == []

            calendar_process_id = await _run_external_producer("calendar", shared_db_url)
            chronicler_process_id = await _run_external_producer("chronicler", shared_db_url)

            inspection_connection = await asyncpg.connect(shared_db_url)
            try:
                durable_rows = await inspection_connection.fetchval(
                    "SELECT count(*) FROM point_events WHERE source_ref = $1",
                    f"fleet-event-transport-proof:{chronicler_process_id}",
                )
            finally:
                await inspection_connection.close()
            assert durable_rows == 1

            received = [json.loads(websocket.receive_text()) for _ in range(2)]

    assert calendar_process_id != dashboard_process_id
    assert chronicler_process_id != dashboard_process_id

    events_by_type = {event["type"]: event for event in received}
    assert set(events_by_type) == {"calendar", "chronicles"}
    assert events_by_type["calendar"]["data"] == {
        "kind": "provider_projection",
        "updated_events": 1,
        "cancelled_events": 0,
    }
    assert events_by_type["chronicles"]["data"] == {
        "kind": "projection",
        "rows_projected": 1,
        "point_events": 1,
        "episodes_opened": 0,
        "episodes_closed": 0,
    }


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
