"""Telegram bot connector — gap-interview (bu-whhll.12) inline-button handling.

Covers the additive, prefix-guarded (``cgi:``) callback path:
- a ``cgi:`` callback is POSTed to the chronicler resolve API and acknowledged;
- a non-``cgi:`` callback_query is NOT claimed (keeps its existing drop path);
- an unset internal API url is handled gracefully (still acks the tap);
- an unknown/expired interview yields a graceful toast, not an error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
)
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER


def _make_connector(
    *,
    internal_api_url: str | None,
    connector_token: str | None = "connector-key",
    owner: bool = True,
) -> TelegramBotConnector:
    config = TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        endpoint_identity="telegram:bot:1",
        telegram_token="test-token",
        internal_api_url=internal_api_url,
        approval_callback_connector_token=connector_token,
    )
    connector = TelegramBotConnector(config, cursor_pool=MagicMock())
    connector._is_primary_owner_callback = AsyncMock(return_value=owner)
    return connector


def _resp(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {"status": "applied"}
    return resp


def _cgi_update(data: str = "cgi:2026-07-02:confirm") -> dict[str, Any]:
    return {"update_id": 1, "callback_query": {"id": "cbq1", "data": data}}


async def test_cgi_callback_posts_to_resolve_api_and_acks():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_resp(body={"status": "applied"}))

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True

    calls = connector._http_client.post.call_args_list
    # First POST → resolve endpoint with the parsed interview_id + answer.
    resolve_url = calls[0].args[0]
    assert resolve_url == "http://dashboard-api:41200/api/chronicler/gap-interview/resolve"
    assert calls[0].kwargs["json"] == {"interview_id": "2026-07-02", "answer": "confirm"}
    # The dashboard API is owner-authenticated; the tap carries the connector credential.
    assert calls[0].kwargs["headers"] == {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "connector-key"}
    # Second POST → answerCallbackQuery acknowledging the tap.
    ack_url = calls[1].args[0]
    assert ack_url.endswith("/answerCallbackQuery")
    assert calls[1].kwargs["json"]["callback_query_id"] == "cbq1"


async def test_non_cgi_callback_is_not_claimed():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock()

    handled = await connector._maybe_handle_gap_interview_callback(
        {"update_id": 2, "callback_query": {"id": "x", "data": "other:thing"}}
    )
    assert handled is False
    connector._http_client.post.assert_not_called()


async def test_non_callback_update_is_not_claimed():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    handled = await connector._maybe_handle_gap_interview_callback(
        {"update_id": 3, "message": {"text": "hi"}}
    )
    assert handled is False


async def test_cgi_callback_without_api_url_is_graceful():
    connector = _make_connector(internal_api_url=None)
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_resp())

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True
    # No resolve POST attempted (no API url); only the answerCallbackQuery ack.
    posts = connector._http_client.post.call_args_list
    assert len(posts) == 1
    assert posts[0].args[0].endswith("/answerCallbackQuery")


async def test_cgi_callback_expired_interview_yields_graceful_toast():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(
        return_value=_resp(body={"status": "error", "error": "unknown_or_expired_interview"})
    )

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True
    ack = connector._http_client.post.call_args_list[1].kwargs["json"]
    assert ack["text"] == "This confirmation has expired."


async def test_cgi_callback_api_unreachable_is_graceful():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    # First call (resolve POST) raises; ack POST must still be attempted.
    connector._http_client.post = AsyncMock(side_effect=[RuntimeError("conn refused"), _resp()])

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True
    assert connector._http_client.post.call_count == 2
    ack = connector._http_client.post.call_args_list[1].kwargs["json"]
    assert "dashboard" in ack["text"]


async def test_cgi_callback_without_connector_credential_is_graceful():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200", connector_token=None)
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_resp())

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True
    posts = connector._http_client.post.call_args_list
    assert len(posts) == 1
    assert posts[0].args[0].endswith("/answerCallbackQuery")


async def test_cgi_callback_from_non_owner_is_acked_but_not_applied():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200", owner=False)
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_resp())

    handled = await connector._maybe_handle_gap_interview_callback(_cgi_update())
    assert handled is True
    posts = connector._http_client.post.call_args_list
    assert len(posts) == 1
    assert posts[0].args[0].endswith("/answerCallbackQuery")
    assert posts[0].kwargs["json"]["text"] == ""


def _cgi_update_with_message(data: str = "cgi:2026-07-02:confirm") -> dict[str, Any]:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq1",
            "data": data,
            "message": {
                "message_id": 77,
                "chat": {"id": 42},
                "text": "Yesterday has about 11.3h of unaccounted waking time",
            },
        },
    }


def _urls(post: AsyncMock) -> list[str]:
    return [call.args[0] for call in post.call_args_list]


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        ({"status": "applied"}, "Logged as a work day ✅"),
        ({"status": "already_answered"}, "Already recorded — thanks!"),
        (
            {"status": "error", "error": "unknown_or_expired_interview"},
            "This confirmation has expired.",
        ),
    ],
)
async def test_settled_answer_stamps_outcome_and_retires_buttons(body, outcome):
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(return_value=_resp(body=body))

    assert await connector._maybe_handle_gap_interview_callback(_cgi_update_with_message())

    edit = connector._http_client.post.call_args_list[-1]
    assert edit.args[0].endswith("/editMessageText")
    assert edit.kwargs["json"]["chat_id"] == "42"
    assert edit.kwargs["json"]["message_id"] == 77
    assert "unaccounted waking time" in edit.kwargs["json"]["text"]
    assert outcome in edit.kwargs["json"]["text"]
    # Omitting reply_markup is what drops the inline keyboard.
    assert "reply_markup" not in edit.kwargs["json"]


@pytest.mark.parametrize(
    "resolve",
    [_resp(status_code=401), _resp(body={"status": "error", "error": "db_down"})],
)
async def test_unsettled_answer_keeps_buttons_for_retry(resolve):
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    post = AsyncMock(side_effect=[resolve, _resp()])
    connector._http_client.post = post

    assert await connector._maybe_handle_gap_interview_callback(_cgi_update_with_message())

    urls = _urls(post)
    assert not any(u.endswith(("/editMessageText", "/editMessageReplyMarkup")) for u in urls)


async def test_failed_text_edit_still_removes_keyboard():
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()
    failed_edit = _resp(status_code=500)
    failed_edit.raise_for_status.side_effect = RuntimeError("telegram 500")
    post = AsyncMock(side_effect=[_resp(body={"status": "applied"}), _resp(), failed_edit, _resp()])
    connector._http_client.post = post

    assert await connector._maybe_handle_gap_interview_callback(_cgi_update_with_message())

    assert _urls(post)[-1].endswith("/editMessageReplyMarkup")
