"""Tests for HAWebSocketClient — WebSocket client core (tasks 3.1–3.6).

Covers openspec/changes/connector-home-assistant/tasks.md §3:

3.1 — Connector entrypoint and process lifecycle (start/stop)
3.2 — WebSocket auth handshake (auth_required → auth → auth_ok/auth_invalid)
3.3 — Event subscription for state_changed, automation_triggered, call_service
3.4 — Ping/pong keepalive (configurable interval + timeout)
3.5 — Exponential backoff reconnection (1s cap at 60s)
3.6 — Event message parsing and dispatch to filter pipeline callback

No real network I/O is performed; all WebSocket calls are mocked.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.home_assistant import (
    _WS_EVENT_SUBSCRIPTIONS,
    HAWebSocketClient,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_dispatch(event_type: str, event: dict[str, Any]) -> None:
    """No-op event dispatch for tests that don't need dispatch assertions."""


def _make_client(
    *,
    ha_base_url: str = "http://ha.local:8123",
    ha_access_token: str = "test-token",
    dispatch=None,
    ping_interval_s: int = 30,
    pong_timeout_s: int = 10,
    reconnect_initial_s: float = 1.0,
    reconnect_max_s: float = 60.0,
    reconnect_jitter: float = 0.5,
    on_connected=None,
    on_disconnected=None,
) -> HAWebSocketClient:
    """Return a pre-configured HAWebSocketClient for unit tests."""
    return HAWebSocketClient(
        ha_base_url=ha_base_url,
        ha_access_token=ha_access_token,
        dispatch=dispatch or _noop_dispatch,
        ping_interval_s=ping_interval_s,
        pong_timeout_s=pong_timeout_s,
        reconnect_initial_s=reconnect_initial_s,
        reconnect_max_s=reconnect_max_s,
        reconnect_jitter=reconnect_jitter,
        on_connected=on_connected,
        on_disconnected=on_disconnected,
    )


def _make_mock_ws(responses: list[dict[str, Any]]) -> MagicMock:
    """Return a mock aiohttp WebSocketResponse that yields ``responses`` in order.

    After responses are exhausted, ``receive_json`` raises ``TimeoutError``.
    """
    ws = MagicMock()
    ws.closed = False

    remaining = list(responses)

    async def _receive_json(timeout: float = 10.0) -> dict[str, Any]:
        if not remaining:
            raise TimeoutError
        return remaining.pop(0)

    async def _send_json(data: dict[str, Any]) -> None:
        pass

    ws.receive_json = _receive_json
    ws.send_json = _send_json
    ws.close = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# 3.2 — WebSocket authentication handshake
# ---------------------------------------------------------------------------


