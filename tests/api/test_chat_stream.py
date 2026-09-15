"""Unit tests for butlers.api.chat_stream (bu-0ynlk.7).

Covers the publish-side contract (mirrors tests/core/test_fleet_events.py)
and the LISTEN-side envelope parsing, without opening a real Postgres
connection.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from butlers.api.chat_stream import (
    chat_stream_channel,
    open_chat_stream_listener,
    publish_chat_stream_event,
)

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


def test_chat_stream_channel_strips_hyphens_and_stays_under_the_identifier_limit():
    request_id = uuid4()
    channel = chat_stream_channel(request_id)

    assert "-" not in channel
    assert channel.startswith("dashboard_chat_stream_")
    assert len(channel.encode("utf-8")) <= 63


def test_chat_stream_channel_is_stable_for_the_same_request_id():
    request_id = uuid4()
    assert chat_stream_channel(request_id) == chat_stream_channel(str(request_id))


async def test_publish_chat_stream_event_sends_pg_notify_on_the_request_channel():
    pool = _FakePool()
    request_id = uuid4()

    ok = await publish_chat_stream_event(pool, request_id, "token", {"content": "hi"})

    assert ok is True
    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "pg_notify" in query
    assert args[0] == chat_stream_channel(request_id)
    envelope = json.loads(args[1])
    assert envelope == {"type": "token", "data": {"content": "hi"}}


async def test_publish_chat_stream_event_defaults_data_to_empty_dict():
    pool = _FakePool()
    request_id = uuid4()

    ok = await publish_chat_stream_event(pool, request_id, "reply_ready")

    assert ok is True
    _, args = pool.execute_calls[0]
    envelope = json.loads(args[1])
    assert envelope == {"type": "reply_ready", "data": {}}


async def test_publish_chat_stream_event_swallows_notify_failure():
    pool = _FakePool(raise_on_execute=RuntimeError("connection lost"))

    ok = await publish_chat_stream_event(pool, uuid4(), "token", {"content": "hi"})

    assert ok is False


async def test_publish_chat_stream_event_drops_oversized_payload():
    pool = _FakePool()
    huge_data = {"content": "x" * 8500}

    ok = await publish_chat_stream_event(pool, uuid4(), "token", huge_data)

    assert ok is False
    assert pool.execute_calls == []


async def test_publish_chat_stream_event_drops_non_serializable_data():
    pool = _FakePool()

    class _Unserializable:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    ok = await publish_chat_stream_event(pool, uuid4(), "token", {"bad": _Unserializable()})

    assert ok is False
    assert pool.execute_calls == []


async def test_open_chat_stream_listener_returns_none_when_connect_fails(monkeypatch):
    """The honest-fallback contract: a dedicated LISTEN connection failure
    must never raise -- it degrades the SSE generator to poll-only, exactly
    like a runtime that cannot stream at all."""
    import butlers.api.chat_stream as chat_stream_module

    async def _boom() -> Any:
        raise ConnectionRefusedError("no postgres here")

    monkeypatch.setattr(chat_stream_module, "_connect_listener", _boom)

    listener = await open_chat_stream_listener(uuid4())

    assert listener is None


async def test_open_chat_stream_listener_queues_and_returns_notify_payloads(monkeypatch):
    """A NOTIFY delivered on this request's channel is parsed and handed back
    from ``get()``; a NOTIFY on a different channel or malformed payload
    (never actually delivered by asyncpg's own channel-scoped dispatch, but
    defensive regardless) is dropped rather than raising."""
    import butlers.api.chat_stream as chat_stream_module

    request_id = uuid4()
    channel = chat_stream_channel(request_id)

    class _FakeConn:
        def __init__(self) -> None:
            self.listeners: dict[str, Any] = {}
            self.removed: list[str] = []
            self._closed = False

        async def add_listener(self, channel_name: str, callback: Any) -> None:
            self.listeners[channel_name] = callback

        async def remove_listener(self, channel_name: str, callback: Any) -> None:
            self.removed.append(channel_name)

        def is_closed(self) -> bool:
            return self._closed

        async def close(self) -> None:
            self._closed = True

    fake_conn = _FakeConn()

    async def _connect() -> Any:
        return fake_conn

    monkeypatch.setattr(chat_stream_module, "_connect_listener", _connect)

    listener = await open_chat_stream_listener(request_id)
    assert listener is not None

    callback = fake_conn.listeners[channel]
    callback(fake_conn, 1, channel, json.dumps({"type": "token", "data": {"content": "hi"}}))
    callback(fake_conn, 1, channel, "not json")  # dropped, not raised

    envelope = await listener.get(timeout=1.0)
    assert envelope == {"type": "token", "data": {"content": "hi"}}

    timed_out = await listener.get(timeout=0.01)
    assert timed_out is None

    await listener.aclose()
    assert fake_conn.removed == [channel]
    assert fake_conn.is_closed()
