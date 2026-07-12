"""Condensed Telegram user-client connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, media messages
- Idempotency key format (canonical: tg:<chat_id>:<message_id>)
- Participant count + chat type enrichment (RFC 0013)
- Interaction eligibility gating for large groups (RFC 0013)

[bu-35fm7]
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.telegram_user_client import (
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

_ENDPOINT = "telegram_user_client:telegram:user123"
_OWNER_ENDPOINT = "telegram:user:999"


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
    # Cache "just now" on the same monotonic clock the connector reads, so the
    # entry stays fresh regardless of wall-clock offset (a fixed sentinel like
    # 999999999.0 is below a faked monotonic clock and spuriously expires).
    connector._participant_count_cache[chat_id] = (15, time.monotonic())

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
    connector._participant_count_cache[chat_id] = (25, time.monotonic())

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
    connector._participant_count_cache[chat_id] = (50, time.monotonic())

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


# ---------------------------------------------------------------------------
# Owner-outbound point-event recording (bu-whhll.8)
# ---------------------------------------------------------------------------


@pytest.fixture
def owner_connector() -> TelegramUserClientConnector:
    """Connector configured with a numeric owner endpoint identity + a mock db_pool."""
    config = TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity=_OWNER_ENDPOINT,
    )
    conn = TelegramUserClientConnector(config, db_pool=AsyncMock(), cursor_pool=MagicMock())
    return conn


async def test_owner_authored_message_records_point_event(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """A message sent BY the owner (sender_id matches endpoint identity) records a point event."""
    msg = _make_message(msg_id=42, chat_id=100, sender_id=999)
    msg.date = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)

    with patch(
        "butlers.connectors.telegram_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(msg)

    mock_record.assert_awaited_once()
    kwargs = mock_record.call_args.kwargs
    assert kwargs["channel"] == "telegram_user_client"
    assert kwargs["provider"] == "telegram"
    assert kwargs["endpoint_identity"] == _OWNER_ENDPOINT
    assert kwargs["occurred_at"] == msg.date
    # Chat/message identifiers may feed the dedup material, but no content
    # and no display name is ever passed through.
    assert "100" in kwargs["dedup_material"]
    assert "42" in kwargs["dedup_material"]


async def test_non_owner_message_does_not_record_point_event(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """A message sent by someone else (sender_id != owner) must never record."""
    msg = _make_message(msg_id=42, chat_id=100, sender_id=111)
    msg.date = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)

    with patch(
        "butlers.connectors.telegram_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(msg)

    mock_record.assert_not_awaited()


async def test_owner_tagging_degrades_gracefully_when_owner_id_unresolvable(
    connector: TelegramUserClientConnector,
) -> None:
    """When endpoint_identity has no resolvable numeric owner id, never record."""
    msg = _make_message(msg_id=42, chat_id=100, sender_id=999)
    msg.date = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)

    with patch(
        "butlers.connectors.telegram_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await connector._record_owner_outbound_if_applicable(msg)

    mock_record.assert_not_awaited()


async def test_missing_message_date_does_not_record(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """A message with no usable timestamp must never record (never fabricate 'now')."""
    msg = _make_message(msg_id=42, chat_id=100, sender_id=999)
    msg.date = None

    with patch(
        "butlers.connectors.telegram_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(msg)

    mock_record.assert_not_awaited()


# ---------------------------------------------------------------------------
# Discretion auth health surfaced on /status (bu-ur7go)
# ---------------------------------------------------------------------------


def test_get_health_state_degrades_on_discretion_auth_failure(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """A degraded discretion auth-health snapshot must surface as an overall
    "degraded" connector health state (bu-ofo3i: /status reported healthy
    while every discretion call 401'd)."""
    owner_connector._telegram_client = MagicMock()
    owner_connector._telegram_client.is_connected.return_value = True
    assert owner_connector._discretion_dispatcher is not None
    owner_connector._discretion_dispatcher.get_auth_health = MagicMock(
        return_value={
            "runtime_type": "codex",
            "auth_file_present": False,
            "last_discretion_success_at": None,
            "last_auth_failure_at": "2026-07-06T00:00:00+00:00",
            "status": "degraded",
        }
    )

    state, error_msg = owner_connector._get_health_state()

    assert state == "degraded"
    assert error_msg is not None
    assert "discretion auth degraded" in error_msg


def test_get_health_state_healthy_when_discretion_auth_ok(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """A healthy discretion auth-health snapshot must not force a degraded
    overall state."""
    owner_connector._telegram_client = MagicMock()
    owner_connector._telegram_client.is_connected.return_value = True
    assert owner_connector._discretion_dispatcher is not None
    owner_connector._discretion_dispatcher.get_auth_health = MagicMock(
        return_value={
            "runtime_type": "codex",
            "auth_file_present": True,
            "last_discretion_success_at": "2026-07-06T00:00:00+00:00",
            "last_auth_failure_at": None,
            "status": "ok",
        }
    )

    state, error_msg = owner_connector._get_health_state()

    assert state == "healthy"
    assert error_msg is None


# ---------------------------------------------------------------------------
# Filtered-content privacy tier (bu-it77x)
#
# Content the connector deliberately does NOT submit (status='filtered':
# policy block, global skip, discretion IGNORE) persists a bounded preview
# only — the full raw message payload MUST NOT be retained. Errored content
# (status='error') is exempt and keeps its payload for diagnosis/replay.
# This brings telegram_user_client into line with the WhatsApp posture.
# ---------------------------------------------------------------------------


def _allow_decision() -> SimpleNamespace:
    return SimpleNamespace(allowed=True, action="pass_through", reason="", matched_rule_type=None)


async def test_policy_block_persists_no_raw_payload(
    connector: TelegramUserClientConnector,
) -> None:
    """A connector-scope policy block records a filtered event whose
    full_payload.raw is empty and whose preview is bounded to 200 chars."""
    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            allowed=False, action="block", reason="blocked", matched_rule_type="sender_domain"
        )
    )
    connector._filtered_event_buffer.record = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    msg = _make_message(msg_id=7, chat_id=100, sender_id=999, text="z" * 500)
    await connector._process_message(msg)

    connector._filtered_event_buffer.record.assert_called_once()
    kwargs = connector._filtered_event_buffer.record.call_args.kwargs
    assert kwargs["filter_reason"] == "connector_rule:block:sender_domain"
    assert kwargs["full_payload"]["payload"]["raw"] == {}
    assert kwargs["subject_or_preview"] == "z" * 200


