"""Condensed Telegram user-client connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for text, media messages
- Idempotency key format (canonical: tg:<chat_id>:<message_id>)
- Participant count + chat type enrichment (RFC 0013)
- Interaction eligibility gating for large groups (RFC 0013)

[bu-35fm7]
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import butlers.connectors.telegram_user_client as telegram_user_client
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


@pytest.mark.parametrize(
    ("configured_port", "expected_port"),
    [(None, 40080), (40123, 40123)],
)
def test_config_reads_loopback_health_port(
    monkeypatch: pytest.MonkeyPatch,
    configured_port: int | None,
    expected_port: int,
) -> None:
    """The local health endpoint uses its documented default or env override."""
    monkeypatch.setattr(telegram_user_client, "TELETHON_AVAILABLE", True)
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    if configured_port is None:
        monkeypatch.delenv("CONNECTOR_HEALTH_PORT", raising=False)
    else:
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", str(configured_port))

    config = TelegramUserClientConnectorConfig.from_env()

    assert config.health_port == expected_port


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
        backfill_window_h=24,
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


async def test_backfill_records_owner_points_with_live_replay_dedup_and_no_content(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """Startup replay preserves the live recorder's owner/privacy/dedup contract."""
    owner_message = _make_message(
        msg_id=42,
        chat_id=100,
        sender_id=999,
        text="private replay content must never reach the point event",
    )
    owner_message.date = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)
    non_owner_message = _make_message(
        msg_id=43,
        chat_id=100,
        sender_id=111,
        text="other participant content must not create an owner point",
    )
    non_owner_message.date = owner_message.date

    async def iter_dialogs():
        yield SimpleNamespace(id=100)

    async def iter_messages(_dialog, **_kwargs):
        yield owner_message
        yield non_owner_message

    telegram_client = MagicMock()
    telegram_client.iter_dialogs = iter_dialogs
    telegram_client.iter_messages = iter_messages
    owner_connector._telegram_client = telegram_client
    owner_connector._process_message = AsyncMock()  # type: ignore[method-assign]

    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=["live-row", None])
    owner_connector._db_pool = pool

    # A live observation records the event first.  The replay of the same
    # message must use the same hashed key, so SQL ON CONFLICT keeps one row.
    await owner_connector._record_owner_outbound_if_applicable(owner_message)
    await owner_connector._perform_backfill()

    assert owner_connector._process_message.await_count == 2
    assert pool.fetchval.await_count == 2
    live_params = pool.fetchval.await_args_list[0].args[1:]
    replay_params = pool.fetchval.await_args_list[1].args[1:]
    assert live_params == replay_params
    assert len(replay_params) == 4
    idempotency_key, channel, endpoint_identity, occurred_at = replay_params
    assert "private replay content" not in idempotency_key
    assert "other participant content" not in idempotency_key
    assert channel == "telegram_user_client"
    assert endpoint_identity == _OWNER_ENDPOINT
    assert occurred_at == owner_message.date
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in pool.fetchval.await_args.args[0]


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
# Local health endpoint
# ---------------------------------------------------------------------------


def _unused_loopback_port() -> int:
    """Return an ephemeral loopback port currently available to this test."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _get_health_response(port: int) -> tuple[bytes, dict[str, object]]:
    """Request the local health endpoint, waiting briefly for its server task."""
    for _ in range(50):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.01)
            continue

        try:
            writer.write(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

        headers, body = response.split(b"\r\n\r\n", maxsplit=1)
        return headers.splitlines()[0], json.loads(body)

    pytest.fail("Telegram user-client local health server did not accept a connection")


async def _health_payload(
    connector: TelegramUserClientConnector,
) -> tuple[bytes, dict[str, object]]:
    health_server = getattr(telegram_user_client, "_run_health_server", None)
    assert health_server is not None, "Telegram user-client must expose a local health server"

    port = _unused_loopback_port()
    task = asyncio.create_task(health_server(port, connector))
    try:
        return await _get_health_response(port)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_health_endpoint_serves_local_connector_status(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """The local endpoint exposes the connector's basic operational status."""
    owner_connector._telegram_client = MagicMock()
    owner_connector._telegram_client.is_connected.return_value = True
    owner_connector._discretion_dispatcher = None

    status_line, payload = await _health_payload(owner_connector)

    assert status_line == b"HTTP/1.0 200 OK"
    assert payload == {
        "status": "healthy",
        "connector_type": "telegram_user_client",
        "endpoint_identity": _OWNER_ENDPOINT,
    }


