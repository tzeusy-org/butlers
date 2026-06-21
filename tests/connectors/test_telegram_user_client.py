"""Condensed Telegram user-client connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, media messages
- Idempotency key format (canonical: tg:<chat_id>:<message_id>)
- Participant count + chat type enrichment (RFC 0013)
- Interaction eligibility gating for large groups (RFC 0013)

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


async def test_text_message_envelope_contract(connector: TelegramUserClientConnector) -> None:
    """Text envelope carries ingest.v1 schema, telegram source, mapped event/sender fields,
    and the canonical 'tg:<chat_id>:<message_id>' idempotency key."""
    msg = _make_message(msg_id=42, chat_id=200, sender_id=777, text="Event test")
    env = await connector._normalize_to_ingest_v1(msg)
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "telegram_user_client"
    assert env["source"]["provider"] == "telegram"
    assert env["source"]["endpoint_identity"] == _ENDPOINT
    assert env["event"]["external_event_id"] == "42"
    assert env["event"]["external_thread_id"] == "200"
    assert env["sender"]["identity"] == "777"
    assert "Event test" in env["payload"]["normalized_text"]
    key = env["control"]["idempotency_key"]
    assert key.startswith("tg:")
    assert "200" in key
    assert "42" in key


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


# ---------------------------------------------------------------------------
# Dunbar group-aware interaction gating tests (RFC 0013)
# ---------------------------------------------------------------------------


def _make_message_with_chat(
    msg_id: int = 1,
    chat_id: int = 100,
    sender_id: int = 999,
    text: str = "Hello!",
    chat_entity: object | None = None,
    participants_count: int | None = None,
) -> MagicMock:
    """Build a mock Telethon message with optional chat entity."""
    msg = MagicMock()
    msg.id = msg_id
    msg.chat_id = chat_id
    msg.sender_id = sender_id
    msg.message = text
    msg.media = None
    msg.to_dict = lambda: {"id": msg_id, "message": text}

    if chat_entity is not None:
        msg.chat = chat_entity
    else:
        # Default: mock User entity for DM
        user_entity = MagicMock()
        user_entity.__class__.__name__ = "User"
        if participants_count is not None:
            user_entity.participants_count = participants_count
        msg.chat = user_entity

    return msg


def test_derive_chat_type_private() -> None:
    """User entity maps to 'private' chat type."""
    user = MagicMock()
    user.__class__.__name__ = "User"
    assert TelegramUserClientConnector._derive_chat_type(user) == "private"


def test_derive_chat_type_group() -> None:
    """Chat entity maps to 'group' chat type."""
    chat = MagicMock()
    chat.__class__.__name__ = "Chat"
    assert TelegramUserClientConnector._derive_chat_type(chat) == "group"


def test_derive_chat_type_supergroup() -> None:
    """Channel entity with megagroup=True maps to 'supergroup'."""
    channel = MagicMock()
    channel.__class__.__name__ = "Channel"
    channel.megagroup = True
    channel.broadcast = False
    assert TelegramUserClientConnector._derive_chat_type(channel) == "supergroup"


def test_derive_chat_type_channel() -> None:
    """Channel entity with broadcast=True maps to 'channel'."""
    channel = MagicMock()
    channel.__class__.__name__ = "Channel"
    channel.megagroup = False
    channel.broadcast = True
    assert TelegramUserClientConnector._derive_chat_type(channel) == "channel"


def test_derive_chat_type_none_entity() -> None:
    """None entity falls back to 'private'."""
    assert TelegramUserClientConnector._derive_chat_type(None) == "private"


async def test_dm_message_has_participant_count_2(
    connector: TelegramUserClientConnector,
) -> None:
    """DM messages must have participant_count=2 and chat_type='private'."""
    user_entity = MagicMock()
    user_entity.__class__.__name__ = "User"
    msg = _make_message_with_chat(chat_entity=user_entity)

    env = await connector._normalize_to_ingest_v1(msg)
    assert env["sender"]["participant_count"] == 2
    assert env["sender"]["chat_type"] == "private"


async def test_dm_message_interaction_eligible(
    connector: TelegramUserClientConnector,
) -> None:
    """DM messages (participant_count=2) must have interaction_eligible=True."""
    user_entity = MagicMock()
    user_entity.__class__.__name__ = "User"
    msg = _make_message_with_chat(chat_entity=user_entity)

    env = await connector._normalize_to_ingest_v1(msg)
    assert env["control"]["interaction_eligible"] is True


async def test_small_group_interaction_eligible(
    connector: TelegramUserClientConnector,
) -> None:
    """Groups at or below max_interaction_group_size must have interaction_eligible=True."""
    # Inject count directly into cache to bypass Telethon API call
    chat_id = "500"
    connector._participant_count_cache[chat_id] = (15, 999999999.0)  # cached, won't expire

    chat_entity = MagicMock()
    chat_entity.__class__.__name__ = "Chat"
    msg = MagicMock()
    msg.id = 1
    msg.chat_id = int(chat_id)
    msg.sender_id = 999
    msg.message = "hello"
    msg.media = None
    msg.chat = chat_entity
    msg.to_dict = lambda: {}

    env = await connector._normalize_to_ingest_v1(msg)
    assert env["sender"]["participant_count"] == 15
    assert env["control"]["interaction_eligible"] is True


async def test_large_group_interaction_not_eligible(
    connector: TelegramUserClientConnector,
) -> None:
    """Groups exceeding max_interaction_group_size must have interaction_eligible=False."""
    chat_id = "888"
    # Inject 25 participants into cache (exceeds default limit of 20)
    connector._participant_count_cache[chat_id] = (25, 999999999.0)

    channel_entity = MagicMock()
    channel_entity.__class__.__name__ = "Channel"
    channel_entity.megagroup = True
    channel_entity.broadcast = False
    msg = MagicMock()
    msg.id = 2
    msg.chat_id = int(chat_id)
    msg.sender_id = 123
    msg.message = "big group message"
    msg.media = None
    msg.chat = channel_entity
    msg.to_dict = lambda: {}

    env = await connector._normalize_to_ingest_v1(msg)
    assert env["sender"]["participant_count"] == 25
    assert env["sender"]["chat_type"] == "supergroup"
    assert env["control"]["interaction_eligible"] is False


async def test_large_group_envelope_passes_parse(
    connector: TelegramUserClientConnector,
) -> None:
    """Large-group envelope with interaction_eligible=False must still validate against schema."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    chat_id = "777"
    connector._participant_count_cache[chat_id] = (50, 999999999.0)

    channel_entity = MagicMock()
    channel_entity.__class__.__name__ = "Channel"
    channel_entity.megagroup = False
    channel_entity.broadcast = True
    msg = MagicMock()
    msg.id = 10
    msg.chat_id = int(chat_id)
    msg.sender_id = 456
    msg.message = "channel post"
    msg.media = None
    msg.chat = channel_entity
    msg.to_dict = lambda: {}

    env = await connector._normalize_to_ingest_v1(msg)
    assert env["control"]["interaction_eligible"] is False
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_batch_envelope_includes_participant_count(
    connector: TelegramUserClientConnector,
) -> None:
    """Batch envelope must include sender.participant_count and sender.chat_type."""
    msgs = [_make_message(msg_id=i, chat_id=100) for i in range(1, 4)]
    env = connector._build_batch_envelope(
        "100",
        msgs,
        msgs,
        participant_count=8,
        chat_type="group",
    )
    assert env["sender"]["participant_count"] == 8
    assert env["sender"]["chat_type"] == "group"
    assert env["control"]["interaction_eligible"] is True


def test_batch_envelope_large_group_not_eligible(
    connector: TelegramUserClientConnector,
) -> None:
    """Batch envelope for large group must have interaction_eligible=False."""
    msgs = [_make_message(msg_id=i, chat_id=100) for i in range(1, 4)]
    env = connector._build_batch_envelope(
        "100",
        msgs,
        msgs,
        participant_count=21,
        chat_type="supergroup",
    )
    assert env["control"]["interaction_eligible"] is False


def test_batch_envelope_no_participant_count_defaults_eligible(
    connector: TelegramUserClientConnector,
) -> None:
    """Batch envelope with participant_count=None must default interaction_eligible=True."""
    msgs = [_make_message(msg_id=i, chat_id=100) for i in range(1, 4)]
    env = connector._build_batch_envelope(
        "100",
        msgs,
        msgs,
        participant_count=None,
        chat_type=None,
    )
    assert env["control"]["interaction_eligible"] is True


def test_participant_count_cache_ttl_constant(
    connector: TelegramUserClientConnector,
) -> None:
    """Participant count cache TTL must be set to 3600 seconds (1 hour)."""
    assert connector._participant_count_cache_ttl_s == 3600