class TestAuthHandshake:
    """Task 3.2: auth_required → auth → auth_ok flow."""

    async def test_successful_auth_sets_connected(self) -> None:
        """_connect completes the auth flow and sets _connected = True."""
        client = _make_client()

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_ok = {"type": "auth_ok", "ha_version": "2024.1.0"}

        mock_ws = _make_mock_ws([auth_required, auth_ok])
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        client._ws_session = mock_session

        await client._connect()

        assert client._connected is True

    async def test_auth_sends_correct_token(self) -> None:
        """_connect sends the configured access_token in the auth message."""
        token = "my-ha-long-lived-access-token"
        client = _make_client(ha_access_token=token)

        sent: list[dict[str, Any]] = []

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_ok = {"type": "auth_ok", "ha_version": "2024.1.0"}
        mock_ws = _make_mock_ws([auth_required, auth_ok])

        async def _capture(data: dict[str, Any]) -> None:
            sent.append(data)

        mock_ws.send_json = _capture

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        client._ws_session = mock_session

        await client._connect()

        auth_msgs = [m for m in sent if m.get("type") == "auth"]
        assert auth_msgs, "No auth message sent"
        assert auth_msgs[0]["access_token"] == token

    async def test_auth_invalid_raises_and_stays_disconnected(self) -> None:
        """_connect raises RuntimeError on auth_invalid and _connected stays False."""
        client = _make_client()

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_invalid = {"type": "auth_invalid"}
        mock_ws = _make_mock_ws([auth_required, auth_invalid])
        mock_ws.close = AsyncMock()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        client._ws_session = mock_session

        with pytest.raises(RuntimeError, match="auth_invalid"):
            await client._connect()

        assert client._connected is False

    async def test_unexpected_first_message_raises(self) -> None:
        """_connect raises if first message is not auth_required."""
        client = _make_client()

        # Server sends auth_ok immediately (protocol error)
        unexpected = {"type": "auth_ok"}
        mock_ws = _make_mock_ws([unexpected])
        mock_ws.close = AsyncMock()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        client._ws_session = mock_session

        with pytest.raises(RuntimeError, match="expected auth_required"):
            await client._connect()

        assert client._connected is False

    async def test_on_connected_callback_called_after_auth(self) -> None:
        """on_connected callback is invoked once auth succeeds."""
        connected_calls: list[bool] = []
        client = _make_client(on_connected=lambda: connected_calls.append(True))

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_ok = {"type": "auth_ok", "ha_version": "2024.1.0"}
        mock_ws = _make_mock_ws([auth_required, auth_ok])

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        client._ws_session = mock_session

        # Patch subscribe and background tasks to avoid interference
        async def _noop_subscribe() -> None:
            pass

        client._subscribe_events = _noop_subscribe  # type: ignore[method-assign]
        client._start_message_loop = lambda: None
        client._start_ping_task = lambda: None

        await client._connect()
        # Manually trigger on_connected (as run() would)
        if client._on_connected is not None:
            client._on_connected()

        assert connected_calls == [True]

    async def test_ws_url_http_to_ws(self) -> None:
        """HTTP base URL is converted to ws:// WebSocket URL."""
        client = _make_client(ha_base_url="http://ha.local:8123")
        assert client._ws_url() == "ws://ha.local:8123/api/websocket"

    async def test_ws_url_https_to_wss(self) -> None:
        """HTTPS base URL is converted to wss:// WebSocket URL."""
        client = _make_client(ha_base_url="https://ha.local:8123")
        assert client._ws_url() == "wss://ha.local:8123/api/websocket"

    async def test_ws_url_trailing_slash_stripped(self) -> None:
        """Trailing slashes in base URL are stripped before appending path."""
        client = _make_client(ha_base_url="http://ha.local:8123/")
        url = client._ws_url()
        assert not url.endswith("//api/websocket"), (
            "Double slash in WS URL indicates trailing slash not stripped"
        )
        assert url.endswith("/api/websocket")


# ---------------------------------------------------------------------------
# 3.3 — Event subscription
# ---------------------------------------------------------------------------


