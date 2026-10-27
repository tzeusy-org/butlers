"""Channel-namespaced conversation identities (bu-7exe4.2)."""

from __future__ import annotations

import pytest

from butlers.conversation_identity import (
    event_conversation_identity,
    telegram_bot_message_identity,
    telegram_bot_update_identity,
)

pytestmark = pytest.mark.unit


def test_forum_topic_message_gets_its_own_conversation() -> None:
    message = {
        "message_id": 5,
        "message_thread_id": 9,
        "is_topic_message": True,
        "chat": {"id": -100777},
    }
    assert telegram_bot_message_identity(message) == ("telegram:-100777:topic:9", "-100777:5")


def test_reply_thread_outside_a_forum_stays_in_its_chat() -> None:
    message = {"message_id": 6, "message_thread_id": 5, "chat": {"id": -100777}}
    assert telegram_bot_message_identity(message) == ("telegram:-100777", "-100777:6")


def test_update_without_a_message_has_no_identity() -> None:
    assert telegram_bot_update_identity({"update_id": 1, "callback_query": {}}) == (None, None)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"external_thread_id": "thread-1"}, ("thread-1", "thread-1")),
        (
            {
                "external_conversation_id": "telegram:1",
                "reply_target_ref": "1:2",
                "external_thread_id": "1:2",
            },
            ("telegram:1", "1:2"),
        ),
        ({}, (None, None)),
    ],
)
def test_event_identity_prefers_split_fields(event: dict, expected: tuple) -> None:
    assert event_conversation_identity(event) == expected
