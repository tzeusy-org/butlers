"""Telegram ``apr1`` approval-callback control-plane coverage.

These tests keep approval decisions out of Switchboard ingestion and require
owner verification before the signed callback can reach the dashboard decision
surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import mint_approval_callback_token

_ACTION_ID = UUID("12345678-1234-5678-1234-567812345678")
_REQUESTED_AT = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
_SECRET = "test-only-approval-callback-secret"
_CONNECTOR_TOKEN = "test-only-approval-callback-connector-token"


def _response(*, status_code: int = 200, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body if body is not None else {"data": {}}
    return response


def _connector() -> TelegramBotConnector:
    return TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url="http://dashboard-api:41200",
            approval_callback_secret=_SECRET,
            approval_callback_connector_token=_CONNECTOR_TOKEN,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )


def _token(*, verb: str = "a") -> str:
    return mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb=verb,
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )


def _update(data: str, *, callback_id: str = "cbq1") -> dict:
    return {
        "update_id": 73,
        "callback_query": {
            "id": callback_id,
            "data": data,
            "from": {"id": 9001},
            "message": {"chat": {"id": 9001}, "message_id": 44},
        },
    }


def _pending_detail(*, status: str = "pending") -> dict:
    return {
        "data": {
            "id": str(_ACTION_ID),
            "created_at": _REQUESTED_AT.isoformat(),
            "status": status,
        }
    }


@pytest.fixture
def owner_resolver(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(telegram_bot_module, "resolve_owner_channel_via_definer", resolver)
    return resolver


async def test_owner_approve_acknowledges_then_uses_standard_decision_route(
    owner_resolver: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _connector()
    connector._http_client = MagicMock()
    connector._http_client.get = AsyncMock(return_value=_response(body=_pending_detail()))
    connector._http_client.post = AsyncMock(
        side_effect=[
            _response(),
            _response(body={"data": {"status": "executed"}}),
        ]
    )
    edit = AsyncMock()
    monkeypatch.setattr(connector, "_edit_approval_callback_message", edit)

    handled = await connector._maybe_handle_approval_callback(_update(_token()))

    assert handled is True
    owner_resolver.assert_awaited_once_with(connector._db_pool, "telegram_bot", "9001")
    assert connector._http_client.post.call_args_list[0].args[0].endswith("/answerCallbackQuery")
    decision_call = connector._http_client.post.call_args_list[1]
    assert decision_call.args[0] == f"http://dashboard-api:41200/api/approvals/{_ACTION_ID}/approve"
    assert decision_call.kwargs["headers"] == {
        "X-Butlers-Decision-Actor": "owner@telegram",
        "X-Butlers-Approval-Callback-Token": _CONNECTOR_TOKEN,
    }
    edit.assert_awaited_once_with(_update(_token())["callback_query"], "executed")


async def test_non_owner_approval_callback_is_acknowledged_without_state_or_api_reads(
    owner_resolver: AsyncMock,
) -> None:
    owner_resolver.return_value = None
    connector = _connector()
    connector._http_client = MagicMock()
    connector._http_client.get = AsyncMock()
    connector._http_client.post = AsyncMock(return_value=_response())

    handled = await connector._maybe_handle_approval_callback(_update(_token()))

    assert handled is True
    connector._http_client.get.assert_not_awaited()
    connector._http_client.post.assert_awaited_once()
    assert connector._http_client.post.call_args.args[0].endswith("/answerCallbackQuery")


async def test_tampered_approval_callback_is_acknowledged_without_decision_mutation(
    owner_resolver: AsyncMock,
) -> None:
    connector = _connector()
    connector._http_client = MagicMock()
    connector._http_client.get = AsyncMock(return_value=_response(body=_pending_detail()))
    connector._http_client.post = AsyncMock(return_value=_response())
    tampered = f"{_token()[:-1]}0"

    handled = await connector._maybe_handle_approval_callback(_update(tampered))

    assert handled is True
    owner_resolver.assert_awaited_once()
    assert connector._http_client.post.await_count == 1
    assert connector._http_client.post.call_args.args[0].endswith("/answerCallbackQuery")


async def test_already_decided_callback_notifies_owner_and_removes_keyboard(
    owner_resolver: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _connector()
    connector._http_client = MagicMock()
    connector._http_client.get = AsyncMock(
        return_value=_response(body=_pending_detail(status="rejected"))
    )
    connector._http_client.post = AsyncMock(return_value=_response())
    edit = AsyncMock()
    monkeypatch.setattr(connector, "_edit_approval_callback_message", edit)

    handled = await connector._maybe_handle_approval_callback(_update(_token()))

    assert handled is True
    assert connector._http_client.post.await_count == 1
    assert connector._http_client.post.call_args.kwargs["json"]["text"] == "Already handled."
    edit.assert_awaited_once_with(_update(_token())["callback_query"], "rejected")


async def test_expired_pending_callback_remains_a_denial_and_is_rendered_terminally(
    owner_resolver: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _connector()
    stale_detail = _pending_detail()
    stale_detail["data"]["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    expired_detail = _pending_detail(status="expired")
    connector._http_client = MagicMock()
    connector._http_client.get = AsyncMock(
        side_effect=[_response(body=stale_detail), _response(body=expired_detail)]
    )
    connector._http_client.post = AsyncMock(
        side_effect=[
            _response(status_code=409),
            _response(),
        ]
    )
    edit = AsyncMock()
    monkeypatch.setattr(connector, "_edit_approval_callback_message", edit)

    handled = await connector._maybe_handle_approval_callback(_update(_token()))

    assert handled is True
    assert connector._http_client.post.call_args_list[0].args[0].endswith("/approve")
    assert connector._http_client.post.call_args_list[1].args[0].endswith("/answerCallbackQuery")
    assert (
        connector._http_client.post.call_args_list[1].kwargs["json"]["text"] == "Already handled."
    )
    edit.assert_awaited_once_with(_update(_token())["callback_query"], "expired")


async def test_non_approval_callback_is_generically_acknowledged_without_ingestion() -> None:
    connector = _connector()
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_response())
    connector._submit_to_ingest = AsyncMock()

    await connector._process_update(_update("not-an-approval"))

    connector._submit_to_ingest.assert_not_awaited()
    connector._http_client.post.assert_awaited_once()
    assert connector._http_client.post.call_args.args[0].endswith("/answerCallbackQuery")