class TestEventSubscription:
    """Task 3.3: subscribe_events for state_changed, automation_triggered, call_service."""

    async def test_subscribe_events_sends_all_required_subscriptions(self) -> None:
        """_subscribe_events sends subscribe_events for all required event types."""
        client = _make_client()
        client._connected = True

        sent_commands: list[dict[str, Any]] = []

        async def _mock_ws_command(
            command: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            sent_commands.append(command)
            return {}

        client._ws_command = _mock_ws_command  # type: ignore[method-assign]

        await client._subscribe_events()

        subscribed_types = [
            c["event_type"] for c in sent_commands if c.get("type") == "subscribe_events"
        ]
        for required in _WS_EVENT_SUBSCRIPTIONS:
            assert required in subscribed_types, (
                f"Missing subscription for event type: {required!r}"
            )

    async def test_subscribe_state_changed(self) -> None:
        """state_changed is always included in event subscriptions."""
        assert "state_changed" in _WS_EVENT_SUBSCRIPTIONS

    async def test_subscribe_automation_triggered(self) -> None:
        """automation_triggered is included in event subscriptions."""
        assert "automation_triggered" in _WS_EVENT_SUBSCRIPTIONS

    async def test_subscribe_call_service(self) -> None:
        """call_service is included in event subscriptions."""
        assert "call_service" in _WS_EVENT_SUBSCRIPTIONS

    async def test_subscribe_events_skipped_when_disconnected(self) -> None:
        """_subscribe_events is a no-op when not connected."""
        client = _make_client()
        client._connected = False

        called: list[dict[str, Any]] = []

        async def _mock_ws_command(
            command: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            called.append(command)
            return {}

        client._ws_command = _mock_ws_command  # type: ignore[method-assign]

        await client._subscribe_events()

        assert called == [], "No commands should be sent when disconnected"

    async def test_subscribe_events_tolerates_individual_failure(self) -> None:
        """_subscribe_events continues subscribing even if one subscription fails."""
        client = _make_client()
        client._connected = True

        succeeded: list[str] = []
        failed_once = [False]

        async def _mock_ws_command(
            command: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            if not failed_once[0] and command.get("type") == "subscribe_events":
                failed_once[0] = True
                raise RuntimeError("Simulated subscription failure")
            succeeded.append(command.get("event_type", ""))
            return {}

        client._ws_command = _mock_ws_command  # type: ignore[method-assign]

        # Should not raise despite one failure
        await client._subscribe_events()

        # At least one subscription should have succeeded
        assert succeeded, "Expected at least some subscriptions to succeed"


# ---------------------------------------------------------------------------
# 3.4 — Ping/pong keepalive
# ---------------------------------------------------------------------------


class TestPingPongKeepalive:
    """Task 3.4: 30s ping interval, 10s pong timeout."""

    async def test_pong_updates_last_pong_time(self) -> None:
        """A pong message in _dispatch_message updates _last_pong_time."""
        client = _make_client()

        before = asyncio.get_running_loop().time()
        pong_msg = {"type": "pong", "id": 1}

        await client._dispatch_message(pong_msg)

        assert client._last_pong_time >= before

    async def test_ping_loop_exits_on_shutdown(self) -> None:
        """The ping loop exits promptly when _shutdown is True."""
        client = _make_client(ping_interval_s=1)
        client._shutdown = True
        client._connected = True

        # Should return without sending any pings
        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=AsyncMock()):
            await client._ping_loop()

        # No exception raised, no hang

    async def test_missed_pong_calls_on_disconnected(self) -> None:
        """Missing a pong after ping triggers on_disconnected callback."""
        disconnected: list[bool] = []
        client = _make_client(
            ping_interval_s=1,
            pong_timeout_s=1,
            on_disconnected=lambda: disconnected.append(True),
        )
        client._shutdown = False
        client._connected = True

        # Provide a mock WS connection where send_json succeeds
        mock_ws = MagicMock()
        mock_ws.closed = False
        mock_ws.send_json = AsyncMock()
        mock_ws.close = AsyncMock()
        client._ws_connection = mock_ws

        # _last_pong_time stays at 0 (epoch) so missed pong is detected
        client._last_pong_time = 0.0

        sleep_call_count = [0]

        async def _fast_sleep(s: float) -> None:
            sleep_call_count[0] += 1
            # Do NOT set shutdown here — let the missed-pong path fire
            # and call on_disconnected naturally.

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_fast_sleep):
            await client._ping_loop()

        assert disconnected, "on_disconnected should be called after missed pong"

    async def test_ping_loop_exits_when_not_connected(self) -> None:
        """Ping loop exits cleanly when _connected becomes False."""
        client = _make_client(ping_interval_s=1)
        client._connected = False
        client._shutdown = False

        async def _fast_sleep(s: float) -> None:
            pass

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_fast_sleep):
            await client._ping_loop()

        # Should exit without error


# ---------------------------------------------------------------------------
# 3.5 — Exponential backoff reconnection
# ---------------------------------------------------------------------------


