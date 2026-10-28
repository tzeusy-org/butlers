"""Provider-level receipts for literal, atomic informational-question edits."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from butlers.modules import telegram_confirms

pytestmark = pytest.mark.unit

NOT_MODIFIED = (
    "Bad Request: message is not modified: specified new message content and reply markup "
    "are exactly the same as a current content and reply markup of the message"
)


def _response(status, body):
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", "https://telegram.invalid/editMessageText")
    )


@pytest.fixture
def provider(monkeypatch):
    permission = AsyncMock()
    monkeypatch.setattr(telegram_confirms, "require_permission", permission)
    client = SimpleNamespace(post=AsyncMock())
    telegram = SimpleNamespace(
        _send_message=AsyncMock(),
        _permission_pool=Mock(return_value="restricted-permission-pool"),
        _butler_name="messenger",
        _get_client=Mock(return_value=client),
        _base_url=Mock(return_value="https://telegram.invalid"),
    )
    delivery = telegram_confirms.messenger_confirm_delivery(
        object(), telegram, allowed=AsyncMock(return_value=True)
    )
    return delivery, telegram, client, permission


async def test_terminal_edit_is_one_literal_provider_call_with_atomic_keyboard_removal(provider):
    delivery, telegram, client, permission = provider
    client.post.return_value = _response(200, {"ok": True})
    text = "<b>Literal answer</b> **not markdown** 😀 https://example.invalid\nRecorded."
    assert await delivery.edit({"recipient_chat": "-10042", "message_ref": "71"}, text) is True
    permission.assert_awaited_once_with(
        "restricted-permission-pool",
        "messenger",
        telegram_confirms.NOTIFY_PERMISSION,
        fail_closed=True,
    )
    client.post.assert_awaited_once_with(
        "https://telegram.invalid/editMessageText",
        json={
            "chat_id": "-10042",
            "message_id": 71,
            "text": text,
            "entities": [
                {"type": "pre", "offset": 0, "length": len(text.encode("utf-16-le")) // 2}
            ],
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {"inline_keyboard": []},
        },
    )
    telegram._send_message.assert_not_awaited()


@pytest.mark.parametrize("description", ["Bad Request: message is not modified", NOT_MODIFIED])
async def test_lost_success_edit_retries_accept_only_the_exact_already_current_receipt(
    provider, description
):
    delivery, telegram, client, _ = provider
    row, text = {"recipient_chat": "42", "message_ref": "71"}, "Answer: Not work. Recorded."
    client.post.side_effect = [
        httpx.ReadError("successful edit response was lost"),
        _response(400, {"ok": False, "error_code": 400, "description": description}),
    ]
    with pytest.raises(httpx.ReadError):
        await delivery.edit(row, text)
    assert await delivery.edit(row, text) is True
    first, retry = client.post.await_args_list
    assert first == retry  # Same message and literal content, never a replacement send.
    telegram._send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {"ok": False, "error_code": 400, "description": "Bad Request: message can't be edited"},
        {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: message is not modified but access was refused",
        },
        {"ok": False, "error_code": 400, "description": NOT_MODIFIED + " extra"},
        {"ok": False, "error_code": 403, "description": NOT_MODIFIED},
        {"ok": False, "description": NOT_MODIFIED},
    ],
)
async def test_other_400_receipts_never_confirm_an_edit(provider, body):
    delivery, _, client, _ = provider
    client.post.return_value = _response(400, body)
    with pytest.raises(httpx.HTTPStatusError):
        await delivery.edit({"recipient_chat": "42", "message_ref": "71"}, "Recorded.")


async def test_permission_failure_prevents_the_provider_edit(provider):
    delivery, _, client, permission = provider
    permission.side_effect = ValueError("permission refused")
    with pytest.raises(ValueError, match="permission refused"):
        await delivery.edit({"recipient_chat": "42", "message_ref": "71"}, "Recorded.")
    client.post.assert_not_awaited()


async def test_owner_policy_retains_prompt_even_if_source_allows(provider, monkeypatch):
    delivery, telegram, client, _ = provider
    policy = AsyncMock(return_value="quiet_hours")
    monkeypatch.setattr(telegram_confirms, "check_owner_notify_suppression", policy)
    row = {"recipient_owner": True}
    assert not await delivery.allowed(row)
    policy.assert_awaited_once_with("restricted-permission-pool", log_context="confirm")
    client.post.assert_not_awaited()
    policy.return_value = None
    assert await delivery.allowed(row)
    policy.side_effect = RuntimeError("unavailable policy")
    with pytest.raises(RuntimeError, match="unavailable policy"):
        await delivery.allowed(row)
    client.post.assert_not_awaited()
