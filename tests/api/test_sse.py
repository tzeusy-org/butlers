"""Tests for the SSE endpoint.

Issue: butlers-26h.14.5

Tests are split into two groups:
1. Unit tests for ``broadcast()`` — exercise the in-memory pub/sub directly.
2. Integration tests for ``_event_generator()`` — exercise the async generator
   with a mock ``Request`` object, verifying SSE formatting and event delivery.

HTTP-level streaming tests are intentionally avoided because
``BaseHTTPMiddleware`` buffers streaming responses in ASGI test transports,
and ``TestClient`` runs the ASGI app in a separate thread making cross-thread
asyncio.Queue notification unreliable.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from butlers.api.routers.sse import _SHUTDOWN, _event_generator, _subscribers, broadcast

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# broadcast() unit tests
# ---------------------------------------------------------------------------


class TestBroadcast:
    """Tests for the broadcast function."""

    def test_broadcast_to_empty_subscribers(self):
        """broadcast() should not raise when no subscribers."""
        broadcast("test", {"key": "value"})

    async def test_broadcast_delivers_to_subscriber(self):
        """broadcast() should deliver events to all subscribers."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        _subscribers.append(queue)
        try:
            broadcast("test_event", {"hello": "world"})
            event = queue.get_nowait()
            assert event["type"] == "test_event"
            assert event["data"] == {"hello": "world"}
            assert "timestamp" in event
        finally:
            _subscribers.remove(queue)

    async def test_broadcast_removes_full_queues(self):
        """broadcast() should remove subscribers with full queues."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        _subscribers.append(queue)
        try:
            # Fill the queue
            broadcast("e1", {})
            # This should remove the full queue
            broadcast("e2", {})
            assert queue not in _subscribers
        finally:
            if queue in _subscribers:
                _subscribers.remove(queue)

    async def test_broadcast_multiple_subscribers(self):
        """broadcast() should deliver to all connected subscribers."""
        q1: asyncio.Queue = asyncio.Queue(maxsize=256)
        q2: asyncio.Queue = asyncio.Queue(maxsize=256)
        _subscribers.append(q1)
        _subscribers.append(q2)
        try:
            broadcast("multi", {"n": 1})
            e1 = q1.get_nowait()
            e2 = q2.get_nowait()
            assert e1["type"] == "multi"
            assert e2["type"] == "multi"
            assert e1["data"] == {"n": 1}
            assert e2["data"] == {"n": 1}
        finally:
            if q1 in _subscribers:
                _subscribers.remove(q1)
            if q2 in _subscribers:
                _subscribers.remove(q2)

    async def test_broadcast_payload_structure(self):
        """broadcast() payload should have type, data, and timestamp."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        _subscribers.append(queue)
        try:
            broadcast("session_start", {"butler": "test", "session_id": "abc"})
            event = queue.get_nowait()
            assert set(event.keys()) == {"type", "data", "timestamp"}
            assert isinstance(event["timestamp"], float)
        finally:
            _subscribers.remove(queue)


# ---------------------------------------------------------------------------
# _event_generator() integration tests
# ---------------------------------------------------------------------------


def _mock_request(*, disconnected: bool = False) -> AsyncMock:
    """Create a mock Starlette Request with configurable is_disconnected()."""
    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=disconnected)
    return request


