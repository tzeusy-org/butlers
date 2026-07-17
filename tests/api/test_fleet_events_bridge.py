"""Unit tests for butlers.api.fleet_events_bridge (bu-01r64.1).

Covers the dashboard-api side of the cross-process NOTIFY/LISTEN bridge
(RFC 0022) without a real database:
- ``_on_notify`` parses a valid envelope and bridges it onto the real
  in-process event bus (``emit_event``)
- Malformed / wrong-channel / non-dict payloads are dropped, not raised
- ``run_fleet_events_listener`` registers a listener via ``add_listener``,
  reconnects after the connection closes, and reconnects after a connect
  failure — using fake connections so no Docker/Postgres is required
  (the real cross-process delivery path is covered by the two-pool
  integration test in tests/integration/).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from butlers.api import fleet_events_bridge
from butlers.api.fleet_events_bridge import (
    FLEET_EVENTS_CHANNEL,
    _connect_listener,
    _on_notify,
    run_fleet_events_listener,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bus():
    from butlers.api.routers.events import _reset_events_bus_for_tests

    _reset_events_bus_for_tests()
    yield
    _reset_events_bus_for_tests()


# ---------------------------------------------------------------------------
# _on_notify — payload parsing / bridging
# ---------------------------------------------------------------------------


def test_on_notify_bridges_valid_envelope_to_event_bus():
    from butlers.api.routers.events import _events_ring

    payload = json.dumps({"type": "session", "data": {"phase": "started", "session_id": "s1"}})

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, payload)

    assert len(_events_ring) == 1
    event = _events_ring[-1]
    assert event["type"] == "session"
    assert event["data"] == {"phase": "started", "session_id": "s1"}


def test_on_notify_ignores_other_channels():
    from butlers.api.routers.events import _events_ring

    payload = json.dumps({"type": "session", "data": {}})

    _on_notify(None, 1, "some_other_channel", payload)

    assert len(_events_ring) == 0


def test_on_notify_drops_malformed_json():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, "{not json")

    assert len(_events_ring) == 0


def test_on_notify_drops_non_object_payload():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps([1, 2, 3]))

    assert len(_events_ring) == 0


def test_on_notify_drops_payload_missing_type():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps({"data": {"a": 1}}))

    assert len(_events_ring) == 0


def test_on_notify_defaults_non_dict_data_to_empty():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps({"type": "spend", "data": "oops"}))

    assert len(_events_ring) == 1
    assert _events_ring[-1]["data"] == {}


# ---------------------------------------------------------------------------
# _connect_listener — environment target selection
# ---------------------------------------------------------------------------


async def test_connect_listener_uses_decoded_database_url_path_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedicated LISTEN connection must target DATABASE_URL's database."""
    captured_kwargs: dict[str, object] = {}
    sentinel = object()

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return sentinel

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fleet_user:fleet_password@db.example:5432/fleet%5Fevents%2Dtarget",
    )
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    assert await _connect_listener() is sentinel
    assert captured_kwargs["database"] == "fleet_events-target"


async def test_connect_listener_database_url_overrides_postgres_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSTGRES_DB cannot redirect a listener when DATABASE_URL is supplied."""
    captured_kwargs: dict[str, object] = {}

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fleet_user:fleet_password@db.example:5432/url_fleet_events",
    )
    monkeypatch.setenv("POSTGRES_DB", "ignored_fallback_database")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    await _connect_listener()

    assert captured_kwargs["database"] == "url_fleet_events"


async def test_connect_listener_uses_postgres_db_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSTGRES_DB remains the explicit fallback when DATABASE_URL is absent."""
    captured_kwargs: dict[str, object] = {}

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_DB", "configured_fleet_events_database")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    await _connect_listener()

    assert captured_kwargs["database"] == "configured_fleet_events_database"


async def test_connect_listener_rejects_database_url_without_database_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathless URL must not silently connect the listener elsewhere."""
    connect_calls: list[dict[str, Any]] = []

    async def unexpected_connect(**kwargs: Any) -> object:
        connect_calls.append(kwargs)
        raise AssertionError("pathless DATABASE_URL opened a listener connection")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example:5432/")
    monkeypatch.setenv("POSTGRES_DB", "not_a_url_fallback")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", unexpected_connect)

    with pytest.raises(ValueError, match="must include a database path"):
        await _connect_listener()

    assert connect_calls == []


# ---------------------------------------------------------------------------
# run_fleet_events_listener — connection lifecycle (fake connections)
# ---------------------------------------------------------------------------


class _FakeConnection:
    """Minimal stand-in for asyncpg.Connection: add_listener + is_closed/close."""

    def __init__(self) -> None:
        self._closed = False
        self.listeners: list[tuple[str, object]] = []

    async def add_listener(self, channel: str, callback: object) -> None:
        self.listeners.append((channel, callback))

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True


async def test_run_fleet_events_listener_registers_listener_on_connect():
    conn = _FakeConnection()

    async def connect() -> _FakeConnection:
        return conn

    task = asyncio.create_task(run_fleet_events_listener(connect, health_poll_interval_s=0.01))
    try:
        for _ in range(200):
            if conn.listeners:
                break
            await asyncio.sleep(0.01)
        assert conn.listeners == [(FLEET_EVENTS_CHANNEL, _on_notify)]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_fleet_events_listener_reconnects_after_connection_closes():
    connections: list[_FakeConnection] = []

    async def connect() -> _FakeConnection:
        conn = _FakeConnection()
        connections.append(conn)
        return conn

    task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.01, reconnect_backoff_s=0.01)
    )
    try:
        # Wait for the first connection, then simulate it dying.
        for _ in range(200):
            if connections:
                break
            await asyncio.sleep(0.01)
        assert len(connections) == 1
        connections[0]._closed = True

        # A second connection should be established after the health poll
        # notices the first is closed and the reconnect backoff elapses.
        for _ in range(400):
            if len(connections) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(connections) >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_fleet_events_listener_reconnects_after_connect_failure():
    attempts = {"count": 0}

    async def connect() -> _FakeConnection:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionRefusedError("db not ready yet")
        return _FakeConnection()

    task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.01, reconnect_backoff_s=0.01)
    )
    try:
        for _ in range(400):
            if attempts["count"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert attempts["count"] >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
