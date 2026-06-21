"""Condensed WhatsApp user-client connector tests — ingest.v1 contract only.

Replaces root tests/test_whatsapp_user_client.py.

Verifies:
- ingest.v1 envelope production for single events
- Batch envelope schema_version
- Idempotency key format
- Participant count + chat type enrichment (RFC 0013)
- Interaction eligibility gating for large groups (RFC 0013)

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
    _derive_wa_chat_type,
    _extract_wa_participant_count,
)

_ENDPOINT = "whatsapp:+12025551234"


@pytest.fixture
def connector() -> WhatsAppUserClientConnector:
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity=_ENDPOINT,
    )
    return WhatsAppUserClientConnector(config, cursor_pool=MagicMock())


def test_single_event_envelope_contract(connector: WhatsAppUserClientConnector) -> None:
    """Single event carries ingest.v1 schema, whatsapp source, mapped event/sender fields,
    and the 'whatsapp:<endpoint>:<msg_id>' idempotency key."""
    event: dict[str, Any] = {
        "message_id": "msg-abc",
        "chat_jid": "chat-123",
        "sender_jid": "sender-456",
        "timestamp": 1711447200,
        "type": "text",
        "text": "Hello there!",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "whatsapp_user_client"
    assert env["source"]["provider"] == "whatsapp"
    assert env["source"]["endpoint_identity"] == _ENDPOINT
    assert env["event"]["external_event_id"] == "msg-abc"
    assert env["event"]["external_thread_id"] == "chat-123"
    assert env["sender"]["identity"] == "sender-456"
    key = env["control"]["idempotency_key"]
    assert "whatsapp:" in key
    assert "msg-abc" in key


def test_single_event_passes_parse_ingest_envelope(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Single event envelope must validate against parse_ingest_envelope."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    event: dict[str, Any] = {
        "message_id": "validate-me",
        "chat_jid": "chat-99",
        "sender_jid": "user-1",
        "timestamp": 1711447200,
        "text": "Validation test",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_filtered_event_buffer_uses_runtime_connector_type(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Filtered-event rows must be keyed by the runtime connector type."""
    connector._record_batch_filtered_event(
        chat_jid="chat-99",
        batch_event_id="batch-001",
        filter_reason="discretion:IGNORE",
    )
    assert connector._filtered_event_buffer._rows[0][1] == "whatsapp_user_client"


