"""The Telegram connector retracts stale owner cards on a timer, not only after a tap."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig

pytestmark = pytest.mark.unit

_API = "http://dashboard-api:41200"


def _connector(*, api: str | None = _API, token: str | None = "test-only-token") -> Any:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=api,
            approval_callback_secret="test-only-secret",
            approval_callback_connector_token=token,
            owner_delivery_sweep_interval_s=0.01,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    connector._mcp_client = AsyncMock()
    connector._http_client = AsyncMock()
    return connector


async def _until(predicate: Any) -> None:
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never held")


async def test_sweep_keeps_retracting_without_any_tap_until_stopped() -> None:
    connector = _connector()
    drain = AsyncMock(return_value=0)
    connector._drain_owner_delivery_retractions = drain

    connector._start_owner_delivery_sweep()
    await _until(lambda: drain.await_count >= 3)
    await connector.stop()
    settled = drain.await_count
    await asyncio.sleep(0.05)

    assert drain.await_count == settled


async def test_sweep_survives_a_failed_drain() -> None:
    connector = _connector()
    drain = AsyncMock(side_effect=[RuntimeError("dashboard down"), 0, 0])
    connector._drain_owner_delivery_retractions = drain

    connector._start_owner_delivery_sweep()
    await _until(lambda: drain.await_count >= 2)
    await connector.stop()


@pytest.mark.parametrize(
    "api, token", [(None, "test-only-token"), (_API, None)], ids=["no api", "no credential"]
)
async def test_sweep_needs_the_dashboard_route_and_its_credential(api, token) -> None:
    connector = _connector(api=api, token=token)

    connector._start_owner_delivery_sweep()

    assert connector._owner_delivery_sweep_task is None
    await connector.stop()


async def test_starting_twice_runs_one_sweep() -> None:
    connector = _connector()
    connector._drain_owner_delivery_retractions = AsyncMock(return_value=0)

    connector._start_owner_delivery_sweep()
    first = connector._owner_delivery_sweep_task
    connector._start_owner_delivery_sweep()

    assert connector._owner_delivery_sweep_task is first
    await connector.stop()


async def test_a_tap_and_the_sweep_never_attempt_the_same_cards_at_once() -> None:
    """Overlapping drains would edit one card twice and receipt a false failure."""
    connector = _connector()
    inside = 0
    most = 0
    release = asyncio.Event()

    async def retract(_item: dict[str, Any]) -> tuple[bool, str | None]:
        nonlocal inside, most
        inside += 1
        most = max(most, inside)
        await release.wait()
        inside -= 1
        return True, None

    listing = MagicMock(status_code=200)
    listing.json.return_value = {"data": [{"presentation_id": "p1"}]}
    connector._http_client.get = AsyncMock(return_value=listing)
    connector._retract_owner_card = retract

    drains = [asyncio.create_task(connector._drain_owner_delivery_retractions()) for _ in range(2)]
    await _until(lambda: inside == 1)
    await asyncio.sleep(0.02)
    release.set()
    await asyncio.gather(*drains)

    assert most == 1


@pytest.mark.parametrize("value, expected", [(None, 60.0), ("15", 15.0)])
def test_sweep_interval_comes_from_the_environment(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:bot:1")
    if value is None:
        monkeypatch.delenv("CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S", raising=False)
    else:
        monkeypatch.setenv("CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S", value)

    assert TelegramBotConnectorConfig.from_env().owner_delivery_sweep_interval_s == expected
