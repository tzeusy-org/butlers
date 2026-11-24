"""Messenger reports where a sent Telegram prompt landed, additively (RFC 0038 S2.2).

``locator`` is ``<chat_id>:<message_id>`` of the message sent; ``endpoint`` is the sending bot's
identity as its inbound connector stamps it. Any gap returns nothing, so a delivery that cannot
report is the delivery it always was. The reply ref has one definition, in core.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools._routing import _telegram_reply_target
from butlers.modules.telegram import TelegramModule

pytestmark = pytest.mark.unit

SENT = {"ok": True, "result": {"message_id": 77, "chat": {"id": 100}}}


def module(identity="telegram:bot:@butler"):
    return SimpleNamespace(bot_endpoint_identity=AsyncMock(return_value=identity))


async def test_a_sent_message_reports_its_locator_and_bot():
    assert await _telegram_reply_target(module(), SENT) == {
        "locator": "100:77",
        "endpoint": "telegram:bot:@butler",
    }


@pytest.mark.parametrize(
    "result",
    [
        None,
        "x",
        {},
        {"result": {}},
        {"result": {"message_id": 77}},
        {"result": {"chat": {"id": 100}}},
        {"result": {"message_id": True, "chat": {"id": 100}}},
        {"result": {"message_id": 77, "chat": {"id": ""}}},
    ],
)
async def test_a_result_without_a_chat_and_message_reports_nothing(result):
    assert await _telegram_reply_target(module(), result) == {}


async def test_an_unresolvable_bot_or_missing_module_reports_nothing():
    assert await _telegram_reply_target(module(None), SENT) == {}
    assert await _telegram_reply_target(None, SENT) == {}
    broken = SimpleNamespace(bot_endpoint_identity=AsyncMock(side_effect=RuntimeError("down")))
    assert await _telegram_reply_target(broken, SENT) == {}


async def test_the_bot_identity_is_resolved_once_and_a_failure_is_not_cached(monkeypatch):
    calls = []

    async def resolve(token):
        calls.append(token)
        if len(calls) == 1:
            raise RuntimeError("getMe unavailable")
        return "telegram:bot:@butler"

    monkeypatch.setattr("butlers.modules.telegram.resolve_telegram_endpoint_identity", resolve)
    bot = TelegramModule.__new__(TelegramModule)
    bot._get_bot_token = lambda: "token"
    assert await bot.bot_endpoint_identity() is None
    assert await bot.bot_endpoint_identity() == "telegram:bot:@butler"
    assert await bot.bot_endpoint_identity() == "telegram:bot:@butler"
    assert len(calls) == 2


def test_the_reply_ref_has_one_definition_in_core():
    from butlers.core import reply_scope
    from butlers.tools.switchboard.ingestion import accepted_source, goal_input

    assert accepted_source.scoped_reply_ref is reply_scope.scoped_reply_ref
    assert not hasattr(goal_input, "scoped_reply_ref")


@pytest.mark.parametrize("channel", ["telegram", "email", "whatsapp"])
@pytest.mark.parametrize("enabled,owner", [(False, True), (True, True), (True, False)])
async def test_d19_one_voice_materializes_only_verified_owner_calls(
    monkeypatch, channel, enabled, owner
):
    """REQ-dashboard-goal-continuity-001: formatting never asserts provenance or authority."""
    from butlers.core_tools import _routing

    if enabled:
        monkeypatch.setenv("BUTLERS_D19_ONE_VOICE", "true")
    else:
        monkeypatch.delenv("BUTLERS_D19_ONE_VOICE", raising=False)
    resolve = AsyncMock(return_value=object() if owner else None)
    monkeypatch.setattr(_routing, "resolve_owner_channel_via_definer", resolve)
    send = AsyncMock(return_value={"ok": True})
    adapter = SimpleNamespace(_send_message=send, _send_email=send, _send_message_with_policy=send)
    command = await _routing._build_routed_delivery_command(
        daemon=SimpleNamespace(db=SimpleNamespace(pool=object())),
        modules_by_name={channel: adapter},
        channel=channel,
        intent="send",
        message_text="Recorded reply",
        origin="health",
        recipient="recorded-target",
        subject="Recorded subject",
        notify_context=None,
    )
    assert command is not None
    if channel == "email":
        assert command.tool_args["subject"] == (
            "Recorded subject" if enabled and owner else "[health] Recorded subject"
        )
        assert command.tool_args["body"] == "Recorded reply"
    else:
        assert command.tool_args["text"] == (
            "Recorded reply" if enabled and owner else "[health] Recorded reply"
        )
    assert command.approval_target == "recorded-target"
    await command.execute()
    send.assert_awaited_once()
    if enabled:
        assert resolve.await_args.args[-1] == "recorded-target"
    else:
        resolve.assert_not_awaited()


async def test_one_voice_reply_resolves_its_actual_sender_not_the_send_recipient(monkeypatch):
    from butlers.core_tools import _routing

    monkeypatch.setenv("BUTLERS_D19_ONE_VOICE", "true")
    resolve = AsyncMock(return_value=object())
    monkeypatch.setattr(_routing, "resolve_owner_channel_via_definer", resolve)
    send = AsyncMock()
    command = await _routing._build_routed_delivery_command(
        daemon=SimpleNamespace(db=SimpleNamespace(pool=object())),
        modules_by_name={"email": SimpleNamespace(_reply_to_thread=send)},
        channel="email",
        intent="reply",
        message_text="Stored reply",
        origin="health",
        recipient="unrelated-caller-value",
        subject="Thread subject",
        notify_context=SimpleNamespace(
            source_sender_identity="actual-sender", source_thread_identity="stored-thread"
        ),
    )
    assert command is not None
    assert command.tool_args["to"] == "actual-sender"
    assert command.tool_args["subject"] == "Thread subject"
    assert resolve.await_args.args[-1] == "actual-sender"


async def test_owner_sender_in_a_group_does_not_establish_owner_delivery(monkeypatch):
    from butlers.core_tools import _routing

    monkeypatch.setenv("BUTLERS_D19_ONE_VOICE", "true")
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(_routing, "resolve_owner_channel_via_definer", resolve)
    command = await _routing._build_routed_delivery_command(
        daemon=SimpleNamespace(db=SimpleNamespace(pool=object())),
        modules_by_name={"telegram": SimpleNamespace(_reply_to_message=AsyncMock())},
        channel="telegram",
        intent="reply",
        message_text="Stored reply",
        origin="health",
        recipient="owner",
        subject=None,
        notify_context=SimpleNamespace(
            source_sender_identity="owner", source_thread_identity="-9001:77"
        ),
    )
    assert command is not None
    assert command.approval_target == "-9001"
    assert command.tool_args["text"] == "[health] Stored reply"
    assert resolve.await_args.args[-1] == "-9001"


@pytest.mark.parametrize("value", ["", "false", "1", "TRUE"])
def test_one_voice_opt_in_requires_the_exact_switch_value(monkeypatch, value):
    from butlers.core.owner_voice import one_voice_enabled

    monkeypatch.setenv("BUTLERS_D19_ONE_VOICE", value)
    assert not one_voice_enabled()
    monkeypatch.setenv("BUTLERS_D19_ONE_VOICE", "true")
    assert one_voice_enabled()


def test_one_voice_preserves_unknown_authors_and_quoted_source_text():
    from butlers.core.owner_voice import notification_text

    assert notification_text("[health] Stored reply", "health", owner_voice=True) == "Stored reply"
    assert notification_text("[unknown] Stored reply", "health", owner_voice=True) == (
        "[unknown] Stored reply"
    )
    assert notification_text("Quote: [health] Stored reply", "health", owner_voice=True) == (
        "Quote: [health] Stored reply"
    )
    assert notification_text("Stored reply", "health") == "[health] Stored reply"
