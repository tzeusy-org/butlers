"""Condensed Telegram user-client connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, media messages
- Idempotency key format (canonical: tg:<chat_id>:<message_id>)

[bu-35fm7]
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from butlers.connectors.telegram_user_client import (
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

_ENDPOINT = "telegram_user_client:telegram:user123"


@pytest.fixture
def connector() -> TelegramUserClientConnector:
    config = TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity=_ENDPOINT,
    )
    return TelegramUserClientConnector(config, cursor_pool=MagicMock())


def _make_message(
    msg_id: int = 1,
    chat_id: int = 100,
    sender_id: int = 999,
    text: str = "Hello!",
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.chat_id = chat_id
    msg.sender_id = sender_id
    msg.message = text
    msg.media = None
    msg.to_dict = lambda: {"id": msg_id, "message": text}
    return msg


async def test_text_message_schema_version(connector: TelegramUserClientConnector) -> None:
    """Text message envelope must carry schema_version='ingest.v1'."""
    msg = _make_message()
    env = await connector._normalize_to_ingest_v1(msg)
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "telegram_user_client"
    assert env["source"]["provider"] == "telegram"
    assert env["source"]["endpoint_identity"] == _ENDPOINT


async def test_text_message_event_fields(connector: TelegramUserClientConnector) -> None:
    """Event fields map correctly from message."""
    msg = _make_message(msg_id=42, chat_id=200, sender_id=777, text="Event test")
    env = await connector._normalize_to_ingest_v1(msg)
    assert env["event"]["external_event_id"] == "42"
    assert env["event"]["external_thread_id"] == "200"
    assert env["sender"]["identity"] == "777"
    assert "Event test" in env["payload"]["normalized_text"]


async def test_idempotency_key_canonical_format(connector: TelegramUserClientConnector) -> None:
    """Idempotency key must follow 'tg:<chat_id>:<message_id>' canonical format."""
    msg = _make_message(msg_id=55, chat_id=300)
    env = await connector._normalize_to_ingest_v1(msg)
    key = env["control"]["idempotency_key"]
    assert key.startswith("tg:")
    assert "300" in key
    assert "55" in key


async def test_media_message_normalized_text(connector: TelegramUserClientConnector) -> None:
    """Message with media and no text produces '[media]' normalized text."""
    msg = _make_message(text="")
    msg.message = None
    msg.media = MagicMock()
    env = await connector._normalize_to_ingest_v1(msg)
    assert env["payload"]["normalized_text"] == "[media]"


async def test_envelope_passes_parse_ingest_envelope(
    connector: TelegramUserClientConnector,
) -> None:
    """Telegram user-client envelope must validate against parse_ingest_envelope."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    msg = _make_message()
    env = await connector._normalize_to_ingest_v1(msg)
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")
