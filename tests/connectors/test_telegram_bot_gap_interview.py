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


# --- Settled taps retire the question's buttons (bu-paj2a3) -------------------


def _cgi_update_with_message(data: str = "cgi:2026-07-02:confirm") -> dict[str, Any]:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq1",
            "data": data,
            "message": {
                "message_id": 77,
                "chat": {"id": 4242},
                "text": "Did you work on 2 Jul?",
            },
        },
    }


def _posts_by_method(connector: TelegramBotConnector) -> dict[str, list[Any]]:
    by_method: dict[str, list[Any]] = {}
    for call in connector._http_client.post.call_args_list:
        by_method.setdefault(call.args[0].rsplit("/", 1)[-1], []).append(call)
    return by_method


def _edit_ok() -> MagicMock:
    resp = _resp(body={"ok": True})
    resp.raise_for_status = MagicMock()
    return resp


async def _tap_with_resolve(resolve_resp: Any) -> dict[str, list[Any]]:
    connector = _make_connector(internal_api_url="http://dashboard-api:41200")
    connector._http_client = MagicMock()

    async def _post(url: str, **_: Any) -> Any:
        if url.endswith("/gap-interview/resolve"):
            if isinstance(resolve_resp, BaseException):
                raise resolve_resp
            return resolve_resp
        return _edit_ok()

    connector._http_client.post = AsyncMock(side_effect=_post)
    assert await connector._maybe_handle_gap_interview_callback(_cgi_update_with_message())
    return _posts_by_method(connector)


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        ({"status": "applied"}, "Logged as a work day ✅"),
        ({"status": "already_answered"}, "Already recorded"),
        ({"status": "error", "error": "unknown_or_expired_interview"}, "expired"),
    ],
)
async def test_settled_tap_shows_outcome_and_removes_keyboard(body, outcome):
    posts = await _tap_with_resolve(_resp(body=body))

    (edit,) = posts["editMessageText"]
    assert edit.kwargs["json"]["chat_id"] == "4242"
    assert edit.kwargs["json"]["message_id"] == 77
    # The question stays legible; the outcome is appended beneath it.
    assert "Did you work on 2 Jul?" in edit.kwargs["json"]["text"]
    assert outcome in edit.kwargs["json"]["text"]
    (markup,) = posts["editMessageReplyMarkup"]
    assert markup.kwargs["json"] == {"chat_id": "4242", "message_id": 77, "reply_markup": None}


@pytest.mark.parametrize(
    "resolve_resp",
    [RuntimeError("conn refused"), _resp(status_code=503), _resp(body={"status": "weird"})],
)
async def test_unsettled_tap_keeps_buttons_for_retry(resolve_resp):
    posts = await _tap_with_resolve(resolve_resp)

    assert "answerCallbackQuery" in posts
    assert "editMessageText" not in posts
    assert "editMessageReplyMarkup" not in posts
