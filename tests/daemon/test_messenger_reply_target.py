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

    monkeypatch.setattr(
        "butlers.connectors.telegram_bot.resolve_telegram_endpoint_identity", resolve
    )
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