async def test_flush_and_drain_uses_runtime_connector_type(
    connector: WhatsAppUserClientConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay drain must look up WhatsApp rows by the runtime connector type."""
    connector._db_pool = MagicMock()
    connector._filtered_event_buffer.flush = AsyncMock()
    submit_mock = AsyncMock()
    connector._submit_to_ingest = submit_mock
    drain_mock = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.drain_replay_pending",
        drain_mock,
    )

    await connector._flush_and_drain()

    # Drain must be keyed by the runtime connector type (the behavioral contract).
    drain_mock.assert_awaited_once()
    assert drain_mock.await_args.args[1] == "whatsapp_user_client"


# ---------------------------------------------------------------------------
# Dunbar group-aware interaction gating tests (RFC 0013)
# ---------------------------------------------------------------------------


def test_derive_wa_chat_type_private() -> None:
    """JID ending in @s.whatsapp.net must map to 'private'."""
    assert _derive_wa_chat_type("15551234@s.whatsapp.net") == "private"


def test_derive_wa_chat_type_group() -> None:
    """JID ending in @g.us must map to 'group'."""
    assert _derive_wa_chat_type("1234567890-1234@g.us") == "group"


def test_derive_wa_chat_type_broadcast() -> None:
    """JID ending in @broadcast must map to 'channel'."""
    assert _derive_wa_chat_type("status@broadcast") == "channel"


def test_derive_wa_chat_type_newsletter() -> None:
    """JID ending in @newsletter must map to 'channel'."""
    assert _derive_wa_chat_type("123@newsletter") == "channel"


def test_derive_wa_chat_type_empty() -> None:
    """Empty JID must fall back to 'private'."""
    assert _derive_wa_chat_type("") == "private"


def test_extract_wa_participant_count_from_top_level() -> None:
    """participant_count at top level of event is read correctly."""
    event: dict[str, Any] = {
        "message_id": "m1",
        "chat_jid": "123@g.us",
        "participant_count": 15,
    }
    assert _extract_wa_participant_count(event) == 15


def test_extract_wa_participant_count_from_content() -> None:
    """participant_count nested in event.content is read correctly."""
    event: dict[str, Any] = {
        "message_id": "m2",
        "chat_jid": "456@g.us",
        "content": {"participant_count": 8, "text": "hello"},
    }
    assert _extract_wa_participant_count(event) == 8


def test_extract_wa_participant_count_absent() -> None:
    """Events without participant_count return None."""
    event: dict[str, Any] = {"message_id": "m3", "chat_jid": "789@g.us", "text": "hi"}
    assert _extract_wa_participant_count(event) is None


def test_dm_single_event_participant_count_2(connector: WhatsAppUserClientConnector) -> None:
    """DM single events must have participant_count=2 and chat_type='private'."""
    event: dict[str, Any] = {
        "message_id": "dm-001",
        "chat_jid": "15551234@s.whatsapp.net",
        "sender_jid": "15559876@s.whatsapp.net",
        "timestamp": 1711447200,
        "type": "text",
        "text": "Hey!",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["sender"]["participant_count"] == 2
    assert env["sender"]["chat_type"] == "private"
    assert env["control"]["interaction_eligible"] is True


def test_group_single_event_below_threshold_eligible(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Group events with participant_count <= 20 must be interaction_eligible."""
    event: dict[str, Any] = {
        "message_id": "grp-001",
        "chat_jid": "1234@g.us",
        "sender_jid": "111@s.whatsapp.net",
        "timestamp": 1711447200,
        "type": "text",
        "text": "Hello group!",
        "participant_count": 10,
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["sender"]["participant_count"] == 10
    assert env["sender"]["chat_type"] == "group"
    assert env["control"]["interaction_eligible"] is True


def test_group_single_event_above_threshold_not_eligible(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Group events with participant_count > 20 must NOT be interaction_eligible."""
    event: dict[str, Any] = {
        "message_id": "grp-002",
        "chat_jid": "5678@g.us",
        "sender_jid": "222@s.whatsapp.net",
        "timestamp": 1711447200,
        "type": "text",
        "text": "Hello big group!",
        "participant_count": 50,
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["sender"]["participant_count"] == 50
    assert env["sender"]["chat_type"] == "group"
    assert env["control"]["interaction_eligible"] is False


def test_group_batch_below_threshold_eligible(connector: WhatsAppUserClientConnector) -> None:
    """Batch envelope for groups at or below threshold must be interaction_eligible."""
    events: list[dict[str, Any]] = [
        {
            "message_id": f"m{i}",
            "chat_jid": "group123@g.us",
            "sender_jid": f"{i}@s.whatsapp.net",
            "type": "text",
            "text": f"msg {i}",
            "participant_count": 5,
        }
        for i in range(3)
    ]
    env = connector._build_batch_envelope("group123@g.us", events, "batch-001")
    assert env["sender"]["participant_count"] == 5
    assert env["sender"]["chat_type"] == "group"
    assert env["control"]["interaction_eligible"] is True


def test_group_batch_above_threshold_not_eligible(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Batch envelope for large groups must have interaction_eligible=False."""
    events: list[dict[str, Any]] = [
        {
            "message_id": f"m{i}",
            "chat_jid": "biggroup@g.us",
            "sender_jid": f"{i}@s.whatsapp.net",
            "type": "text",
            "text": f"msg {i}",
            "participant_count": 25,
        }
        for i in range(3)
    ]
    env = connector._build_batch_envelope("biggroup@g.us", events, "batch-002")
    assert env["sender"]["participant_count"] == 25
    assert env["control"]["interaction_eligible"] is False


def test_batch_envelope_large_group_passes_parse(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Large-group batch envelope must still validate against parse_ingest_envelope."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    events: list[dict[str, Any]] = [
        {
            "message_id": f"m{i}",
            "chat_jid": "huge@g.us",
            "sender_jid": f"{i}@s.whatsapp.net",
            "type": "text",
            "text": f"msg {i}",
            "participant_count": 100,
        }
        for i in range(2)
    ]
    env = connector._build_batch_envelope("huge@g.us", events, "batch-100")
    assert env["control"]["interaction_eligible"] is False
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_group_with_no_participant_count_in_event_defaults_eligible(
    connector: WhatsAppUserClientConnector,
) -> None:
    """Group events without participant_count in bridge event default to interaction_eligible=True.

    The bridge may not include participant_count; in this case we cannot gate.
    """
    events: list[dict[str, Any]] = [
        {
            "message_id": f"m{i}",
            "chat_jid": "unknown-size@g.us",
            "sender_jid": f"{i}@s.whatsapp.net",
            "type": "text",
            "text": f"msg {i}",
        }
        for i in range(2)
    ]
    env = connector._build_batch_envelope("unknown-size@g.us", events, "batch-003")
    # participant_count is None (bridge didn't report it for groups)
    assert env["sender"]["participant_count"] is None
    assert env["control"]["interaction_eligible"] is True


# ---------------------------------------------------------------------------
# Stale-link watchdog
# ---------------------------------------------------------------------------


def _connector_with_threshold(threshold_s: int) -> WhatsAppUserClientConnector:
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        endpoint_identity=_ENDPOINT,
        stale_restart_threshold_s=threshold_s,
    )
    return WhatsAppUserClientConnector(config, cursor_pool=MagicMock())


def test_link_not_stale_without_bridge_manager() -> None:
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = None
    assert connector._link_is_stale() is False


def test_link_not_stale_when_link_healthy() -> None:
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = None
    assert connector._link_is_stale() is False


def test_link_not_stale_below_threshold() -> None:
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 120.0
    assert connector._link_is_stale() is False


def test_link_stale_at_or_above_threshold() -> None:
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 3600.0
    connector._bridge_manager.is_degraded_terminal = False
    assert connector._link_is_stale() is True


def test_link_watchdog_disabled_when_threshold_zero() -> None:
    connector = _connector_with_threshold(0)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 999999.0
    assert connector._link_is_stale() is False


async def test_restart_for_stale_link_flushes_then_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a stale-link restart, buffers are flushed best-effort before exiting."""
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_reason = "Link down (session taken over?)"
    connector._bridge_manager.degraded_duration_s = 3601.0

    flush = AsyncMock()
    exit_seam = MagicMock()
    monkeypatch.setattr(connector, "_flush_all_buffers", flush)
    monkeypatch.setattr(connector, "_exit_process", exit_seam)

    await connector._restart_for_stale_link()

    flush.assert_awaited_once()
    exit_seam.assert_called_once()


async def test_restart_for_stale_link_exits_even_if_flush_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed flush must not prevent the restart exit."""
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_reason = "down"
    connector._bridge_manager.degraded_duration_s = 3601.0

    monkeypatch.setattr(
        connector, "_flush_all_buffers", AsyncMock(side_effect=RuntimeError("boom"))
    )
    exit_seam = MagicMock()
    monkeypatch.setattr(connector, "_exit_process", exit_seam)

    await connector._restart_for_stale_link()
    exit_seam.assert_called_once()


async def test_watchdog_loop_triggers_restart_when_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watchdog loop calls the restart path once the link is stale."""
    import butlers.connectors.whatsapp_user_client as wac

    connector = _connector_with_threshold(3600)
    connector._running = True
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 4000.0
    connector._bridge_manager.is_degraded_terminal = False
    connector._bridge_manager.degraded_reason = "down"

    monkeypatch.setattr(wac, "_LINK_WATCHDOG_INTERVAL_S", 0)
    restart = AsyncMock()
    monkeypatch.setattr(connector, "_restart_for_stale_link", restart)

    await connector._link_watchdog_loop()
    restart.assert_awaited_once()


async def test_watchdog_loop_exits_cleanly_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy link keeps the watchdog idling until cancelled."""
    import asyncio

    import butlers.connectors.whatsapp_user_client as wac

    connector = _connector_with_threshold(3600)
    connector._running = True
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = None  # healthy

    monkeypatch.setattr(wac, "_LINK_WATCHDOG_INTERVAL_S", 0.01)

    task = asyncio.create_task(connector._link_watchdog_loop())
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_link_not_stale_when_degraded_terminal() -> None:
    """A terminal degraded state (needs re-pair) must not trip the watchdog."""
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 99999.0
    connector._bridge_manager.is_degraded_terminal = True
    assert connector._link_is_stale() is False


def test_link_stale_when_recoverable_past_threshold() -> None:
    """A recoverable outage past threshold does trip the watchdog."""
    connector = _connector_with_threshold(3600)
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 3601.0
    connector._bridge_manager.is_degraded_terminal = False
    assert connector._link_is_stale() is True