async def test_global_skip_persists_no_raw_payload(
    connector: TelegramUserClientConnector,
) -> None:
    """A global-scope skip records a filtered event with an empty raw payload."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            allowed=True, action="skip", reason="skip", matched_rule_type="keyword"
        )
    )
    connector._filtered_event_buffer.record = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    msg = _make_message(msg_id=8, chat_id=100, sender_id=999, text="hello")
    await connector._process_message(msg)

    connector._filtered_event_buffer.record.assert_called_once()
    kwargs = connector._filtered_event_buffer.record.call_args.kwargs
    assert kwargs["filter_reason"] == "global_rule:skip:keyword"
    assert kwargs["full_payload"]["payload"]["raw"] == {}


async def test_error_status_retains_full_raw_payload(
    connector: TelegramUserClientConnector,
) -> None:
    """Errored content (status='error') is exempt from the privacy tier and
    keeps its full raw payload for diagnosis and replay."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._discretion_dispatcher = None
    connector._normalize_to_ingest_v1 = AsyncMock(  # type: ignore[method-assign]
        return_value={"schema_version": "ingest.v1"}
    )
    connector._submit_to_ingest = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    connector._filtered_event_buffer.record = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    msg = _make_message(msg_id=9, chat_id=100, sender_id=999, text="hi")
    await connector._process_message(msg)

    connector._filtered_event_buffer.record.assert_called_once()
    kwargs = connector._filtered_event_buffer.record.call_args.kwargs
    assert kwargs["status"] == "error"
    assert kwargs["full_payload"]["payload"]["raw"] == {"id": 9, "message": "hi"}
