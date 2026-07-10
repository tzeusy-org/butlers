"""Unit tests for the gap-interview telegram inline-button transport (bu-whhll.12)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from butlers.chronicler.gap_interview import (
    GapInterview,
    GapInterviewDecision,
)
from butlers.chronicler.gap_interview_telegram import (
    TelegramInlineButtonTransport,
    build_telegram_transport,
)


def _interview() -> GapInterview:
    decision = GapInterviewDecision(
        local_date="2026-07-02",
        question="Yesterday 09:00-18:00 looks like a work day — confirm?",
        reasons=("low_confidence_occupation",),
        unaccounted_seconds=0.0,
    )
    return GapInterview(interview_id="2026-07-02", decision=decision)


def _resp(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {"result": {"message_id": 42}}
    resp.text = ""
    return resp


async def test_deliver_posts_inline_keyboard_with_cgi_callbacks():
    http = MagicMock()
    http.post = AsyncMock(return_value=_resp())
    transport = TelegramInlineButtonTransport(
        http_client=http, api_base="https://api.telegram.org/botTOKEN", chat_id="12345"
    )

    result = await transport.deliver_interview(_interview())

    assert result.delivered is True
    assert result.reference == "42"
    (url,), kwargs = http.post.call_args
    assert url.endswith("/sendMessage")
    payload = kwargs["json"]
    assert payload["chat_id"] == "12345"
    assert payload["text"].endswith("confirm?")
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    callbacks = {b["callback_data"] for b in buttons}
    assert callbacks == {
        "cgi:2026-07-02:confirm",
        "cgi:2026-07-02:correct",
        "cgi:2026-07-02:dismiss",
    }
    # Every button stays within Telegram's 64-byte callback_data budget.
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


async def test_deliver_reports_not_delivered_on_http_error():
    http = MagicMock()
    http.post = AsyncMock(return_value=_resp(status_code=403, body={"description": "blocked"}))
    transport = TelegramInlineButtonTransport(
        http_client=http, api_base="https://api.telegram.org/botTOKEN", chat_id="12345"
    )
    result = await transport.deliver_interview(_interview())
    assert result.delivered is False
    assert "403" in result.detail


async def test_deliver_reports_not_delivered_on_network_exception():
    http = MagicMock()
    http.post = AsyncMock(side_effect=RuntimeError("boom"))
    transport = TelegramInlineButtonTransport(
        http_client=http, api_base="https://api.telegram.org/botTOKEN", chat_id="12345"
    )
    result = await transport.deliver_interview(_interview())
    assert result.delivered is False


async def test_build_transport_none_without_owner_chat(monkeypatch):
    import butlers.chronicler.gap_interview_telegram as mod

    monkeypatch.setattr(mod, "resolve_owner_chat_id", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "resolve_bot_token", AsyncMock(return_value="TOKEN"))
    transport = await build_telegram_transport(MagicMock(), http_client=MagicMock())
    assert transport is None


async def test_build_transport_none_without_token(monkeypatch):
    import butlers.chronicler.gap_interview_telegram as mod

    monkeypatch.setattr(mod, "resolve_owner_chat_id", AsyncMock(return_value="12345"))
    monkeypatch.setattr(mod, "resolve_bot_token", AsyncMock(return_value=None))
    transport = await build_telegram_transport(MagicMock(), http_client=MagicMock())
    assert transport is None


async def test_build_transport_ok(monkeypatch):
    import butlers.chronicler.gap_interview_telegram as mod

    monkeypatch.setattr(mod, "resolve_owner_chat_id", AsyncMock(return_value="12345"))
    monkeypatch.setattr(mod, "resolve_bot_token", AsyncMock(return_value="TOKEN"))
    http = MagicMock()
    http.post = AsyncMock(return_value=_resp())
    transport = await build_telegram_transport(MagicMock(), http_client=http)
    assert transport is not None
    result = await transport.deliver_interview(_interview())
    assert result.delivered is True
