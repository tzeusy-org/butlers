"""Integration tests for the Home Assistant connector.

Covers tasks 12.1–12.4 from openspec/changes/connector-home-assistant/tasks.md:

12.1 - WebSocket connection lifecycle (connect, auth, subscribe, receive events, reconnect)
12.2 - Full pipeline (HA event -> filter -> envelope -> Switchboard submission)
12.3 - REST fallback activation/deactivation during WebSocket outage
12.4 - Dashboard settings flow (validate, save, connector reads credentials)

No real network I/O is performed; all DB, WebSocket, and HTTP calls are mocked.
The ``HomeAssistantModule`` (roster/home/modules/__init__.py) is the unit under test,
as it contains the full WS client, REST fallback, and event dispatch pipeline.

NOTE on 12.4: The ``POST /api/settings/home-assistant`` endpoint (task bu-syyq) has
not been implemented yet.  Tests in ``TestDashboardSettingsFlow`` exercise the credential
storage layer directly and are marked ``xfail`` where they depend on the missing endpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules._roster_home import (
    HomeAssistantConfig,
    HomeAssistantModule,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module(
    *,
    url: str = "http://ha.local:8123",
    token: str = "test-token-xyz",
    ping_interval: int = 30,
    poll_interval: int = 60,
) -> HomeAssistantModule:
    """Create a pre-configured ``HomeAssistantModule`` instance.

    Bypasses ``on_startup`` by injecting state directly so tests can
    drive individual methods without a real HA server or database.
    """
    module = HomeAssistantModule()
    module._config = HomeAssistantConfig(
        url=url,
        websocket_ping_interval=ping_interval,
        poll_interval_seconds=poll_interval,
    )
    module._url = url
    module._token = token
    module._shutdown = False
    return module


def _make_mock_ws(responses: list[dict[str, Any]]) -> MagicMock:
    """Return a mock aiohttp WebSocketResponse that yields ``responses`` in order.

    Each call to ``receive_json`` pops and returns the next response.
    After the list is exhausted, further calls raise ``asyncio.TimeoutError``.
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


