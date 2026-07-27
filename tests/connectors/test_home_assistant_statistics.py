"""Tests for the shared Home Assistant recorder statistics client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.home_assistant_statistics import (
    HAStatisticsClient,
    HAStatisticsError,
    parse_statistics_change_series,
)

pytestmark = pytest.mark.unit


def _session_with_responses(*responses: dict) -> tuple[MagicMock, MagicMock]:
    websocket = MagicMock()
    websocket.receive_json = AsyncMock(side_effect=responses)
    websocket.send_json = AsyncMock()

    websocket_context = MagicMock()
    websocket_context.__aenter__ = AsyncMock(return_value=websocket)
    websocket_context.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.ws_connect.return_value = websocket_context
    return session, websocket


async def test_one_shot_client_authenticates_and_uses_current_recorder_command():
    statistics = {"sensor.energy": [{"start": 1, "change": 2.5}]}
    session, websocket = _session_with_responses(
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": statistics},
    )

    with patch(
        "butlers.connectors.home_assistant_statistics.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await HAStatisticsClient(
            ha_url="https://ha.example/",
            ha_token="secret-token",
        ).get_statistics(
            statistic_ids=["sensor.energy"],
            start="2026-07-20T00:00:00+00:00",
            end="2026-07-27T00:00:00+00:00",
            period="hour",
            types=("change",),
        )

    assert result == statistics
    session.ws_connect.assert_called_once_with(
        "wss://ha.example/api/websocket",
        heartbeat=None,
        ssl=True,
    )
    assert websocket.send_json.await_args_list[0].args[0] == {
        "type": "auth",
        "access_token": "secret-token",
    }
    command = websocket.send_json.await_args_list[1].args[0]
    assert command == {
        "id": 1,
        "type": "recorder/statistics_during_period",
        "statistic_ids": ["sensor.energy"],
        "start_time": "2026-07-20T00:00:00+00:00",
        "end_time": "2026-07-27T00:00:00+00:00",
        "period": "hour",
        "types": ["change"],
    }


async def test_one_shot_client_bounds_provider_errors():
    secret = "provider-message-with-sensitive-context"
    session, _websocket = _session_with_responses(
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {
            "id": 1,
            "type": "result",
            "success": False,
            "error": {"code": "untrusted_code", "message": secret},
        },
    )

    with (
        patch(
            "butlers.connectors.home_assistant_statistics.aiohttp.ClientSession",
            return_value=session,
        ),
        pytest.raises(HAStatisticsError) as exc_info,
    ):
        await HAStatisticsClient(
            ha_url="http://ha.example",
            ha_token="secret-token",
        ).get_statistics(
            statistic_ids=["sensor.energy"],
            start="2026-07-20T00:00:00+00:00",
            end="2026-07-27T00:00:00+00:00",
        )

    assert exc_info.value.code == "provider_error"
    assert exc_info.value.scope == "command"
    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        pytest.param([{"change": 0}, {"change": 1.5}], [0.0, 1.5], id="finite"),
        pytest.param([{}], None, id="missing"),
        pytest.param([{"change": "1.5"}], None, id="nonnumeric"),
        pytest.param([{"change": float("inf")}], None, id="infinite"),
        pytest.param([{"change": float("nan")}], None, id="nan"),
        pytest.param([], None, id="empty"),
    ],
)
def test_parse_statistics_change_series(series, expected):
    assert parse_statistics_change_series(series) == expected