async def test_health_endpoint_exposes_raw_discretion_auth_snapshot(
    owner_connector: TelegramUserClientConnector,
) -> None:
    """The endpoint returns the dispatcher's unmodified auth-health snapshot."""
    owner_connector._telegram_client = MagicMock()
    owner_connector._telegram_client.is_connected.return_value = True
    assert owner_connector._discretion_dispatcher is not None
    auth_snapshot = {
        "runtime_type": "codex",
        "auth_file_present": False,
        "last_discretion_success_at": None,
        "last_auth_failure_at": "2026-07-06T00:00:00+00:00",
        "status": "degraded",
    }
    owner_connector._discretion_dispatcher.get_auth_health = MagicMock(return_value=auth_snapshot)

    status_line, payload = await _health_payload(owner_connector)

    assert status_line == b"HTTP/1.0 200 OK"
    assert payload["status"] == "degraded"
    assert payload["discretion_auth"] == auth_snapshot


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


# ---------------------------------------------------------------------------
# Group-size discretion bypass wiring (live single-message path)
#
# Small/family-sized groups must skip the discretion LLM entirely and always
# submit, independent of content — the discretion system prompt otherwise
# instructs the LLM to IGNORE "group banter", which starves passive
# interaction sync of exactly the low-content chatter Dunbar scoring needs
# for small groups. Large groups must keep full LLM-gated filtering so token
# cost stays bounded on high-volume/mass-membership chats.
# ---------------------------------------------------------------------------


async def test_live_path_small_group_bypasses_discretion(
    connector: TelegramUserClientConnector,
) -> None:
    """A message in a group at/under the threshold FORWARDs without an LLM call."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    connector._discretion_dispatcher = dispatcher
    # Force sender weight below weight_bypass (1.0) so only the group-size
    # bypass (not the pre-existing weight bypass) can explain a skipped LLM call.
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)

    chat_id = "700"
    connector._participant_count_cache[chat_id] = (8, time.monotonic())

    connector._normalize_to_ingest_v1 = AsyncMock(  # type: ignore[method-assign]
        return_value={"schema_version": "ingest.v1"}
    )
    connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
    connector._filtered_event_buffer.record = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    msg = _make_message(msg_id=10, chat_id=int(chat_id), sender_id=999, text="lol nice")
    await connector._process_message(msg)

    dispatcher.call.assert_not_called()
    connector._submit_to_ingest.assert_awaited_once()
    connector._filtered_event_buffer.record.assert_not_called()


async def test_live_path_large_group_still_runs_discretion(
    connector: TelegramUserClientConnector,
) -> None:
    """A message in a group over the threshold still calls the LLM and honours IGNORE."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    connector._discretion_dispatcher = dispatcher
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)

    chat_id = "701"
    connector._participant_count_cache[chat_id] = (300, time.monotonic())

    connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
    connector._filtered_event_buffer.record = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    msg = _make_message(msg_id=11, chat_id=int(chat_id), sender_id=999, text="ambient chatter")
    await connector._process_message(msg)

    dispatcher.call.assert_awaited_once()
    connector._submit_to_ingest.assert_not_called()
    connector._filtered_event_buffer.record.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_batch_weight
#
# The batch-flush path used to hardcode weight=1.0, which always satisfied
# weight_bypass (default 1.0) — discretion never ran on that path at all,
# and the group-size bypass added alongside it could never be reached
# either. _resolve_batch_weight restores real per-batch weight resolution,
# mirroring WhatsAppUserClientConnector's identical method.
# ---------------------------------------------------------------------------