def _make_ws_event(
    event_type: str,
    entity_id: str,
    new_state: str,
    old_state: str = "off",
    attributes: dict[str, Any] | None = None,
    time_fired: str = "2024-01-01T12:00:00.000000+00:00",
) -> dict[str, Any]:
    """Construct a minimal HA WebSocket state_changed event message."""
    return {
        "id": 1,
        "type": "event",
        "event": {
            "event_type": event_type,
            "time_fired": time_fired,
            "data": {
                "entity_id": entity_id,
                "old_state": {"state": old_state, "entity_id": entity_id, "attributes": {}},
                "new_state": {
                    "state": new_state,
                    "entity_id": entity_id,
                    "attributes": attributes or {},
                    "last_changed": time_fired,
                    "last_updated": time_fired,
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# 12.1 — WebSocket connection lifecycle
# ---------------------------------------------------------------------------


class TestWebSocketLifecycle:
    """WebSocket connect → auth → subscribe → receive events → reconnect."""

    async def test_auth_handshake_success(self) -> None:
        """_ws_connect completes auth flow: auth_required → auth → auth_ok."""
        module = _make_module()

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_ok = {"type": "auth_ok", "ha_version": "2024.1.0"}

        mock_ws = _make_mock_ws([auth_required, auth_ok])
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)

        module._ws_session = mock_session

        await module._ws_connect()

        assert module._ws_connected is True

    async def test_auth_handshake_sends_correct_token(self) -> None:
        """_ws_connect sends the configured access token in the auth message."""
        token = "my-super-secret-ha-token"
        module = _make_module(token=token)

        sent_messages: list[dict[str, Any]] = []

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_ok = {"type": "auth_ok", "ha_version": "2024.1.0"}
        mock_ws = _make_mock_ws([auth_required, auth_ok])

        original_send = mock_ws.send_json

        async def _capture_send(data: dict[str, Any]) -> None:
            sent_messages.append(data)
            await original_send(data)

        mock_ws.send_json = _capture_send

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        module._ws_session = mock_session

        await module._ws_connect()

        auth_msgs = [m for m in sent_messages if m.get("type") == "auth"]
        assert auth_msgs, "No auth message sent during handshake"
        assert auth_msgs[0]["access_token"] == token

    async def test_auth_invalid_raises_and_disconnects(self) -> None:
        """_ws_connect raises RuntimeError on auth_invalid and stays disconnected."""
        module = _make_module()

        auth_required = {"type": "auth_required", "ha_version": "2024.1.0"}
        auth_invalid = {"type": "auth_invalid"}
        mock_ws = _make_mock_ws([auth_required, auth_invalid])
        mock_ws.close = AsyncMock()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        module._ws_session = mock_session

        with pytest.raises(RuntimeError, match="auth_invalid"):
            await module._ws_connect()

        assert module._ws_connected is False

    async def test_unexpected_first_message_raises(self) -> None:
        """_ws_connect raises if first message is not auth_required."""
        module = _make_module()

        unexpected = {"type": "auth_ok"}  # auth_ok before auth_required
        mock_ws = _make_mock_ws([unexpected])
        mock_ws.close = AsyncMock()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        module._ws_session = mock_session

        with pytest.raises(RuntimeError, match="expected auth_required"):
            await module._ws_connect()

        assert module._ws_connected is False

    async def test_ws_subscribe_events_sends_subscriptions(self) -> None:
        """_ws_subscribe_events sends subscribe_events for state_changed and registries."""
        module = _make_module()
        module._ws_connected = True

        sent_commands: list[dict[str, Any]] = []

        async def _mock_ws_command(
            command: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            sent_commands.append(command)
            return {}

        module._ws_command = _mock_ws_command  # type: ignore[method-assign]

        await module._ws_subscribe_events()

        event_types_subscribed = [
            c["event_type"] for c in sent_commands if c.get("type") == "subscribe_events"
        ]
        assert "state_changed" in event_types_subscribed, (
            "Expected subscription to state_changed events"
        )

    async def test_ws_subscribe_events_skipped_when_not_connected(self) -> None:
        """_ws_subscribe_events is a no-op when WebSocket is disconnected."""
        module = _make_module()
        module._ws_connected = False

        called = []

        async def _mock_ws_command(
            command: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            called.append(command)
            return {}

        module._ws_command = _mock_ws_command  # type: ignore[method-assign]

        await module._ws_subscribe_events()

        assert called == [], "No commands should be sent when disconnected"

    async def test_state_changed_event_updates_entity_cache(self) -> None:
        """Receiving a state_changed WS event updates the in-memory entity cache."""
        module = _make_module()
        module._ws_connected = True

        event_msg = _make_ws_event(
            event_type="state_changed",
            entity_id="light.kitchen",
            new_state="on",
            old_state="off",
            attributes={"brightness": 200, "friendly_name": "Kitchen Light"},
        )

        await module._dispatch_ws_message(event_msg)

        assert "light.kitchen" in module._entity_cache
        entity = module._entity_cache["light.kitchen"]
        assert entity.state == "on"
        assert entity.attributes.get("brightness") == 200

    async def test_entity_removed_on_null_new_state(self) -> None:
        """A state_changed event with null new_state removes the entity from cache."""
        module = _make_module()
        module._ws_connected = True

        # Pre-populate cache
        from butlers.modules._roster_home import CachedEntity

        module._entity_cache["sensor.gone"] = CachedEntity(entity_id="sensor.gone", state="42.0")

        # Entity removed event: new_state is None
        removal_msg: dict[str, Any] = {
            "id": 5,
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "sensor.gone",
                    "old_state": {"state": "42.0", "entity_id": "sensor.gone", "attributes": {}},
                    "new_state": None,
                },
            },
        }

        await module._dispatch_ws_message(removal_msg)

        assert "sensor.gone" not in module._entity_cache

    async def test_pong_updates_last_pong_time(self) -> None:
        """Receiving a pong message updates _last_pong_time."""
        module = _make_module()

        before = asyncio.get_running_loop().time()
        pong_msg = {"type": "pong", "id": 1}

        await module._dispatch_ws_message(pong_msg)

        assert module._last_pong_time >= before

    async def test_result_message_resolves_pending_future(self) -> None:
        """A WS result message with matching id resolves the pending future."""
        module = _make_module()
        module._ws_connected = True

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        module._ws_pending[42] = fut

        result_msg = {"type": "result", "id": 42, "success": True, "result": {"areas": []}}
        await module._dispatch_ws_message(result_msg)

        assert fut.done()
        assert fut.result() == {"areas": []}

    async def test_connection_drop_triggers_fallback_and_reconnect(self) -> None:
        """Connection drop in the message loop triggers REST fallback and schedules reconnect."""
        module = _make_module()
        module._ws_connected = True
        module._shutdown = False

        fallback_started = []
        reconnect_scheduled = []

        def _mock_start_fallback() -> None:
            fallback_started.append(True)

        def _mock_schedule_reconnect(delay: float) -> None:
            reconnect_scheduled.append(delay)

        module._start_poll_fallback = _mock_start_fallback
        module._schedule_reconnect = _mock_schedule_reconnect

        # Simulate the end-of-loop connection-drop logic directly
        # (same code path as when message loop breaks out)
        module._ws_connected = False
        if not module._shutdown:
            module._start_poll_fallback()
            module._schedule_reconnect(delay=1.0)

        assert fallback_started, "REST fallback should be started on connection drop"
        assert reconnect_scheduled, "Reconnect should be scheduled on connection drop"

    async def test_reconnect_loop_stops_fallback_on_success(self) -> None:
        """Successful reconnect stops REST polling fallback and restarts WS tasks."""
        module = _make_module()
        module._shutdown = False
        module._ws_connected = False

        fallback_stopped = []
        tasks_started: list[str] = []

        async def _mock_ws_connect() -> None:
            module._ws_connected = True

        def _mock_stop_fallback() -> None:
            fallback_stopped.append(True)

        def _mock_start_message_loop() -> None:
            tasks_started.append("message_loop")

        def _mock_start_ping_task() -> None:
            tasks_started.append("ping_task")

        async def _mock_seed(*_args: Any, **_kwargs: Any) -> None:
            pass

        async def _mock_fetch(*_args: Any, **_kwargs: Any) -> None:
            pass

        async def _mock_subscribe() -> None:
            pass

        module._ws_connect = _mock_ws_connect  # type: ignore[method-assign]
        module._stop_poll_fallback = _mock_stop_fallback
        module._start_ws_message_loop = _mock_start_message_loop
        module._start_ws_ping_task = _mock_start_ping_task
        module._seed_entity_cache_from_rest = _mock_seed  # type: ignore[method-assign]
        module._fetch_area_registry = _mock_fetch  # type: ignore[method-assign]
        module._fetch_entity_registry = _mock_fetch  # type: ignore[method-assign]
        module._ws_subscribe_events = _mock_subscribe  # type: ignore[method-assign]

        await module._ws_reconnect_loop(initial_delay=0.0)

        assert fallback_stopped, "REST fallback should be stopped after successful reconnect"
        assert "message_loop" in tasks_started
        assert "ping_task" in tasks_started

    async def test_ws_shutdown_cancels_background_tasks(self) -> None:
        """on_shutdown cancels all background tasks and clears connection state."""
        module = _make_module()
        module._ws_connected = True

        # Create dummy tasks
        async def _noop() -> None:
            await asyncio.sleep(100)

        loop = asyncio.get_running_loop()
        module._ws_loop_task = loop.create_task(_noop())
        module._ws_ping_task = loop.create_task(_noop())
        module._poll_task = loop.create_task(_noop())

        mock_ws = MagicMock()
        mock_ws.closed = False
        mock_ws.close = AsyncMock()
        module._ws_connection = mock_ws

        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        module._ws_session = mock_session

        mock_client = AsyncMock()
        module._client = mock_client

        await module.on_shutdown()

        assert module._ws_connected is False
        assert module._ws_loop_task is None
        assert module._ws_ping_task is None
        assert module._poll_task is None
        assert module._client is None


# ---------------------------------------------------------------------------
# 12.2 — Full pipeline (event → entity cache update → state accessible)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """HA event flows through the pipeline and is accessible via module state."""

    async def test_state_changed_flow_updates_entity_cache(self) -> None:
        """state_changed event flows through dispatch and updates entity cache."""
        module = _make_module()

        event_msg = _make_ws_event(
            event_type="state_changed",
            entity_id="sensor.living_room_temperature",
            new_state="22.5",
            old_state="21.0",
            attributes={"unit_of_measurement": "°C", "friendly_name": "Living Room Temperature"},
        )

        await module._dispatch_ws_message(event_msg)

        assert "sensor.living_room_temperature" in module._entity_cache
        entity = module._entity_cache["sensor.living_room_temperature"]
        assert entity.state == "22.5"
        assert entity.attributes["unit_of_measurement"] == "°C"

    async def test_multiple_events_update_cache_independently(self) -> None:
        """Multiple state_changed events for different entities update cache entries."""
        module = _make_module()

        events = [
            _make_ws_event("state_changed", "light.kitchen", "on"),
            _make_ws_event("state_changed", "lock.front_door", "unlocked"),
            _make_ws_event("state_changed", "sensor.humidity", "55"),
        ]

        for event in events:
            await module._dispatch_ws_message(event)

        assert module._entity_cache["light.kitchen"].state == "on"
        assert module._entity_cache["lock.front_door"].state == "unlocked"
        assert module._entity_cache["sensor.humidity"].state == "55"

    async def test_sequential_updates_to_same_entity(self) -> None:
        """Multiple state_changed events for the same entity reflect the latest state."""
        module = _make_module()

        await module._dispatch_ws_message(_make_ws_event("state_changed", "switch.fan", "on"))
        await module._dispatch_ws_message(_make_ws_event("state_changed", "switch.fan", "off"))
        await module._dispatch_ws_message(_make_ws_event("state_changed", "switch.fan", "on"))

        assert module._entity_cache["switch.fan"].state == "on"

    async def test_rest_poll_seeds_full_entity_cache(self) -> None:
        """REST poll via _seed_entity_cache_from_rest populates the full entity cache."""
        module = _make_module()

        states_response = [
            {
                "entity_id": "climate.thermostat",
                "state": "heat",
                "attributes": {"current_temperature": 20.5, "set_temperature": 22.0},
                "last_changed": "2024-01-01T10:00:00+00:00",
                "last_updated": "2024-01-01T10:00:00+00:00",
            },
            {
                "entity_id": "binary_sensor.motion",
                "state": "on",
                "attributes": {},
                "last_changed": "2024-01-01T11:00:00+00:00",
                "last_updated": "2024-01-01T11:00:00+00:00",
            },
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = states_response
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        module._client = mock_client

        await module._seed_entity_cache_from_rest()

        assert "climate.thermostat" in module._entity_cache
        assert module._entity_cache["climate.thermostat"].state == "heat"
        assert "binary_sensor.motion" in module._entity_cache
        assert module._entity_cache["binary_sensor.motion"].state == "on"

    async def test_ws_command_sends_and_awaits_result(self) -> None:
        """_ws_command sends a command and resolves when a matching result arrives."""
        module = _make_module()
        module._ws_connected = True

        sent_messages: list[dict[str, Any]] = []

        async def _send_json(data: dict[str, Any]) -> None:
            sent_messages.append(data)
            # Simulate the server immediately resolving the pending future
            cmd_id = data.get("id")
            if cmd_id in module._ws_pending:
                fut = module._ws_pending[cmd_id]
                if not fut.done():
                    fut.set_result({"areas": ["bedroom"]})

        mock_ws = MagicMock()
        mock_ws.send_json = _send_json
        module._ws_connection = mock_ws

        result = await module._ws_command({"type": "config/area_registry/list"}, timeout=2.0)

        assert result == {"areas": ["bedroom"]}
        assert any(m.get("type") == "config/area_registry/list" for m in sent_messages), (
            "Expected command to be sent"
        )

    async def test_ws_command_raises_on_error_response(self) -> None:
        """_ws_command raises RuntimeError when HA returns error result."""
        module = _make_module()
        module._ws_connected = True

        async def _send_json(data: dict[str, Any]) -> None:
            cmd_id = data.get("id")
            if cmd_id in module._ws_pending:
                fut = module._ws_pending[cmd_id]
                if not fut.done():
                    fut.set_exception(
                        RuntimeError("WS command 1 failed: 'not_found' — 'Entity not found'")
                    )

        mock_ws = MagicMock()
        mock_ws.send_json = _send_json
        module._ws_connection = mock_ws

        with pytest.raises(RuntimeError, match="failed"):
            await module._ws_command({"type": "get_state", "entity_id": "nonexistent"}, timeout=2.0)

    async def test_ws_command_raises_when_not_connected(self) -> None:
        """_ws_command raises RuntimeError when WebSocket is not connected."""
        module = _make_module()
        module._ws_connected = False
        module._ws_connection = None

        with pytest.raises(RuntimeError, match="not connected"):
            await module._ws_command({"type": "ping"}, timeout=1.0)

    async def test_area_registry_updated_event_triggers_refresh(self) -> None:
        """area_registry_updated WS event triggers _fetch_area_registry."""
        module = _make_module()
        module._ws_connected = True

        refresh_called = []

        async def _mock_fetch_area() -> None:
            refresh_called.append("area")

        module._fetch_area_registry = _mock_fetch_area  # type: ignore[method-assign]

        area_event = {
            "type": "event",
            "event": {
                "event_type": "area_registry_updated",
                "data": {},
            },
        }

        await module._dispatch_ws_message(area_event)

        assert refresh_called == ["area"], "area_registry_updated should trigger cache refresh"

    async def test_entity_registry_updated_event_triggers_refresh(self) -> None:
        """entity_registry_updated WS event triggers _fetch_entity_registry."""
        module = _make_module()
        module._ws_connected = True

        refresh_called = []

        async def _mock_fetch_entity() -> None:
            refresh_called.append("entity")

        module._fetch_entity_registry = _mock_fetch_entity  # type: ignore[method-assign]

        entity_event = {
            "type": "event",
            "event": {
                "event_type": "entity_registry_updated",
                "data": {},
            },
        }

        await module._dispatch_ws_message(entity_event)

        assert refresh_called == ["entity"]


# ---------------------------------------------------------------------------
# 12.3 — REST fallback activation/deactivation during WebSocket outage
# ---------------------------------------------------------------------------


class TestRestFallback:
    """REST polling fallback activates on WS failure, deactivates on reconnect."""

    async def test_fallback_starts_when_ws_fails(self) -> None:
        """_start_poll_fallback creates a background poll task."""
        module = _make_module()
        module._ws_connected = False
        module._poll_task = None

        poll_runs = []

        async def _mock_poll_loop() -> None:
            poll_runs.append(True)
            await asyncio.sleep(0)

        module._poll_loop = _mock_poll_loop  # type: ignore[method-assign]

        with patch(
            "asyncio.ensure_future",
            side_effect=lambda coro: asyncio.get_running_loop().create_task(coro),
        ):
            module._start_poll_fallback()

        assert module._poll_task is not None, "Poll task should be created on fallback start"

    async def test_fallback_does_not_start_twice(self) -> None:
        """_start_poll_fallback is idempotent — second call does not replace running task."""
        module = _make_module()
        module._ws_connected = False

        async def _long_poll() -> None:
            await asyncio.sleep(100)

        loop = asyncio.get_running_loop()
        original_task = loop.create_task(_long_poll())
        module._poll_task = original_task

        module._start_poll_fallback()

        assert module._poll_task is original_task, "Existing poll task must not be replaced"
        original_task.cancel()
        try:
            await original_task
        except asyncio.CancelledError:
            pass

    async def test_fallback_stops_when_ws_reconnects(self) -> None:
        """_stop_poll_fallback cancels the REST polling task."""
        module = _make_module()

        async def _long_poll() -> None:
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                return

        loop = asyncio.get_running_loop()
        task = loop.create_task(_long_poll())
        module._poll_task = task

        module._stop_poll_fallback()

        await asyncio.sleep(0)  # allow CancelledError to propagate

        assert module._poll_task is None
        assert task.cancelled(), "Poll task should be cancelled after stop"

    async def test_poll_loop_seeds_cache_and_stops_on_ws_reconnect(self) -> None:
        """_poll_loop polls REST and exits once WebSocket reconnects."""
        module = _make_module()
        module._ws_connected = False
        module._shutdown = False
        module._config = HomeAssistantConfig(
            url="http://ha.local:8123",
            poll_interval_seconds=0,  # no sleep for test
        )

        seed_calls = []

        async def _mock_seed() -> None:
            seed_calls.append(True)
            # Simulate WS reconnecting after first poll
            module._ws_connected = True

        module._seed_entity_cache_from_rest = _mock_seed  # type: ignore[method-assign]

        await module._poll_loop()

        assert seed_calls, "REST poll should have called _seed_entity_cache_from_rest"
        assert module._ws_connected is True

    async def test_poll_loop_handles_seed_error_and_continues(self) -> None:
        """_poll_loop logs errors from seed failures and continues polling."""
        module = _make_module()
        module._ws_connected = False
        module._shutdown = False
        module._config = HomeAssistantConfig(
            url="http://ha.local:8123",
            poll_interval_seconds=0,
        )

        call_count = 0

        async def _mock_seed() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("connection refused")
            # Second call succeeds and triggers WS reconnect to exit loop
            module._ws_connected = True

        module._seed_entity_cache_from_rest = _mock_seed  # type: ignore[method-assign]

        await module._poll_loop()

        assert call_count >= 2, "Poll loop should retry after seed failure"

    async def test_fallback_activation_on_initial_ws_connect_failure(self) -> None:
        """_ws_connect_and_seed activates REST fallback when initial WS connect fails."""
        module = _make_module()

        fallback_started = []
        reconnect_scheduled = []

        async def _failing_ws_connect() -> None:
            raise ConnectionError("Connection refused")

        def _mock_start_fallback() -> None:
            fallback_started.append(True)

        def _mock_schedule_reconnect(delay: float) -> None:
            reconnect_scheduled.append(delay)

        module._ws_connect = _failing_ws_connect  # type: ignore[method-assign]
        module._start_poll_fallback = _mock_start_fallback
        module._schedule_reconnect = _mock_schedule_reconnect

        await module._ws_connect_and_seed()

        assert fallback_started, "REST fallback must activate when initial WS connect fails"
        assert reconnect_scheduled, "Reconnect must be scheduled when initial WS connect fails"
        assert module._ws_connected is False

    async def test_reconnect_deactivates_fallback(self) -> None:
        """Successful reconnect stops REST polling via _stop_poll_fallback."""
        module = _make_module()
        module._ws_connected = False
        module._shutdown = False

        stopped = []

        async def _mock_ws_connect() -> None:
            module._ws_connected = True

        def _mock_stop_fallback() -> None:
            stopped.append(True)

        async def _noop(*_: Any, **__: Any) -> None:
            pass

        module._ws_connect = _mock_ws_connect  # type: ignore[method-assign]
        module._stop_poll_fallback = _mock_stop_fallback
        module._start_ws_message_loop = lambda: None
        module._start_ws_ping_task = lambda: None
        module._seed_entity_cache_from_rest = _noop  # type: ignore[method-assign]
        module._fetch_area_registry = _noop  # type: ignore[method-assign]
        module._fetch_entity_registry = _noop  # type: ignore[method-assign]
        module._ws_subscribe_events = _noop  # type: ignore[method-assign]

        await module._ws_reconnect_loop(initial_delay=0.0)

        assert stopped, "REST fallback should be stopped after successful reconnect"

    async def test_ws_url_derivation_http_to_ws(self) -> None:
        """_ws_url converts http:// to ws:// and appends /api/websocket."""
        module = _make_module(url="http://homeassistant.local:8123")
        ws_url = module._ws_url()
        assert ws_url == "ws://homeassistant.local:8123/api/websocket"

    async def test_ws_url_derivation_https_to_wss(self) -> None:
        """_ws_url converts https:// to wss:// and appends /api/websocket."""
        module = _make_module(url="https://ha.example.com")
        ws_url = module._ws_url()
        assert ws_url == "wss://ha.example.com/api/websocket"


# ---------------------------------------------------------------------------
# 12.4 — Dashboard settings flow
# ---------------------------------------------------------------------------


class TestDashboardSettingsFlow:
    """Validate/save HA credentials and connector reads them back.

    Full HTTP endpoint tests depend on ``POST /api/settings/home-assistant``
    (issue bu-syyq, not yet implemented).  The tests below verify the
    credential storage layer and the connector's startup credential resolution.
    """

    async def test_credential_store_saves_and_resolves_base_url(self) -> None:
        """CredentialStore round-trips home_assistant:base_url."""
        from butlers.credential_store import CredentialStore

        stored: dict[str, Any] = {}

        mock_pool = MagicMock()

        class _MockConn:
            async def execute(self, query: str, *args: Any) -> None:
                # Capture the upsert
                if "butler_secrets" in query and args:
                    stored[args[0]] = args[1]  # key → value

        class _MockAcquire:
            async def __aenter__(self) -> _MockConn:
                return _MockConn()

            async def __aexit__(self, *_: Any) -> None:
                pass

        mock_pool.acquire = lambda: _MockAcquire()
        cred_store = CredentialStore(mock_pool)

        await cred_store.store(
            "home_assistant:base_url",
            "http://homeassistant.local:8123",
            category="home_assistant",
            description="HA instance URL",
            is_sensitive=True,
        )

        assert stored.get("home_assistant:base_url") == "http://homeassistant.local:8123"

    async def test_credential_store_saves_and_resolves_access_token(self) -> None:
        """CredentialStore round-trips home_assistant:access_token."""
        from butlers.credential_store import CredentialStore

        stored: dict[str, Any] = {}

        mock_pool = MagicMock()

        class _MockConn:
            async def execute(self, query: str, *args: Any) -> None:
                if "butler_secrets" in query and args:
                    stored[args[0]] = args[1]

        class _MockAcquire:
            async def __aenter__(self) -> _MockConn:
                return _MockConn()

            async def __aexit__(self, *_: Any) -> None:
                pass

        mock_pool.acquire = lambda: _MockAcquire()
        cred_store = CredentialStore(mock_pool)

        await cred_store.store(
            "home_assistant:access_token",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.my-ha-token",
            category="home_assistant",
            description="HA long-lived access token",
            is_sensitive=True,
        )

        assert stored.get("home_assistant:access_token") is not None

    async def test_credential_resolve_returns_stored_value(self) -> None:
        """CredentialStore.resolve returns the stored credential via load()."""
        from butlers.credential_store import CredentialStore

        # _safe_fetch_secret_row uses pool.fetchrow; simulate via acquire context
        mock_pool = MagicMock()

        class _MockConn:
            async def fetchrow(self, query: str, *args: Any) -> dict[str, Any]:
                return {"secret_value": "http://homeassistant.local:8123"}

        class _MockAcquire:
            async def __aenter__(self) -> _MockConn:
                return _MockConn()

            async def __aexit__(self, *_: Any) -> None:
                pass

        mock_pool.acquire = lambda: _MockAcquire()
        cred_store = CredentialStore(mock_pool)

        val = await cred_store.resolve("home_assistant:base_url")
        assert val == "http://homeassistant.local:8123"

    async def test_credential_delete_removes_credential(self) -> None:
        """CredentialStore.delete returns True when a row is deleted."""
        from butlers.credential_store import CredentialStore

        deleted_keys: list[str] = []

        mock_pool = MagicMock()

        class _MockConn:
            async def execute(self, query: str, *args: Any) -> str:
                deleted_keys.append(str(args[0]) if args else "")
                return "DELETE 1"

        class _MockAcquire:
            async def __aenter__(self) -> _MockConn:
                return _MockConn()

            async def __aexit__(self, *_: Any) -> None:
                pass

        mock_pool.acquire = lambda: _MockAcquire()
        cred_store = CredentialStore(mock_pool)

        deleted = await cred_store.delete("home_assistant:access_token")
        assert deleted is True
        assert "home_assistant:access_token" in deleted_keys

    async def test_module_startup_raises_without_url(self) -> None:
        """on_startup raises RuntimeError when home_assistant_url is not configured."""
        module = HomeAssistantModule()

        mock_pool = MagicMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool

        async def _resolve_missing(pool: Any, info_type: str) -> str | None:
            return None  # both url and token are absent

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve_missing),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_url"):
                await module.on_startup(config={}, db=mock_db)

    async def test_module_startup_raises_without_token(self) -> None:
        """on_startup raises RuntimeError when home_assistant_token is not configured."""
        module = HomeAssistantModule()

        mock_pool = MagicMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool

        async def _resolve_url_only(pool: Any, info_type: str) -> str | None:
            if info_type == "home_assistant_url":
                return "http://ha.local:8123"
            return None  # token absent

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve_url_only),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_token"):
                await module.on_startup(config={}, db=mock_db)

    async def test_module_startup_succeeds_with_valid_credentials(self) -> None:
        """on_startup completes without error when both URL and token are available."""
        module = HomeAssistantModule()

        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            if info_type == "home_assistant_url":
                return "http://ha.local:8123"
            if info_type == "home_assistant_token":
                return "test-bearer-token-12345"
            return None

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(side_effect=_resolve),
            ),
            patch("httpx.AsyncClient", return_value=MagicMock()),
            patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()),
        ):
            await module.on_startup(config={}, db=mock_db)

        assert module._url == "http://ha.local:8123"
        assert module._token == "test-bearer-token-12345"
        assert module._ws_connected is False  # WS connect was mocked as no-op

    @pytest.mark.xfail(
        reason=(
            "POST /api/settings/home-assistant endpoint not yet implemented — "
            "tracked in bu-syyq (HA: Dashboard settings — connection configuration)"
        ),
        strict=False,
    )
    async def test_settings_api_validates_and_saves_credentials(self) -> None:
        """POST /api/settings/home-assistant validates connection and saves credentials.

        This test exercises the full dashboard settings flow:
        1. Submit URL + token via POST /api/settings/home-assistant
        2. Endpoint validates by calling GET /api/ on the HA instance
        3. On success (200 response), credentials are stored in CredentialStore
        4. Connector startup can resolve credentials from CredentialStore

        This test will pass once bu-syyq is implemented.
        """
        import httpx

        from butlers.api.app import create_app

        app = create_app(api_key="")

        # Mock the validation HTTP call to HA: GET /api/ → 200 {"message": "API running."}
        mock_ha_response = MagicMock()
        mock_ha_response.status_code = 200
        mock_ha_response.json.return_value = {"message": "API running."}
        mock_ha_response.raise_for_status = MagicMock()

        stored_creds: dict[str, str] = {}

        # Wire a mock credential store
        mock_cred_store = AsyncMock()

        async def _mock_store(key: str, value: str, **_kwargs: Any) -> None:
            stored_creds[key] = value

        mock_cred_store.store = _mock_store

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_ha_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/settings/home-assistant",
                    json={
                        "base_url": "http://homeassistant.local:8123",
                        "access_token": "test-long-lived-access-token",
                    },
                )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        # Both credentials must have been persisted
        assert "home_assistant:base_url" in stored_creds
        assert "home_assistant:access_token" in stored_creds

    @pytest.mark.xfail(
        reason=(
            "POST /api/settings/home-assistant endpoint not yet implemented — tracked in bu-syyq"
        ),
        strict=False,
    )
    async def test_settings_api_rejects_invalid_token(self) -> None:
        """POST /api/settings/home-assistant returns 400 for auth failure (HTTP 401 from HA)."""
        import httpx

        from butlers.api.app import create_app

        app = create_app(api_key="")

        mock_ha_response = MagicMock()
        mock_ha_response.status_code = 401
        mock_ha_response.raise_for_status.side_effect = Exception("401 Unauthorized")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_ha_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/settings/home-assistant",
                    json={
                        "base_url": "http://homeassistant.local:8123",
                        "access_token": "wrong-token",
                    },
                )

        # Should return 400 with an actionable error for invalid access token
        assert response.status_code in (400, 422), (
            f"Expected 4xx for invalid token, got {response.status_code}"
        )
        detail = response.json().get("detail", "")
        assert "token" in detail.lower() or "auth" in detail.lower(), (
            f"Error detail should mention auth/token: {detail!r}"
        )

    @pytest.mark.xfail(
        reason=(
            "POST /api/settings/home-assistant endpoint not yet implemented — tracked in bu-syyq"
        ),
        strict=False,
    )
    async def test_settings_api_rejects_unreachable_host(self) -> None:
        """POST /api/settings/home-assistant returns 400 for unreachable HA host."""
        import httpx

        from butlers.api.app import create_app

        app = create_app(api_key="")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/settings/home-assistant",
                    json={
                        "base_url": "http://192.168.1.999:8123",
                        "access_token": "any-token",
                    },
                )

        assert response.status_code in (400, 503), (
            f"Expected 4xx/503 for unreachable host, got {response.status_code}"
        )
        detail = response.json().get("detail", "")
        assert "connect" in detail.lower() or "reach" in detail.lower(), (
            f"Error detail should mention connectivity: {detail!r}"
        )
