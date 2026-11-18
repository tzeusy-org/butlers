"""Telegram /reach: owner-only, deterministic, narrowing-only (RFC 0038 D4)."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.connectors import telegram_bot
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.reach_tokens import mint_reach_revoke_token, verify_reach_revoke_token

pytestmark = pytest.mark.unit

SECRET = "synthetic-key"


def _connector(*, secret: str | None = SECRET) -> TelegramBotConnector:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="synthetic",
            internal_api_url="http://dashboard",
            approval_callback_secret=secret,
            approval_callback_connector_token="connector-key",
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    connector._http_client = MagicMock()
    connector._http_client.post = AsyncMock(
        return_value=httpx.Response(200, json={"data": {"status": "ok", "clauses": []}})
    )
    return connector


def _owner(monkeypatch, *, primary: bool = True, known: bool = True) -> None:
    result = (MagicMock(), primary) if known else None
    monkeypatch.setattr(
        telegram_bot, "resolve_owner_channel_via_definer", AsyncMock(return_value=result)
    )


def _command(text: str = "/reach", sender: int = 42, chat: int = 42) -> dict:
    return {"message": {"text": text, "from": {"id": sender}, "chat": {"id": chat}}}


def _tap(token: str, *, sender: int = 42, chat: int = 42) -> dict:
    return {
        "callback_query": {
            "id": "cb",
            "data": token,
            "from": {"id": sender},
            "message": {"chat": {"id": chat}, "message_id": 9},
        }
    }


def _post(connector: TelegramBotConnector) -> AsyncMock:
    return cast(AsyncMock, connector._http_client.post)


def _urls(connector: TelegramBotConnector) -> list[str]:
    return [call.args[0] for call in _post(connector).call_args_list]


def test_token_is_chat_bound_and_fits_callback_data():
    clause = uuid4()
    token = mint_reach_revoke_token(clause, chat_id="42", secret=SECRET)
    assert len(token.encode()) <= 64
    assert verify_reach_revoke_token(token, chat_id="42", secret=SECRET) == clause
    assert verify_reach_revoke_token(token, chat_id="43", secret=SECRET) is None
    assert verify_reach_revoke_token(token, chat_id="42", secret="other") is None
    assert verify_reach_revoke_token(token[:-1] + "0x", chat_id="42", secret=SECRET) is None
    assert verify_reach_revoke_token(None, chat_id="42", secret=SECRET) is None


async def test_owner_command_lists_clauses_with_signed_revoke_buttons(monkeypatch):
    _owner(monkeypatch)
    clause = uuid4()
    connector = _connector()
    connector._http_client.post = AsyncMock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "status": "ok",
                        "clauses": [
                            {"id": str(clause), "channel": "email", "sender_domain": "bank.example"}
                        ],
                    }
                },
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert await connector._maybe_handle_reach_command(_command())
    first, second = _post(connector).call_args_list
    assert first.args[0] == "http://dashboard/api/attention/reach/telegram"
    assert first.kwargs["headers"] == {"X-Butlers-Approval-Callback-Token": "connector-key"}
    assert first.kwargs["json"] == {"action": "list", "sender_id": "42", "chat_id": "42"}
    assert second.args[0].endswith("/sendMessage")
    button = second.kwargs["json"]["reply_markup"]["inline_keyboard"][0][0]
    assert verify_reach_revoke_token(button["callback_data"], chat_id="42", secret=SECRET) == clause
    assert "bank.example" in second.kwargs["json"]["text"]


async def test_command_from_non_owner_or_non_primary_falls_through(monkeypatch):
    for kwargs in ({"known": False}, {"primary": False}):
        _owner(monkeypatch, **kwargs)
        connector = _connector()
        assert not await connector._maybe_handle_reach_command(_command())
        _post(connector).assert_not_called()


@pytest.mark.parametrize("text", ["/reach add bank.example", "reach", "/reachx", "hello /reach"])
async def test_only_the_exact_command_is_intercepted(monkeypatch, text):
    _owner(monkeypatch)
    connector = _connector()
    assert not await connector._maybe_handle_reach_command(_command(text))
    _post(connector).assert_not_called()


async def test_command_never_reaches_ingest(monkeypatch):
    _owner(monkeypatch)
    connector = _connector()
    connector._normalize_to_ingest_v1 = AsyncMock()
    await connector._process_update({"update_id": 1, **_command()})
    connector._normalize_to_ingest_v1.assert_not_called()


async def test_missing_secret_replies_with_dashboard_hint_and_no_api_call(monkeypatch):
    _owner(monkeypatch)
    connector = _connector(secret=None)
    assert await connector._maybe_handle_reach_command(_command())
    assert [url.rsplit("/", 1)[-1] for url in _urls(connector)] == ["sendMessage"]
    assert "dashboard" in _post(connector).call_args.kwargs["json"]["text"]


async def test_owner_tap_revokes_through_the_connector_route(monkeypatch):
    _owner(monkeypatch)
    clause = uuid4()
    connector = _connector()
    connector._http_client.post = AsyncMock(
        side_effect=[
            httpx.Response(200, json={"data": {"status": "revoked"}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    token = mint_reach_revoke_token(clause, chat_id="42", secret=SECRET)
    assert await connector._maybe_handle_reach_callback(_tap(token))
    first, second = _post(connector).call_args_list
    assert first.kwargs["json"] == {
        "action": "revoke",
        "sender_id": "42",
        "chat_id": "42",
        "clause_id": str(clause),
    }
    assert second.args[0].endswith("/answerCallbackQuery")
    assert second.kwargs["json"]["text"] == "Revoked."


async def test_forged_replayed_or_non_owner_taps_never_call_the_api(monkeypatch):
    token = mint_reach_revoke_token(uuid4(), chat_id="42", secret=SECRET)
    cases = [
        (_tap(token[:-1] + ("0" if token[-1] != "0" else "1")), {}),
        (_tap(token, chat=43), {}),
        (_tap(token, sender=7), {"known": False}),
        (_tap(token), {"primary": False}),
    ]
    for update, owner in cases:
        _owner(monkeypatch, **owner)
        connector = _connector()
        assert await connector._maybe_handle_reach_callback(update)
        assert [url.rsplit("/", 1)[-1] for url in _urls(connector)] == ["answerCallbackQuery"]


async def test_foreign_callbacks_are_not_claimed():
    connector = _connector()
    assert not await connector._maybe_handle_reach_callback(_tap("apr1:foreign"))
    assert not await connector._maybe_handle_reach_callback({"message": {"text": "x"}})


async def test_command_in_a_group_chat_falls_through(monkeypatch):
    _owner(monkeypatch)
    connector = _connector()
    assert not await connector._maybe_handle_reach_command(_command(chat=-1001))
    _post(connector).assert_not_called()


async def test_owner_lookup_failure_still_answers_the_callback(monkeypatch):
    monkeypatch.setattr(
        telegram_bot,
        "resolve_owner_channel_via_definer",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    connector = _connector()
    connector._http_client.post = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    token = mint_reach_revoke_token(uuid4(), chat_id="42", secret=SECRET)
    assert await connector._maybe_handle_reach_callback(_tap(token))
    assert [url.rsplit("/", 1)[-1] for url in _urls(connector)] == ["answerCallbackQuery"]