async def test_resolve_batch_weight_no_resolver_returns_one(
    connector: TelegramUserClientConnector,
) -> None:
    """No weight resolver configured (no DB pool) → defaults to 1.0."""
    assert connector._weight_resolver is None

    weight = await connector._resolve_batch_weight({"111", "222"}, owner_sender_id=None)

    assert weight == 1.0


async def test_resolve_batch_weight_excludes_owner(
    connector: TelegramUserClientConnector,
) -> None:
    """The owner's sender ID must not be weighed — only the other side."""
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.3)

    await connector._resolve_batch_weight({"999", "111"}, owner_sender_id="999")

    connector._weight_resolver.resolve.assert_awaited_once_with("telegram", "111")


async def test_resolve_batch_weight_owner_only_returns_one_without_resolving(
    connector: TelegramUserClientConnector,
) -> None:
    """A batch where only the owner spoke has nobody left to weigh → 1.0."""
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.3)

    weight = await connector._resolve_batch_weight({"999"}, owner_sender_id="999")

    assert weight == 1.0
    connector._weight_resolver.resolve.assert_not_called()


async def test_resolve_batch_weight_takes_max_across_senders(
    connector: TelegramUserClientConnector,
) -> None:
    """A batch with a mix of senders is weighed by the highest, not the last."""
    connector._weight_resolver = AsyncMock()
    weights = {"111": 0.3, "222": 0.9}
    connector._weight_resolver.resolve = AsyncMock(side_effect=lambda _type, sid: weights[sid])

    weight = await connector._resolve_batch_weight({"111", "222"}, owner_sender_id=None)

    assert weight == 0.9


# ---------------------------------------------------------------------------
# Group-size discretion bypass wiring (batch-flush path)
#
# Mirrors the live-path tests above, but through _flush_chat_buffer — the
# primary/dominant ingestion path for this connector.
# ---------------------------------------------------------------------------


async def _prime_flush_buffer(
    connector: TelegramUserClientConnector, chat_id: str, msg: MagicMock
) -> None:
    """Populate the chat buffer and stub out the Telethon-backed history fetch."""
    from butlers.connectors.telegram_user_client import ChatBuffer

    connector._chat_buffers[chat_id] = ChatBuffer(messages=[msg])
    connector._fetch_conversation_history = AsyncMock(return_value=[msg])  # type: ignore[method-assign]
    connector._resolve_reply_tos = AsyncMock(return_value=[msg])  # type: ignore[method-assign]


async def test_batch_flush_small_group_bypasses_discretion(
    connector: TelegramUserClientConnector,
) -> None:
    """A batch flush in a group at/under the threshold FORWARDs without an LLM call."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    connector._discretion_dispatcher = dispatcher
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)

    chat_id = "710"
    connector._participant_count_cache[chat_id] = (8, time.monotonic())
    msg = _make_message(msg_id=20, chat_id=int(chat_id), sender_id=111, text="lol nice")
    await _prime_flush_buffer(connector, chat_id, msg)

    connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
    connector._record_batch_filtered_event = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]
    connector._save_checkpoint = AsyncMock()  # type: ignore[method-assign]

    await connector._flush_chat_buffer(chat_id)

    dispatcher.call.assert_not_called()
    connector._submit_to_ingest.assert_awaited_once()
    connector._record_batch_filtered_event.assert_not_called()


async def test_batch_flush_large_group_still_runs_discretion(
    connector: TelegramUserClientConnector,
) -> None:
    """A batch flush in a group over the threshold still calls the LLM."""
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow_decision())  # type: ignore[method-assign]

    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    connector._discretion_dispatcher = dispatcher
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)

    chat_id = "711"
    connector._participant_count_cache[chat_id] = (300, time.monotonic())
    msg = _make_message(msg_id=21, chat_id=int(chat_id), sender_id=111, text="ambient chatter")
    await _prime_flush_buffer(connector, chat_id, msg)

    connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
    connector._record_batch_filtered_event = MagicMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    await connector._flush_chat_buffer(chat_id)

    dispatcher.call.assert_awaited_once()
    connector._submit_to_ingest.assert_not_called()
    connector._record_batch_filtered_event.assert_called_once()
