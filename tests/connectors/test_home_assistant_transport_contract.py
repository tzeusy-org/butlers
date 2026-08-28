"""Transport-readiness contracts for the Home Assistant connector.

These tests cover the failure mode observed in the dev stack: Home Assistant
accepted the WebSocket connection, but a ``state_changed`` subscription timed
out and the connector still announced the transport as healthy.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.home_assistant import HAConnector, HAConnectorConfig, HAWebSocketClient

pytestmark = pytest.mark.unit


def _client(**overrides: Any) -> HAWebSocketClient:
    return HAWebSocketClient(
        ha_base_url="http://homeassistant.test:8123",
        ha_access_token="token-for-test-only",
        dispatch=AsyncMock(),
        reconnect_jitter=0.0,
        **overrides,
    )


@pytest.mark.asyncio
async def test_subscriptions_are_not_ready_when_state_changed_ack_times_out() -> None:
    client = _client()
    client._connected = True
    requested: list[str] = []

    async def respond(command: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        del timeout
        requested.append(command["event_type"])
        if command["event_type"] == "state_changed":
            raise TimeoutError("state_changed subscription timed out")
        return {}

    client._ws_command = AsyncMock(side_effect=respond)

    ready = await client._subscribe_events()

    assert requested == ["state_changed", "automation_triggered", "call_service"]
    assert ready is False
    assert client._subscriptions_ready is False


@pytest.mark.asyncio
async def test_all_subscription_acknowledgements_mark_transport_ready() -> None:
    client = _client()
    client._connected = True
    client._ws_command = AsyncMock(return_value={})

    ready = await client._subscribe_events()

    assert ready is True
    assert client._subscriptions_ready is True


@pytest.mark.asyncio
async def test_reconnect_subscription_failure_does_not_report_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected_callbacks: list[bool] = []
    reconnect_failures: list[bool] = []
    client = _client()
    client._on_connected = lambda: connected_callbacks.append(True)

    def on_reconnect_failed() -> None:
        reconnect_failures.append(True)
        client._shutdown = True

    client._on_reconnect_failed = on_reconnect_failed

    async def fake_sleep(_delay: float) -> None:
        return None

    async def fake_connect() -> None:
        client._connected = True

    async def fake_subscribe() -> bool:
        return False

    async def fake_close() -> None:
        client._connected = False

    client._connect = fake_connect
    client._subscribe_events = fake_subscribe
    client._close_connection = fake_close
    client._start_message_loop = lambda: None
    client._start_ping_task = lambda: None

    import butlers.connectors.home_assistant as home_assistant

    # Keep the test deterministic without changing the production retry delay.
    monkeypatch.setattr(home_assistant.asyncio, "sleep", fake_sleep)
    await client._reconnect_loop()

    assert connected_callbacks == []
    assert reconnect_failures == [True]


def test_health_requires_subscription_readiness_after_websocket_auth() -> None:
    connector = HAConnector(HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"))
    connector._starting = False
    connector._ws_connected = True
    connector._subscriptions_ready = False

    state, message = connector._get_health_state()

    assert state == "degraded"
    assert "subscription" in (message or "").lower()


def test_disconnect_clears_subscription_readiness() -> None:
    connector = HAConnector(HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"))
    connector._starting = False
    connector._ws_connected = True
    connector._subscriptions_ready = True

    connector.on_ws_disconnected()

    assert connector._subscriptions_ready is False
    assert connector._get_health_state()[0] == "degraded"