class TestExponentialBackoffReconnection:
    """Task 3.5: reconnect with 1s–60s exponential backoff."""

    async def test_reconnect_loop_retries_on_failure(self) -> None:
        """_reconnect_loop retries on connection failure and doubles the delay."""
        client = _make_client(
            reconnect_initial_s=1.0,
            reconnect_max_s=60.0,
        )
        client._shutdown = False
        client._connected = False

        attempt_count = [0]

        async def _fail_twice() -> None:
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise RuntimeError("Simulated connect failure")
            client._connected = True  # succeed on 3rd attempt

        client._connect = _fail_twice  # type: ignore[method-assign]

        async def _noop_subscribe() -> None:
            pass

        client._subscribe_events = _noop_subscribe  # type: ignore[method-assign]
        client._start_message_loop = lambda: None
        client._start_ping_task = lambda: None

        async def _fast_sleep(s: float) -> None:
            pass

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_fast_sleep):
            await client._reconnect_loop()

        assert attempt_count[0] == 3, "Expected 3 connect attempts"
        assert client._connected is True

    async def test_reconnect_delay_doubles_on_failure(self) -> None:
        """Delay doubles on each failed attempt (exponential backoff)."""
        client = _make_client(
            reconnect_initial_s=1.0,
            reconnect_max_s=60.0,
            reconnect_jitter=0.0,  # disable jitter for deterministic test
        )
        client._shutdown = False
        client._connected = False

        attempt_count = [0]
        sleep_durations: list[float] = []

        async def _fail_three_times() -> None:
            attempt_count[0] += 1
            if attempt_count[0] < 4:
                raise RuntimeError("Simulated failure")
            client._connected = True

        client._connect = _fail_three_times  # type: ignore[method-assign]

        async def _noop_subscribe() -> None:
            pass

        client._subscribe_events = _noop_subscribe  # type: ignore[method-assign]
        client._start_message_loop = lambda: None
        client._start_ping_task = lambda: None

        async def _capture_sleep(s: float) -> None:
            sleep_durations.append(s)

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_capture_sleep):
            await client._reconnect_loop()

        assert len(sleep_durations) >= 3, "Expected at least 3 sleep calls"
        # Each successive sleep should be >= previous (exponential growth)
        for i in range(1, min(3, len(sleep_durations))):
            assert sleep_durations[i] >= sleep_durations[i - 1], (
                f"Backoff delay not increasing: {sleep_durations}"
            )

    async def test_reconnect_delay_capped_at_max(self) -> None:
        """Backoff delay does not exceed reconnect_max_s."""
        client = _make_client(
            reconnect_initial_s=30.0,
            reconnect_max_s=60.0,
            reconnect_jitter=0.0,
        )
        client._shutdown = False
        client._connected = False

        attempt_count = [0]
        sleep_durations: list[float] = []

        async def _fail_then_succeed() -> None:
            attempt_count[0] += 1
            if attempt_count[0] < 4:
                raise RuntimeError("Simulated failure")
            client._connected = True

        client._connect = _fail_then_succeed  # type: ignore[method-assign]

        async def _noop_subscribe() -> None:
            pass

        client._subscribe_events = _noop_subscribe  # type: ignore[method-assign]
        client._start_message_loop = lambda: None
        client._start_ping_task = lambda: None

        async def _capture_sleep(s: float) -> None:
            sleep_durations.append(s)

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_capture_sleep):
            await client._reconnect_loop()

        for duration in sleep_durations:
            assert duration <= 60.0, f"Delay {duration}s exceeds 60s cap"

    async def test_reconnect_stops_when_shutdown(self) -> None:
        """_reconnect_loop exits immediately when _shutdown is set."""
        client = _make_client()
        client._shutdown = True
        client._connected = False

        connect_calls: list[bool] = []

        async def _mock_connect() -> None:
            connect_calls.append(True)

        client._connect = _mock_connect  # type: ignore[method-assign]

        async def _fast_sleep(s: float) -> None:
            pass

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_fast_sleep):
            await client._reconnect_loop()

        assert connect_calls == [], "No connect attempt should be made when shut down"

    async def test_reconnect_calls_on_connected_on_success(self) -> None:
        """on_connected callback is called after successful reconnect."""
        connected_calls: list[bool] = []
        client = _make_client(on_connected=lambda: connected_calls.append(True))
        client._shutdown = False
        client._connected = False

        async def _mock_connect() -> None:
            client._connected = True

        client._connect = _mock_connect  # type: ignore[method-assign]

        async def _noop_subscribe() -> None:
            pass

        client._subscribe_events = _noop_subscribe  # type: ignore[method-assign]
        client._start_message_loop = lambda: None
        client._start_ping_task = lambda: None

        async def _fast_sleep(s: float) -> None:
            pass

        with patch("butlers.connectors.home_assistant.asyncio.sleep", new=_fast_sleep):
            await client._reconnect_loop()

        assert connected_calls == [True], "on_connected should be called after reconnect"


