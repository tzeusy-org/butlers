"""``notify(awaits_reply_seconds=...)`` marks a Telegram message as a prompt (RFC 0038 S2.2).

The argument is bounded and applies to Telegram send/reply only. A delivered prompt returns an
additive ``delivery`` block (the domain's claim, which the broker resolves); without the argument,
or without a Messenger report, the result is what it was before.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.daemon.test_notify_confirm import notification  # noqa: F401

pytestmark = pytest.mark.unit

SENT = {
    "status": "sent",
    "notification_id": "n-1",
    "result": {
        "status": "ok",
        "delivery": {
            "channel": "telegram",
            "delivery_id": "77",
            "locator": "100:77",
            "endpoint": "telegram:bot:@butler",
        },
    },
}


def deliver(daemon, data):
    daemon.switchboard_client.call_tool = AsyncMock(
        return_value=SimpleNamespace(is_error=False, data=data)
    )


@pytest.mark.parametrize("seconds", [0, 29, 86401, True, "300"])
async def test_the_window_is_bounded_and_nothing_is_sent_outside_it(notification, seconds):  # noqa: F811
    notify, daemon, _ = notification
    result = await notify(message="Shall I book it?", awaits_reply_seconds=seconds)
    assert result["code"] == "awaits_reply_validation"
    daemon.switchboard_client.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channel": "email", "recipient": "owner@example.test"},
        {"intent": "insight"},
        {
            "intent": "confirm",
            "options": [{"value": "y", "label": "Y"}, {"value": "n", "label": "N"}],
        },
    ],
)
async def test_only_a_telegram_send_or_reply_can_await_a_reply(notification, kwargs):  # noqa: F811
    notify, daemon, _ = notification
    result = await notify(message="Shall I?", awaits_reply_seconds=300, **kwargs)
    assert result["code"] == "awaits_reply_validation"
    daemon.switchboard_client.call_tool.assert_not_awaited()


async def test_a_delivered_prompt_returns_where_it_landed_and_its_window(notification):  # noqa: F811
    notify, daemon, _ = notification
    deliver(daemon, SENT)
    before = datetime.now(UTC)

    result = await notify(message="Shall I book it?", awaits_reply_seconds=300)

    assert result["status"] == "ok" and result["result"] == SENT
    block = result["delivery"]
    assert {k: block[k] for k in ("transport", "endpoint", "conversation", "locator")} == {
        "transport": "telegram",
        "endpoint": "telegram:bot:@butler",
        "conversation": "telegram:100",
        "locator": "100:77",
    }
    expires = datetime.fromisoformat(block["prompt_expires_at"])
    assert before + timedelta(seconds=299) <= expires <= datetime.now(UTC) + timedelta(seconds=300)
    # The ask is part of what was sent: nothing here is a separate side channel.
    sent = daemon.switchboard_client.call_tool.call_args.args[1]["notify_request"]
    assert "awaits_reply_seconds" not in sent["delivery"]


async def test_an_ordinary_message_returns_what_it_always_did(notification):  # noqa: F811
    notify, daemon, _ = notification
    deliver(daemon, SENT)
    result = await notify(message="Done.")
    assert result == {"status": "ok", "result": SENT}


@pytest.mark.parametrize(
    "data",
    [
        {"status": "sent", "result": {"status": "ok", "delivery": {"channel": "telegram"}}},
        {"status": "sent", "result": {"status": "ok"}},
        {"status": "sent"},
        {
            "status": "sent",
            "result": {"delivery": {"locator": ":77", "endpoint": "telegram:bot:@butler"}},
        },
    ],
)
async def test_a_prompt_with_no_usable_report_is_just_a_delivery(notification, data):  # noqa: F811
    notify, daemon, _ = notification
    deliver(daemon, data)
    result = await notify(message="Shall I?", awaits_reply_seconds=300)
    assert result == {"status": "ok", "result": data}
