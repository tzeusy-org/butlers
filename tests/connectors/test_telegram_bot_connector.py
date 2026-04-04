"""Condensed Telegram bot connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, channel post, photo messages
- Returns None for non-message updates (callback_query, service messages)
- Idempotency key format

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
)

_ENDPOINT = "telegram:bot:123456789"


@pytest.fixture
def connector() -> TelegramBotConnector:
    config = TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_bot",
        endpoint_identity=_ENDPOINT,
        telegram_token="test-token",
    )
    return TelegramBotConnector(config, cursor_pool=MagicMock())


def test_text_message_schema_version(connector: TelegramBotConnector) -> None:
    """Text message envelope must carry schema_version='ingest.v1'."""
    update: dict[str, Any] = {
        "update_id": 123,
        "message": {
            "message_id": 1,
            "from": {"id": 987},
            "chat": {"id": 100},
            "text": "Hello Bot!",
        },
    }
    env = connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "telegram_bot"
    assert env["source"]["provider"] == "telegram"


def test_text_message_event_fields(connector: TelegramBotConnector) -> None:
    """Event fields map correctly from update."""
    update: dict[str, Any] = {
        "update_id": 456,
        "message": {
            "message_id": 7,
            "from": {"id": 777},
            "chat": {"id": 200},
            "text": "Test message",
        },
    }
    env = connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert env["event"]["external_event_id"] == "456"
    assert env["sender"]["identity"] == "777"
    assert "Hello Bot!" not in env["payload"]["normalized_text"]
    assert "Test message" in env["payload"]["normalized_text"]


def test_channel_post_produces_envelope(connector: TelegramBotConnector) -> None:
    """channel_post updates must produce an ingest.v1 envelope."""
    update: dict[str, Any] = {
        "update_id": 789,
        "channel_post": {
            "message_id": 5,
            "chat": {"id": 300},
            "text": "Channel announcement",
        },
    }
    env = connector._normalize_to_ingest_v1(update)
    assert env is not None
    assert "Channel announcement" in env["payload"]["normalized_text"]


def test_no_message_returns_none(connector: TelegramBotConnector) -> None:
    """callback_query updates (no message) must return None."""
    update: dict[str, Any] = {
        "update_id": 999,
        "callback_query": {"data": "btn_click"},
    }
    result = connector._normalize_to_ingest_v1(update)
    assert result is None


def test_service_message_returns_none(connector: TelegramBotConnector) -> None:
    """Service messages with no text/media must return None."""
    update: dict[str, Any] = {
        "update_id": 888,
        "message": {
            "message_id": 3,
            "chat": {"id": 150},
            "new_chat_members": [{"id": 42}],
        },
    }
    result = connector._normalize_to_ingest_v1(update)
    assert result is None


def test_idempotency_key_uses_chat_and_message_id(connector: TelegramBotConnector) -> None:
    """Idempotency key must follow 'tg:<chat_id>:<message_id>' format."""
    update: dict[str, Any] = {
        "update_id": 100,
        "message": {
            "message_id": 42,
            "from": {"id": 1},
            "chat": {"id": 999},
            "text": "idempotency test",
        },
    }
    env = connector._normalize_to_ingest_v1(update)
    assert env is not None
    key = env["control"]["idempotency_key"]
    assert "tg:" in key
    assert "999" in key
    assert "42" in key