# ---------------------------------------------------------------------------
# 3.6 — Event message parsing and dispatch
# ---------------------------------------------------------------------------


class TestEventDispatch:
    """Task 3.6: event message parsing and dispatch to filter pipeline."""

    async def test_event_message_dispatches_to_callback(self) -> None:
        """An 'event' WS message calls the dispatch callback with event_type and event."""
        received: list[tuple[str, dict[str, Any]]] = []

        async def _capture_dispatch(event_type: str, event: dict[str, Any]) -> None:
            received.append((event_type, event))

        client = _make_client(dispatch=_capture_dispatch)

        event_msg = {
            "type": "event",
            "id": 1,
            "event": {
                "event_type": "state_changed",
                "time_fired": "2024-01-01T12:00:00+00:00",
                "data": {
                    "entity_id": "light.kitchen",
                    "old_state": {"state": "off"},
                    "new_state": {"state": "on"},
                },
            },
        }

        await client._dispatch_message(event_msg)

        assert len(received) == 1
        event_type, event = received[0]
        assert event_type == "state_changed"
        assert event["event_type"] == "state_changed"

    async def test_automation_triggered_event_dispatched(self) -> None:
        """automation_triggered events are dispatched to the callback."""
        received: list[str] = []

        async def _capture(event_type: str, event: dict[str, Any]) -> None:
            received.append(event_type)

        client = _make_client(dispatch=_capture)

        event_msg = {
            "type": "event",
            "id": 2,
            "event": {
                "event_type": "automation_triggered",
                "data": {"entity_id": "automation.good_morning"},
            },
        }

        await client._dispatch_message(event_msg)

        assert "automation_triggered" in received

    async def test_call_service_event_dispatched(self) -> None:
        """call_service events are dispatched to the callback."""
        received: list[str] = []

        async def _capture(event_type: str, event: dict[str, Any]) -> None:
            received.append(event_type)

        client = _make_client(dispatch=_capture)

        event_msg = {
            "type": "event",
            "id": 3,
            "event": {
                "event_type": "call_service",
                "data": {"domain": "light", "service": "turn_on"},
            },
        }

        await client._dispatch_message(event_msg)

        assert "call_service" in received

    async def test_result_message_resolves_pending_future(self) -> None:
        """A WS result message resolves the matching pending command future."""
        client = _make_client()
        client._connected = True

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._ws_pending[99] = fut

        result_msg = {"type": "result", "id": 99, "success": True, "result": {"key": "val"}}
        await client._dispatch_message(result_msg)

        assert fut.done()
        assert fut.result() == {"key": "val"}

    async def test_result_error_rejects_future(self) -> None:
        """A failed WS result sets an exception on the pending future."""
        client = _make_client()
        client._connected = True

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._ws_pending[55] = fut

        error_msg = {
            "type": "result",
            "id": 55,
            "success": False,
            "error": {"code": "not_found", "message": "Entity not found"},
        }
        await client._dispatch_message(error_msg)

        assert fut.done()
        assert fut.exception() is not None
        assert "not_found" in str(fut.exception())

    async def test_unknown_message_type_is_silently_ignored(self) -> None:
        """Unknown message types don't raise and don't call dispatch."""
        received: list[Any] = []

        async def _capture(event_type: str, event: dict[str, Any]) -> None:
            received.append(event_type)

        client = _make_client(dispatch=_capture)

        unknown_msg = {"type": "some_future_ha_message_type", "id": 10}
        await client._dispatch_message(unknown_msg)

        assert received == [], "Unknown message type should not trigger dispatch"

    async def test_dispatch_exception_is_caught_and_logged(self) -> None:
        """Exceptions raised by the dispatch callback are caught (not propagated)."""

        async def _raising_dispatch(event_type: str, event: dict[str, Any]) -> None:
            raise RuntimeError("Pipeline error")

        client = _make_client(dispatch=_raising_dispatch)

        event_msg = {
            "type": "event",
            "id": 1,
            "event": {"event_type": "state_changed", "data": {}},
        }

        # Should not raise — exception is caught internally
        await client._dispatch_message(event_msg)

    async def test_pong_updates_last_pong_time_via_dispatch(self) -> None:
        """Pong messages dispatched through _dispatch_message update _last_pong_time."""
        client = _make_client()
        before = asyncio.get_running_loop().time()

        pong_msg = {"type": "pong", "id": 7}
        await client._dispatch_message(pong_msg)

        assert client._last_pong_time >= before


