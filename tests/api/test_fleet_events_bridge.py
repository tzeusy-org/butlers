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

import pytest

from butlers.api.fleet_events_bridge import (
    FLEET_EVENTS_CHANNEL,
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
