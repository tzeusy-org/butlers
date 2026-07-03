"""Tests for the multiplexed fleet event bus — WS /api/events/stream (bu-86c4c.8, move 5).

Covers:
- WS connect/snapshot/live-event/auth (mirrors test_spend.py's WS stream tests)
- Heartbeat emission on an idle connection
- Ring buffer overflow behaviour
- The three existing channels (approvals, spend) fan their events onto this
  bus in addition to their own dedicated streams
- The new choke points (session lifecycle, notify() delivery, audit-log
  errors) emit onto the bus
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bus():
    """Clear the events bus ring buffer/subscribers before and after each test."""
    from butlers.api.routers.events import _reset_events_bus_for_tests

    _reset_events_bus_for_tests()
    yield
    _reset_events_bus_for_tests()


# ---------------------------------------------------------------------------
# WS /api/events/stream — connect / snapshot / live event / auth
# ---------------------------------------------------------------------------


def test_events_stream_connect_and_receive_snapshot(app):
    """Connecting immediately yields a snapshot message with an events list."""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/api/events/stream") as ws:
            snap = json.loads(ws.receive_text())
            assert snap["type"] == "snapshot"
            assert isinstance(snap["events"], list)
            assert snap["events"] == []


def test_events_stream_receives_emitted_event(app):
    """A connected subscriber immediately receives an emit_event() call."""
    from fastapi.testclient import TestClient

    from butlers.api.routers.events import emit_event

    with TestClient(app) as client:
        with client.websocket_connect("/api/events/stream") as ws:
            ws.receive_text()  # snapshot

            emit_event("approval", {"kind": "created", "approval_id": "abc-123"})

            msg = json.loads(ws.receive_text())
            assert msg["type"] == "approval"
            assert msg["data"]["kind"] == "created"
            assert msg["data"]["approval_id"] == "abc-123"
            assert "ts" in msg


async def test_events_stream_subscribes_before_snapshot_send(monkeypatch):
    """Regression (bu-fq8y1): subscribing must happen BEFORE the snapshot is
    sent, so an event emitted during/right after the snapshot send — the old
    miss-window between snapshot-send and subscribe — is still delivered live
    instead of being silently dropped.

    Drives ``events_stream`` directly against a fake websocket so the emit can
    be pinned to the exact moment the snapshot is flushed, rather than relying
    on real-world scheduling to hit a race window.
    """
    from starlette.websockets import WebSocketDisconnect

    import butlers.api.routers.events as events_mod

    monkeypatch.setattr(events_mod, "_EVENTS_HEARTBEAT_INTERVAL_S", 0.05)

    sent: list[dict] = []
    emitted = {"done": False}

    class _FakeWebSocket:
        async def accept(self) -> None:
            return None

        async def send_text(self, text: str) -> None:
            msg = json.loads(text)
            sent.append(msg)
            if not emitted["done"] and msg["type"] == "snapshot":
                # Fires exactly when the snapshot hits the wire. With the fix
                # this connection is already subscribed, so this event must
                # still reach it via the live queue.
                emitted["done"] = True
                events_mod.emit_event("notification", {"kind": "gap-event"})
            if len(sent) >= 2:
                raise WebSocketDisconnect(code=1000)

    ws = _FakeWebSocket()
    await events_mod.events_stream(ws, api_key=None)

    assert sent[0]["type"] == "snapshot"
    assert sent[1]["type"] == "notification"
    assert sent[1]["data"]["kind"] == "gap-event"
    # The finally block ran and cleaned up the subscriber on disconnect.
    assert events_mod._events_subscribers == []


def test_events_stream_snapshot_includes_recent_events(app):
    """Snapshot contains events emitted before the connection was opened."""
    from fastapi.testclient import TestClient

    from butlers.api.routers.events import emit_event

    emit_event("spend", {"kind": "call", "butler": "atlas"})

    with TestClient(app) as client:
        with client.websocket_connect("/api/events/stream") as ws:
            snap = json.loads(ws.receive_text())
            assert len(snap["events"]) == 1
            assert snap["events"][0]["type"] == "spend"
            assert snap["events"][0]["data"]["butler"] == "atlas"


def test_events_stream_auth_rejected_when_key_configured(app, monkeypatch):
    """WS closes with 4401 when api_key is wrong (matches the other 3 streams)."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "secret-key")

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/events/stream?api_key=wrong-key") as ws:
                ws.receive_text()
    assert exc_info.value.code == 4401