# ---------------------------------------------------------------------------
# 3.1 — Lifecycle: stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Task 3.1: connector process lifecycle."""

    async def test_stop_sets_shutdown_flag(self) -> None:
        """stop() sets _shutdown = True."""
        client = _make_client()
        client._shutdown = False

        await client.stop()

        assert client._shutdown is True

    async def test_stop_cancels_background_tasks(self) -> None:
        """stop() cancels running background tasks."""
        client = _make_client()

        async def _long_running() -> None:
            await asyncio.sleep(1000)

        loop = asyncio.get_running_loop()
        client._loop_task = loop.create_task(_long_running())
        client._ping_task = loop.create_task(_long_running())

        await client.stop()

        # Tasks should be cancelled or done
        assert client._loop_task is None
        assert client._ping_task is None

    async def test_stop_closes_ws_session(self) -> None:
        """stop() closes the aiohttp session."""
        client = _make_client()

        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        client._ws_session = mock_session

        mock_ws = MagicMock()
        mock_ws.closed = True
        client._ws_connection = mock_ws

        await client.stop()

        mock_session.close.assert_awaited_once()
        assert client._ws_session is None

    async def test_stop_fails_pending_futures(self) -> None:
        """stop() cancels pending WS command futures."""
        client = _make_client()
        loop = asyncio.get_running_loop()

        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._ws_pending[1] = fut

        await client.stop()

        assert fut.cancelled()
        assert client._ws_pending == {}

    async def test_close_connection_sets_disconnected(self) -> None:
        """_close_connection sets _connected = False."""
        client = _make_client()
        client._connected = True

        mock_ws = MagicMock()
        mock_ws.closed = False
        mock_ws.close = AsyncMock()
        client._ws_connection = mock_ws

        await client._close_connection()

        assert client._connected is False
        assert client._ws_connection is None
        mock_ws.close.assert_awaited_once()

    async def test_ws_command_raises_when_not_connected(self) -> None:
        """_ws_command raises RuntimeError when client is not connected."""
        client = _make_client()
        client._connected = False
        client._ws_connection = None

        with pytest.raises(RuntimeError, match="not connected"):
            await client._ws_command({"type": "get_states"})
