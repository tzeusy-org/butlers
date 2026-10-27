"""Unit tests for butlers.fleet_events.publish_fleet_event (bu-01r64.1).

Covers the daemon-side publish half of the cross-process NOTIFY/LISTEN
bridge documented in RFC 0022:
- Publishes a JSON envelope via ``SELECT pg_notify(channel, payload)``
- Bounds publication; safely isolated SQL failures are logged and swallowed
- Propagates cancellation and errors that leave caller transaction health unknown
- Accepts exactly 7800 encoded bytes and rejects the first oversized payload
- Refuses (rather than crashes) on non-JSON-serializable data
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
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


class _FakeConnection(_FakePool, asyncpg.Connection):
    """A connection-shaped lifecycle seam; real SQL health is verified with Postgres."""

    def __init__(self):
        _FakePool.__init__(self)
        self.isolation = MagicMock()
        self.isolation.start = AsyncMock()
        self.isolation.commit = AsyncMock()
        self.isolation.rollback = AsyncMock()

    def transaction(self):
        return self.isolation

    def __del__(self):
        # This seam deliberately owns no asyncpg protocol/transport.
        pass


# Spec: REQ-core-fleet-events-001
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


# Spec: REQ-core-fleet-events-001
async def test_publish_fleet_event_defaults_data_to_empty_dict():
    pool = _FakePool()

    ok = await publish_fleet_event(pool, "heartbeat")

    assert ok is True
    _, args = pool.execute_calls[0]
    envelope = json.loads(args[1])
    assert envelope == {"type": "heartbeat", "data": {}}


# Spec: REQ-core-fleet-events-002, REQ-core-fleet-events-007
@pytest.mark.parametrize("stalled", [False, True])
@pytest.mark.parametrize("failure_stage", ["query", "pool-acquisition"])
async def test_publish_fleet_event_swallows_notify_failure(monkeypatch, stalled, failure_stage):
    from butlers import fleet_events

    monkeypatch.setattr(fleet_events, "_PUBLISH_TIMEOUT_S", 0.02, raising=False)
    pool = _FakePool(raise_on_execute=RuntimeError("connection lost"))
    released = asyncio.Event()
    if stalled:

        async def execute(*args):
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        pool.execute = execute
    if failure_stage == "pool-acquisition":
        pool = MagicMock(spec=asyncpg.Pool)
        acquire = pool.acquire.return_value
        if stalled:
            acquire.__aenter__.side_effect = execute
        else:
            acquire.__aenter__.side_effect = RuntimeError("pool unavailable")

    ok = await asyncio.wait_for(
        publish_fleet_event(pool, "session", {"phase": "ended"}), timeout=0.3
    )

    assert ok is False
    if stalled:
        assert released.is_set()


async def test_publish_fleet_event_swallows_missing_execute_method():
    """A pool/object that doesn't even implement execute() (e.g. a bare mock
    used by unrelated unit tests) must not blow up call sites that added an
    additive publish_fleet_event() call."""

    class _NoExecute:
        pass

    ok = await publish_fleet_event(_NoExecute(), "session", {"phase": "started"})

    assert ok is False


# Spec: REQ-core-fleet-events-002
@pytest.mark.parametrize("encoded_size", [7800, 7801])
async def test_publish_fleet_event_drops_oversized_payload(encoded_size):
    pool = _FakePool()
    # Default JSON escaping makes the non-ASCII character six encoded bytes.
    data = {"blob": "é"}
    overhead = len(json.dumps({"type": "notification", "data": data}).encode("utf-8"))
    data["blob"] += "x" * (encoded_size - overhead)
    assert len(json.dumps({"type": "notification", "data": data}).encode("utf-8")) == encoded_size

    ok = await publish_fleet_event(pool, "notification", data)

    assert ok is (encoded_size == 7800)
    assert len(pool.execute_calls) == (1 if ok else 0)


# Spec: REQ-core-fleet-events-002
async def test_publish_fleet_event_drops_non_serializable_data():
    pool = _FakePool()

    class _Unserializable:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    ok = await publish_fleet_event(pool, "spend", {"bad": _Unserializable()})

    assert ok is False
    assert pool.execute_calls == []


# Spec: REQ-core-fleet-events-002, REQ-core-fleet-events-007
@pytest.mark.parametrize("failure", [None, RuntimeError("SQL failure"), "stall", "cancel"])
@pytest.mark.parametrize("resource", ["caller-connection", "pool"])
async def test_publish_fleet_event_isolates_connection_and_cancellation(
    monkeypatch, failure, resource
):
    from butlers import fleet_events

    monkeypatch.setattr(fleet_events, "_PUBLISH_TIMEOUT_S", 0.02, raising=False)
    connection = _FakeConnection()
    transaction = connection.isolation
    started = asyncio.Event()

    async def execute(*args, **kwargs):
        started.set()
        if failure in ("stall", "cancel"):
            await asyncio.Event().wait()
        elif failure is not None:
            raise failure
        return "SELECT 1"

    connection.execute = AsyncMock(side_effect=execute)
    publisher = connection
    if resource == "pool":
        publisher = MagicMock(spec=asyncpg.Pool)
        publisher.acquire.return_value.__aenter__.return_value = connection
    task = asyncio.create_task(publish_fleet_event(publisher, "session", {}))
    try:
        await asyncio.wait_for(started.wait(), timeout=0.3)
    except TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await asyncio.wait_for(task, timeout=0.3) is (failure is None)
    if resource == "pool":
        publisher.acquire.return_value.__aexit__.assert_awaited_once()
        transaction.start.assert_not_awaited()
        return
    transaction.start.assert_awaited_once()
    if failure is None:
        transaction.commit.assert_awaited_once()
        transaction.rollback.assert_not_awaited()
    else:
        transaction.rollback.assert_awaited_once()
        transaction.commit.assert_not_awaited()


# Spec: REQ-core-fleet-events-007
@pytest.mark.parametrize("unsafe_stage", ["start", "rollback", "commit"])
@pytest.mark.parametrize("stalled", [False, True])
async def test_publish_fleet_event_propagates_transaction_safety_failure(
    monkeypatch, unsafe_stage, stalled
):
    from butlers import fleet_events

    monkeypatch.setattr(fleet_events, "_PUBLISH_TIMEOUT_S", 0.02, raising=False)
    connection = _FakeConnection()
    transaction = connection.isolation
    safety_failure = RuntimeError("transaction safety unavailable")
    if stalled:

        async def hang():
            await asyncio.Event().wait()

        getattr(transaction, unsafe_stage).side_effect = hang
    else:
        getattr(transaction, unsafe_stage).side_effect = safety_failure
    connection.execute = AsyncMock(
        side_effect=RuntimeError("SQL failure") if unsafe_stage == "rollback" else None
    )

    with pytest.raises(TimeoutError if stalled else RuntimeError) as caught:
        await asyncio.wait_for(publish_fleet_event(connection, "session", {}), timeout=0.3)
    if not stalled:
        assert caught.value is safety_failure
