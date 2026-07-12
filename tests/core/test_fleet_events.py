"""Unit tests for butlers.fleet_events.publish_fleet_event (bu-01r64.1).

Covers the daemon-side publish half of the cross-process NOTIFY/LISTEN
bridge documented in RFC 0022:
- Publishes a JSON envelope via ``SELECT pg_notify(channel, payload)``
- Never raises — a NOTIFY failure is logged and swallowed
- Refuses to send a payload over Postgres's 8000-byte NOTIFY hard cap
- Refuses (rather than crashes) on non-JSON-serializable data
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from butlers.fleet_events import FLEET_EVENTS_CHANNEL, publish_fleet_event

pytestmark = pytest.mark.unit


class _FakePool:
    """Fake asyncpg pool/connection capturing execute() calls."""

    def __init__(self, *, raise_on_execute: Exception | None = None) -> None:
        self._raise_on_execute = raise_on_execute
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        return "SELECT 1"


async def test_publish_fleet_event_sends_pg_notify_with_channel_and_json_payload():
    pool = _FakePool()

    ok = await publish_fleet_event(pool, "session", {"phase": "started", "session_id": "abc"})

    assert ok is True
    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "pg_notify" in query
    assert args[0] == FLEET_EVENTS_CHANNEL
    envelope = json.loads(args[1])
    assert envelope == {
        "type": "session",
        "data": {"phase": "started", "session_id": "abc"},
    }


async def test_publish_fleet_event_defaults_data_to_empty_dict():
    pool = _FakePool()

    ok = await publish_fleet_event(pool, "heartbeat")

    assert ok is True
    _, args = pool.execute_calls[0]
    envelope = json.loads(args[1])
    assert envelope == {"type": "heartbeat", "data": {}}


async def test_publish_fleet_event_swallows_notify_failure():
    pool = _FakePool(raise_on_execute=RuntimeError("connection lost"))

    ok = await publish_fleet_event(pool, "session", {"phase": "ended"})

    assert ok is False  # never raises; caller's real work must not be affected


async def test_publish_fleet_event_swallows_missing_execute_method():
    """A pool/object that doesn't even implement execute() (e.g. a bare mock
    used by unrelated unit tests) must not blow up call sites that added an
    additive publish_fleet_event() call."""

    class _NoExecute:
        pass

    ok = await publish_fleet_event(_NoExecute(), "session", {"phase": "started"})

    assert ok is False


async def test_publish_fleet_event_drops_oversized_payload():
    pool = _FakePool()
    # Comfortably exceeds Postgres's 8000-byte NOTIFY payload cap.
    huge_data = {"blob": "x" * 8500}

    ok = await publish_fleet_event(pool, "notification", huge_data)

    assert ok is False
    assert pool.execute_calls == []  # never attempted the NOTIFY


async def test_publish_fleet_event_drops_non_serializable_data():
    pool = _FakePool()

    class _Unserializable:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    ok = await publish_fleet_event(pool, "spend", {"bad": _Unserializable()})

    assert ok is False
    assert pool.execute_calls == []