def test_events_stream_auth_accepted_with_correct_key(app, monkeypatch):
    """WS accepts the connection when api_key matches."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "correct-key")

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/api/events/stream?api_key=correct-key") as ws:
            snap = json.loads(ws.receive_text())
            assert snap["type"] == "snapshot"


def test_events_stream_heartbeat_on_idle(app, monkeypatch):
    """An idle connection receives a synthetic heartbeat event."""
    import butlers.api.routers.events as events_mod

    monkeypatch.setattr(events_mod, "_EVENTS_HEARTBEAT_INTERVAL_S", 0.05)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/api/events/stream") as ws:
            ws.receive_text()  # snapshot
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "heartbeat"
            assert msg["data"] == {}


def test_emit_event_ring_buffer_trims_to_max():
    """The ring buffer never grows past _RING_BUFFER_SIZE."""
    import butlers.api.routers.events as events_mod
    from butlers.api.routers.events import emit_event

    for i in range(events_mod._RING_BUFFER_SIZE + 10):
        emit_event("heartbeat", {"i": i})

    assert len(events_mod._events_ring) == events_mod._RING_BUFFER_SIZE
    # Oldest events were dropped — the first surviving event is #10.
    assert events_mod._events_ring[0]["data"]["i"] == 10


def test_emit_event_drops_slow_subscriber_queue():
    """A subscriber whose queue fills up is dropped rather than blocking emit."""
    import asyncio

    from butlers.api.routers.events import (
        _events_subscribers,
        emit_event,
    )

    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    _events_subscribers.append(q)

    emit_event("heartbeat", {})
    emit_event("heartbeat", {})
    emit_event("heartbeat", {})  # queue is full at this point -> subscriber dropped

    assert q not in _events_subscribers


# ---------------------------------------------------------------------------
# Existing channels fan onto the bus in addition to their own stream
# ---------------------------------------------------------------------------


def test_approvals_event_fans_onto_bus():
    """emit_approvals_event() also lands on the multiplexed bus as type=approval."""
    from butlers.api.routers.approvals import emit_approvals_event
    from butlers.api.routers.events import _events_ring

    emit_approvals_event("created", "action-1", butler="home", tool_name="notify")

    assert len(_events_ring) == 1
    bus_event = _events_ring[0]
    assert bus_event["type"] == "approval"
    assert bus_event["data"]["kind"] == "created"
    assert bus_event["data"]["approval_id"] == "action-1"
    assert bus_event["data"]["butler"] == "home"


def test_spend_event_fans_onto_bus():
    """emit_spend_event() also lands on the multiplexed bus as type=spend."""
    from butlers.api.routers.events import _events_ring
    from butlers.api.routers.spend import emit_spend_event

    emit_spend_event(
        {
            "kind": "call",
            "butler": "atlas",
            "model": "claude-haiku-35-20241022",
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": 0.0001,
            "session_id": "sess-1",
        }
    )

    assert len(_events_ring) == 1
    bus_event = _events_ring[0]
    assert bus_event["type"] == "spend"
    assert bus_event["data"]["butler"] == "atlas"
    assert bus_event["data"]["cost_usd"] == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# New choke points: audit-log errors -> "issue"
# ---------------------------------------------------------------------------


async def test_audit_append_error_emits_issue_event():
    """audit.append(result='error') fans an 'issue' event onto the bus."""
    from butlers.api.routers.audit import append
    from butlers.api.routers.events import _events_ring

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=42)

    row_id = await append(
        pool,
        "home",
        "session.failed",
        target="session:abc",
        result="error",
        error="boom",
    )

    assert row_id == 42
    assert len(_events_ring) == 1
    bus_event = _events_ring[0]
    assert bus_event["type"] == "issue"
    assert bus_event["data"]["action"] == "session.failed"
    assert bus_event["data"]["error"] == "boom"


async def test_audit_append_success_does_not_emit_issue_event():
    """audit.append(result='success' or None) does not touch the issue bus."""
    from butlers.api.routers.audit import append
    from butlers.api.routers.events import _events_ring

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)

    await append(pool, "home", "spend.rule.create", result="success")
    await append(pool, "home", "spend.rule.create")

    assert len(_events_ring) == 0


# ---------------------------------------------------------------------------
# New choke points: session lifecycle -> "session"
# ---------------------------------------------------------------------------


class _FakeSessionPool:
    """Fake asyncpg pool for session_create/session_complete tests."""

    def __init__(self, *, return_id: Any) -> None:
        self._return_id = return_id

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._return_id


async def test_session_create_emits_session_started_event():
    from butlers.api.routers.events import _events_ring
    from butlers.core.sessions import session_create

    expected_id = uuid.uuid4()
    pool = _FakeSessionPool(return_id=expected_id)

    result = await session_create(
        pool,
        prompt="hello",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
        butler_name="home",
    )

    assert result == expected_id
    assert len(_events_ring) == 1
    bus_event = _events_ring[0]
    assert bus_event["type"] == "session"
    assert bus_event["data"]["phase"] == "started"
    assert bus_event["data"]["butler"] == "home"
    assert bus_event["data"]["session_id"] == str(expected_id)


async def test_session_complete_emits_session_ended_event():
    from butlers.api.routers.events import _events_ring
    from butlers.core.sessions import session_complete

    session_id = uuid.uuid4()
    pool = _FakeSessionPool(return_id=session_id)

    await session_complete(
        pool,
        session_id,
        output="done",
        tool_calls=[],
        duration_ms=1234,
        success=True,
        butler_name="atlas",
    )

    assert len(_events_ring) == 1
    bus_event = _events_ring[0]
    assert bus_event["type"] == "session"
    assert bus_event["data"]["phase"] == "ended"
    assert bus_event["data"]["butler"] == "atlas"
    assert bus_event["data"]["success"] is True
    assert bus_event["data"]["duration_ms"] == 1234