class TestEventGenerator:
    """Tests for the SSE event generator.

    Exercises the async generator directly with a mock Request, verifying
    SSE event formatting and the subscriber lifecycle.
    """

    async def test_initial_connected_event(self):
        """Generator should yield a 'connected' event as its first output."""
        request = _mock_request()
        gen = _event_generator(request)
        initial_count = len(_subscribers)

        first = await gen.__anext__()
        assert "event: connected" in first
        assert "data:" in first
        data_line = [line for line in first.split("\n") if line.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload == {"status": "ok"}

        # A subscriber should have been registered
        assert len(_subscribers) == initial_count + 1

        # Cleanup: close the generator
        await gen.aclose()

    async def test_subscriber_cleanup_on_close(self):
        """Closing the generator should remove the subscriber from the list."""
        request = _mock_request()
        gen = _event_generator(request)
        initial_count = len(_subscribers)

        await gen.__anext__()  # consume connected event
        assert len(_subscribers) == initial_count + 1

        await gen.aclose()
        assert len(_subscribers) == initial_count

    async def test_receives_broadcast_events(self):
        """Generator should yield SSE-formatted broadcast events."""
        request = _mock_request()
        gen = _event_generator(request)

        # Get connected event
        await gen.__anext__()

        # Broadcast an event
        broadcast("butler_status", {"butler": "health", "status": "online"})

        # Get the broadcast event
        event_text = await gen.__anext__()
        assert "event: butler_status" in event_text
        data_line = [line for line in event_text.split("\n") if line.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload["butler"] == "health"
        assert payload["status"] == "online"

        await gen.aclose()

    async def test_receives_session_start_event(self):
        """Generator should yield session_start events."""
        request = _mock_request()
        gen = _event_generator(request)

        await gen.__anext__()  # connected
        broadcast("session_start", {"butler": "health", "session_id": "s-42"})

        event_text = await gen.__anext__()
        assert "event: session_start" in event_text
        data_line = [line for line in event_text.split("\n") if line.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload["session_id"] == "s-42"

        await gen.aclose()

    async def test_receives_session_end_event(self):
        """Generator should yield session_end events."""
        request = _mock_request()
        gen = _event_generator(request)

        await gen.__anext__()  # connected
        broadcast("session_end", {"butler": "health", "session_id": "s-42", "duration_ms": 5000})

        event_text = await gen.__anext__()
        assert "event: session_end" in event_text
        data_line = [line for line in event_text.split("\n") if line.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload["session_id"] == "s-42"
        assert payload["duration_ms"] == 5000

        await gen.aclose()

    async def test_shutdown_sentinel_stops_generator(self):
        """The _SHUTDOWN sentinel should cause the generator to terminate."""
        request = _mock_request()
        gen = _event_generator(request)

        await gen.__anext__()  # connected

        # Find and send shutdown to the subscriber queue
        # (the last one added is ours)
        queue = _subscribers[-1]
        queue.put_nowait(_SHUTDOWN)

        # Generator should raise StopAsyncIteration
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    async def test_disconnected_client_stops_generator(self):
        """Generator should stop when request.is_disconnected() returns True."""
        request = _mock_request(disconnected=True)
        gen = _event_generator(request)

        await gen.__anext__()  # connected event is yielded before the loop check

        # Next iteration should detect disconnection and stop
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    async def test_sse_event_format(self):
        """Events should follow the SSE wire format: 'event: ...\ndata: ...\n\n'."""
        request = _mock_request()
        gen = _event_generator(request)

        connected = await gen.__anext__()
        # SSE format: "event: <type>\ndata: <json>\n\n"
        assert connected.startswith("event: connected\n")
        assert connected.endswith("\n\n")
        lines = connected.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")

        broadcast("test_type", {"key": "val"})
        event = await gen.__anext__()
        assert event.startswith("event: test_type\n")
        assert event.endswith("\n\n")

        await gen.aclose()

    async def test_multiple_events_in_sequence(self):
        """Generator should yield multiple broadcast events in order."""
        request = _mock_request()
        gen = _event_generator(request)

        await gen.__anext__()  # connected

        broadcast("butler_status", {"butler": "a", "status": "online"})
        broadcast("butler_status", {"butler": "b", "status": "offline"})
        broadcast("session_start", {"butler": "a", "session_id": "s-1"})

        e1 = await gen.__anext__()
        e2 = await gen.__anext__()
        e3 = await gen.__anext__()

        assert '"butler": "a"' in e1 and '"online"' in e1
        assert '"butler": "b"' in e2 and '"offline"' in e2
        assert '"session_id": "s-1"' in e3

        await gen.aclose()


# ---------------------------------------------------------------------------
# Router registration test
# ---------------------------------------------------------------------------


class TestSSERouterRegistration:
    """Verify the SSE router is properly registered in the full app."""

    async def test_events_endpoint_exists_in_full_app(self, app):
        """The /api/events endpoint should be registered in the main app."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/events" in routes
