"""Signed informational callbacks forward typed identity through scoped HTTP admission."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.confirm_tokens import mint_confirm_token

pytestmark = pytest.mark.unit


async def test_signed_confirm_forwarding_and_forged_token_acknowledgement():
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="synthetic",
            internal_api_url="http://dashboard",
            approval_callback_secret="synthetic-key",
            approval_callback_connector_token="connector-key",
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(
        return_value=httpx.Response(
            200, json={"data": {"status": "answered", "application_status": "pending"}}
        )
    )
    token = mint_confirm_token(uuid4(), 0, chat_id="42", secret="synthetic-key")
    update = {
        "callback_query": {
            "id": "callback",
            "data": token,
            "from": {"id": 42},
            "message": {"chat": {"id": 42}, "message_id": 9},
        }
    }
    assert await connector._maybe_handle_confirm_callback(update)
    call = connector._http_client.post.call_args_list[0]
    assert call.args[0] == "http://dashboard/api/confirms/callback"
    assert call.kwargs["headers"] == {"X-Butlers-Confirm-Callback-Token": "connector-key"}
    assert call.kwargs["json"] == {"token": token, "sender_id": "42", "chat_id": "42"}
    assert "Applying" in connector._http_client.post.call_args_list[1].kwargs["json"]["text"]
    connector._http_client.post.reset_mock()
    update["callback_query"]["data"] = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert await connector._maybe_handle_confirm_callback(update)
    connector._http_client.post.assert_awaited_once()
    assert connector._http_client.post.call_args.args[0].endswith("/answerCallbackQuery")
    update["callback_query"]["data"] = "apr1:foreign"
    assert not await connector._maybe_handle_confirm_callback(update)
