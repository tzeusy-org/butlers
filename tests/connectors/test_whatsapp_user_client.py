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

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.whatsapp_user_client import (
    _SSE_PAIRING_WAIT_TIMEOUT_S,
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


async def test_watchdog_loop_survives_invalidated_session_check_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception from the invalidated-session check (e.g. a
    malformed settings row) must not kill the watchdog loop — that would
    silently disable both invalidated-session alerting AND the stale-link
    restart check for the rest of the connector process's lifetime, which
    is exactly the "nothing ever tells the owner" failure mode bu-5ocmh
    exists to fix, just from a different code path."""
    import butlers.connectors.whatsapp_user_client as wac

    connector = _connector_with_threshold(3600)
    connector._running = True
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.degraded_duration_s = 4000.0
    connector._bridge_manager.is_degraded_terminal = False
    connector._bridge_manager.degraded_reason = "down"

    monkeypatch.setattr(wac, "_LINK_WATCHDOG_INTERVAL_S", 0)
    monkeypatch.setattr(
        connector,
        "_check_invalidated_session_state",
        AsyncMock(side_effect=AttributeError("'str' object has no attribute 'get'")),
    )
    restart = AsyncMock()
    monkeypatch.setattr(connector, "_restart_for_stale_link", restart)

    await connector._link_watchdog_loop()  # must not raise

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


# ---------------------------------------------------------------------------
# Owner-outbound point-event recording (bu-whhll.8)
# ---------------------------------------------------------------------------


@pytest.fixture
def owner_connector() -> WhatsAppUserClientConnector:
    """Connector with a mock db_pool for owner-outbound recording tests."""
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity=_ENDPOINT,
    )
    return WhatsAppUserClientConnector(config, db_pool=AsyncMock(), cursor_pool=MagicMock())


async def test_is_from_me_event_records_point_event(
    owner_connector: WhatsAppUserClientConnector,
) -> None:
    """A bridge event tagged raw.is_from_me=true records a point event."""
    event: dict[str, Any] = {
        "message_id": "msg-1",
        "chat_jid": "chat-abc",
        "sender_jid": "owner-jid",
        "timestamp": 1751709600,
        "type": "text",
        "text": "hello",
        "raw": {"is_from_me": True, "is_group": False},
    }

    with patch(
        "butlers.connectors.whatsapp_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(event, "chat-abc")

    mock_record.assert_awaited_once()
    kwargs = mock_record.call_args.kwargs
    assert kwargs["channel"] == "whatsapp_user_client"
    assert kwargs["provider"] == "whatsapp"
    assert kwargs["endpoint_identity"] == _ENDPOINT
    assert "chat-abc" in kwargs["dedup_material"]
    assert "msg-1" in kwargs["dedup_material"]


async def test_inbound_event_does_not_record_point_event(
    owner_connector: WhatsAppUserClientConnector,
) -> None:
    """A bridge event from someone else (is_from_me=false) must never record."""
    event: dict[str, Any] = {
        "message_id": "msg-2",
        "chat_jid": "chat-abc",
        "sender_jid": "someone-else",
        "timestamp": 1751709600,
        "type": "text",
        "text": "hi",
        "raw": {"is_from_me": False, "is_group": False},
    }

    with patch(
        "butlers.connectors.whatsapp_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(event, "chat-abc")

    mock_record.assert_not_awaited()


async def test_missing_raw_field_does_not_record(
    owner_connector: WhatsAppUserClientConnector,
) -> None:
    """An event with no 'raw' summary (e.g. malformed bridge payload) must never record."""
    event: dict[str, Any] = {
        "message_id": "msg-3",
        "chat_jid": "chat-abc",
        "timestamp": 1751709600,
        "type": "text",
        "text": "hi",
    }

    with patch(
        "butlers.connectors.whatsapp_user_client.record_owner_outbound_point",
        new=AsyncMock(return_value=True),
    ) as mock_record:
        await owner_connector._record_owner_outbound_if_applicable(event, "chat-abc")

    mock_record.assert_not_awaited()


async def test_history_replay_sse_event_uses_owner_metadata_dedup_contract() -> None:
    """A replayed bridge event takes the same privacy-safe path as a live event."""
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity=_ENDPOINT,
    )
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=["live-row", None])
    connector = WhatsAppUserClientConnector(config, db_pool=pool, cursor_pool=MagicMock())
    connector._buffer_event = AsyncMock()  # type: ignore[method-assign]

    event: dict[str, Any] = {
        "message_id": "msg-1",
        "chat_jid": "chat-abc",
        "sender_jid": "owner-jid",
        "timestamp": 1751709600,
        "type": "text",
        "text": "private replay content must never reach the point event",
        "raw": {"is_from_me": True, "is_group": False},
    }

    await connector._handle_bridge_event(event)
    # A bridge history replay returns through the normal SSE stream, so the
    # connector receives the same event shape a second time here.
    await connector._handle_bridge_event(event)

    assert connector._buffer_event.await_count == 2
    assert pool.fetchval.await_count == 2
    live_params = pool.fetchval.await_args_list[0].args[1:]
    replay_params = pool.fetchval.await_args_list[1].args[1:]
    assert live_params == replay_params
    assert len(replay_params) == 4
    idempotency_key, channel, endpoint_identity, occurred_at = replay_params
    assert "private replay content" not in idempotency_key
    assert channel == "whatsapp_user_client"
    assert endpoint_identity == _ENDPOINT
    assert occurred_at == datetime.fromtimestamp(1751709600, UTC)
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in pool.fetchval.await_args.args[0]


# ---------------------------------------------------------------------------
# Invalidated-session alerting + owner-triggered recovery (bu-5ocmh)
# ---------------------------------------------------------------------------


def _connector_with_mocks(**config_kwargs) -> WhatsAppUserClientConnector:
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        endpoint_identity=_ENDPOINT,
        **config_kwargs,
    )
    connector = WhatsAppUserClientConnector(config, db_pool=MagicMock(), cursor_pool=MagicMock())
    connector._mcp_client = MagicMock()
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "ok"})
    return connector


async def test_check_invalidated_session_state_noop_without_bridge_manager() -> None:
    connector = _connector_with_mocks()
    connector._bridge_manager = None
    await connector._check_invalidated_session_state()  # must not raise


async def test_check_invalidated_session_state_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The alert fires exactly once per invalidation episode, not every tick."""
    connector = _connector_with_mocks()
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.is_invalidated_session = True
    connector._bridge_manager.is_degraded_terminal = True

    alert = AsyncMock()
    monkeypatch.setattr(connector, "_send_invalidated_session_alert", alert)
    monkeypatch.setattr(connector, "_maybe_perform_pair_reset", AsyncMock())

    await connector._check_invalidated_session_state()
    await connector._check_invalidated_session_state()
    await connector._check_invalidated_session_state()

    alert.assert_awaited_once()
    assert connector._invalidated_session_alert_sent is True


async def test_check_invalidated_session_state_resets_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the session is no longer invalidated, the alert flag resets so a
    future invalidation episode can alert again."""
    connector = _connector_with_mocks()
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.is_invalidated_session = False
    connector._invalidated_session_alert_sent = True
    monkeypatch.setattr(connector, "_maybe_perform_pair_reset", AsyncMock())

    await connector._check_invalidated_session_state()

    assert connector._invalidated_session_alert_sent is False


async def test_send_invalidated_session_alert_calls_deliver_tool() -> None:
    """Alerting goes through Switchboard's `deliver` tool directly (this runs
    outside any butler daemon, so the full notify() tool isn't reachable)."""
    connector = _connector_with_mocks()

    await connector._send_invalidated_session_alert()

    connector._mcp_client.call_tool.assert_awaited_once()
    tool_name, payload = connector._mcp_client.call_tool.call_args.args
    assert tool_name == "deliver"
    assert payload["source_butler"] == "whatsapp_user_client"
    assert payload["notify_request"]["schema_version"] == "notify.v1"
    assert payload["notify_request"]["delivery"]["channel"] == "telegram"


async def test_send_invalidated_session_alert_never_raises_on_failure() -> None:
    """A delivery failure (exception or {"status": "failed"}) must never
    propagate — alerting failures must not affect ingestion."""
    connector = _connector_with_mocks()
    connector._mcp_client.call_tool = AsyncMock(side_effect=ConnectionError("switchboard down"))

    await connector._send_invalidated_session_alert()  # must not raise


async def test_send_invalidated_session_alert_records_attention_ledger_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _connector_with_mocks()
    record = AsyncMock()
    monkeypatch.setattr("butlers.core.attention_ledger.record_attention_event", record)

    await connector._send_invalidated_session_alert()

    record.assert_awaited_once()
    assert record.call_args.kwargs["origin_butler"] == "whatsapp_user_client"
    assert record.call_args.kwargs["outcome"] == "delivered"
    assert record.call_args.kwargs["reason"] == "whatsapp_invalidated_session"


async def test_maybe_perform_pair_reset_noop_when_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy (or merely transiently-degraded, non-terminal) session must
    never be cleared, even if a stale reset flag lingers in settings."""
    connector = _connector_with_mocks()
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.is_degraded_terminal = False

    load_settings = AsyncMock()
    monkeypatch.setattr("butlers.connectors.cursor_store.load_connector_settings", load_settings)

    await connector._maybe_perform_pair_reset()

    load_settings.assert_not_awaited()


async def test_maybe_perform_pair_reset_noop_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = _connector_with_mocks()
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.is_degraded_terminal = True

    monkeypatch.setattr(
        "butlers.connectors.cursor_store.load_connector_settings",
        AsyncMock(return_value={}),
    )
    reset = AsyncMock()
    monkeypatch.setattr(connector, "_perform_pair_reset", reset)

    await connector._maybe_perform_pair_reset()

    reset.assert_not_awaited()


async def test_maybe_perform_pair_reset_triggers_once_per_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending pair_reset_requested_at flag triggers recovery exactly once;
    re-reading the same (unchanged) flag value on the next tick must not
    trigger it again."""
    connector = _connector_with_mocks()
    connector._bridge_manager = MagicMock()
    connector._bridge_manager.is_degraded_terminal = True

    monkeypatch.setattr(
        "butlers.connectors.cursor_store.load_connector_settings",
        AsyncMock(return_value={"pair_reset_requested_at": "2026-07-05T12:00:00+00:00"}),
    )
    reset = AsyncMock()
    monkeypatch.setattr(connector, "_perform_pair_reset", reset)

    await connector._maybe_perform_pair_reset()
    await connector._maybe_perform_pair_reset()

    reset.assert_awaited_once()


async def test_clear_whatsmeow_device_store_executes_delete() -> None:
    connector = _connector_with_mocks()
    connector._cursor_pool = AsyncMock()

    await connector._clear_whatsmeow_device_store()

    connector._cursor_pool.execute.assert_awaited_once()
    (sql,) = connector._cursor_pool.execute.call_args.args
    assert "DELETE FROM public.whatsmeow_device" in sql


async def test_clear_whatsmeow_device_store_raises_without_pool() -> None:
    connector = _connector_with_mocks()
    connector._cursor_pool = None

    with pytest.raises(RuntimeError):
        await connector._clear_whatsmeow_device_store()


async def test_perform_pair_reset_stops_clears_and_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full owner-triggered recovery sequence: stop, clear the device
    store, then rebuild+start a fresh BridgeSubprocessManager configured to
    accept a terminal pair_required outcome as a normal result."""
    connector = _connector_with_mocks()
    old_manager = AsyncMock()
    connector._bridge_manager = old_manager

    clear_store = AsyncMock()
    monkeypatch.setattr(connector, "_clear_whatsmeow_device_store", clear_store)
    monkeypatch.setattr("butlers.connectors.cursor_store.save_connector_settings", AsyncMock())

    new_manager = AsyncMock()
    created_configs = []

    def _fake_bridge_manager_ctor(cfg):
        created_configs.append(cfg)
        return new_manager

    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        _fake_bridge_manager_ctor,
    )

    connector._invalidated_session_alert_sent = True
    await connector._perform_pair_reset()

    old_manager.stop.assert_awaited_once()
    clear_store.assert_awaited_once()
    new_manager.start.assert_awaited_once()
    assert connector._bridge_manager is new_manager
    assert created_configs[0].startup_allow_degraded is True
    # Forgetting the alert episode lets a still-stuck (or freshly re-invalidated)
    # bridge alert again rather than staying silently suppressed forever.
    assert connector._invalidated_session_alert_sent is False


async def test_perform_pair_reset_clears_persisted_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted pair_reset_requested_at flag must be cleared once acted
    on — otherwise a later, unrelated connector process restart (redeploy,
    crash, host reboot) would re-read the same stale timestamp as if it were
    a brand-new request and wipe an already-healthy, already-repaired
    device (the in-memory `_last_pair_reset_handled_at` guard alone does not
    survive a process restart)."""
    connector = _connector_with_mocks()
    connector._bridge_manager = AsyncMock()
    monkeypatch.setattr(connector, "_clear_whatsmeow_device_store", AsyncMock())
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        lambda cfg: AsyncMock(),
    )
    save_settings = AsyncMock()
    monkeypatch.setattr("butlers.connectors.cursor_store.save_connector_settings", save_settings)

    await connector._perform_pair_reset()

    save_settings.assert_awaited_once()
    args = save_settings.call_args.args
    assert args[0] is connector._db_pool
    assert args[-1] == {"pair_reset_requested_at": None}


async def test_perform_pair_reset_tolerates_expected_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restarted bridge legitimately waiting in pair_required can still hit
    the outer startup_timeout_s in rare slow-spawn cases — this must be
    treated as an acceptable outcome, not an unhandled crash."""
    connector = _connector_with_mocks()
    connector._bridge_manager = AsyncMock()
    monkeypatch.setattr(connector, "_clear_whatsmeow_device_store", AsyncMock())
    monkeypatch.setattr("butlers.connectors.cursor_store.save_connector_settings", AsyncMock())

    new_manager = AsyncMock()
    new_manager.start = AsyncMock(side_effect=TimeoutError("bridge did not start in time"))
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        lambda cfg: new_manager,
    )

    await connector._perform_pair_reset()  # must not raise


async def test_perform_pair_reset_restarts_even_if_clear_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DB failure while clearing the device store must not skip
    the restart — otherwise the connector is left with no bridge at all
    until the owner notices and retries."""
    connector = _connector_with_mocks()
    connector._bridge_manager = AsyncMock()
    monkeypatch.setattr(
        connector,
        "_clear_whatsmeow_device_store",
        AsyncMock(side_effect=RuntimeError("db blip")),
    )
    monkeypatch.setattr("butlers.connectors.cursor_store.save_connector_settings", AsyncMock())

    new_manager = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        lambda cfg: new_manager,
    )

    await connector._perform_pair_reset()  # must not raise

    new_manager.start.assert_awaited_once()
    assert connector._bridge_manager is new_manager


async def test_perform_pair_reset_restarts_even_if_stop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure stopping the old bridge must not abort the whole recovery."""
    connector = _connector_with_mocks()
    old_manager = AsyncMock()
    old_manager.stop = AsyncMock(side_effect=RuntimeError("stop blew up"))
    connector._bridge_manager = old_manager
    monkeypatch.setattr(connector, "_clear_whatsmeow_device_store", AsyncMock())
    monkeypatch.setattr("butlers.connectors.cursor_store.save_connector_settings", AsyncMock())

    new_manager = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        lambda cfg: new_manager,
    )

    await connector._perform_pair_reset()  # must not raise

    new_manager.start.assert_awaited_once()
    assert connector._bridge_manager is new_manager


async def test_perform_pair_reset_survives_flag_clear_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DB failure while clearing the persisted flag must not
    abort the stop/clear/restart recovery sequence."""
    connector = _connector_with_mocks()
    connector._bridge_manager = AsyncMock()
    monkeypatch.setattr(connector, "_clear_whatsmeow_device_store", AsyncMock())
    monkeypatch.setattr(
        "butlers.connectors.cursor_store.save_connector_settings",
        AsyncMock(side_effect=RuntimeError("db blip")),
    )

    new_manager = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.BridgeSubprocessManager",
        lambda cfg: new_manager,
    )

    await connector._perform_pair_reset()  # must not raise

    new_manager.start.assert_awaited_once()


async def test_build_bridge_config_defaults_to_strict_startup() -> None:
    """Ordinary boot (not a pair-reset restart) keeps the existing strict
    startup contract: wait for a real 'connected' state."""
    connector = _connector_with_mocks()
    cfg = connector._build_bridge_config()
    assert cfg.startup_allow_degraded is False


async def test_build_bridge_config_allows_degraded_for_recovery() -> None:
    connector = _connector_with_mocks()
    cfg = connector._build_bridge_config(startup_allow_degraded=True)
    assert cfg.startup_allow_degraded is True


# ---------------------------------------------------------------------------
# _sse_event_loop must idle (not tear down) while awaiting QR pairing (bu-7sh43)
# ---------------------------------------------------------------------------


async def test_sse_event_loop_waits_for_bridge_connection_while_awaiting_pairing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bridge sitting in pair_required is a legitimate waiting state, not a
    failure: the SSE loop must wait for its manager's connection signal
    instead of tearing down the bridge or polling on a fixed sleep."""
    connector = _connector_with_mocks()

    async def _wait_until_connected(*, timeout: float) -> None:
        assert timeout == _SSE_PAIRING_WAIT_TIMEOUT_S
        bridge_manager.is_degraded = False
        connector._running = False

    wait_until_connected = AsyncMock(side_effect=_wait_until_connected)
    bridge_manager = SimpleNamespace(
        is_degraded=True,
        is_awaiting_pairing=True,
        degraded_reason="pair_required",
        wait_until_connected=wait_until_connected,
    )
    connector._bridge_manager = bridge_manager
    connector._running = True

    async def _unexpected_sleep(_delay: float) -> None:
        connector._running = False

    sleep = AsyncMock(side_effect=_unexpected_sleep)
    monkeypatch.setattr("butlers.connectors.whatsapp_user_client.asyncio.sleep", sleep)

    await connector._sse_event_loop()  # must return without raising or hanging

    wait_until_connected.assert_awaited_once_with(timeout=_SSE_PAIRING_WAIT_TIMEOUT_S)
    sleep.assert_not_awaited()


async def test_sse_event_loop_stops_for_genuinely_degraded_non_pairing_state() -> None:
    """Non-pairing degraded states (pairing-timeout exit, session invalidated,
    an unreachable bridge) must still stop the SSE loop promptly — only
    pair_required is exempt (bu-7sh43)."""
    connector = _connector_with_mocks()
    bridge_manager = SimpleNamespace(
        is_degraded=True,
        is_awaiting_pairing=False,
        degraded_reason="Session invalidated — re-pair required",
    )
    connector._bridge_manager = bridge_manager
    connector._running = True

    await connector._sse_event_loop()  # must return promptly via break, not idle

    # _sse_event_loop itself only breaks; it does not flip _running (that's
    # connector.stop()'s job, invoked by the caller's finally block).
    assert connector._running is True


# ---------------------------------------------------------------------------
# Pending endpoint_identity must self-heal once the bridge actually connects
# (bu-7sh43 follow-up: start() can now return while still pair_required, so a
# first-time setup's one-shot resolution attempt can no longer be relied on to
# see a phone number).
# ---------------------------------------------------------------------------


async def test_maybe_resolve_pending_endpoint_identity_noop_when_already_resolved() -> None:
    connector = _connector_with_mocks()  # endpoint_identity=_ENDPOINT, not pending
    connector._bridge_manager = SimpleNamespace(get_status=AsyncMock())

    await connector._maybe_resolve_pending_endpoint_identity()

    connector._bridge_manager.get_status.assert_not_awaited()
    assert connector._config.endpoint_identity == _ENDPOINT


async def test_maybe_resolve_pending_endpoint_identity_noop_without_bridge_manager() -> None:
    connector = _connector_with_mocks()
    connector._config.endpoint_identity = "whatsapp:pending"
    connector._bridge_manager = None

    await connector._maybe_resolve_pending_endpoint_identity()  # must not raise

    assert connector._config.endpoint_identity == "whatsapp:pending"


async def test_maybe_resolve_pending_endpoint_identity_stays_pending_while_awaiting_pairing() -> (
    None
):
    """The bridge has no phone to report yet — must not misclassify this as
    a hard failure, and must leave the placeholder in place for the next
    retry rather than raising or logging a false 'connected but no phone'
    warning."""
    connector = _connector_with_mocks()
    connector._config.endpoint_identity = "whatsapp:pending"
    connector._bridge_manager = SimpleNamespace(
        get_status=AsyncMock(return_value={"state": "pair_required"})
    )

    await connector._maybe_resolve_pending_endpoint_identity()

    assert connector._config.endpoint_identity == "whatsapp:pending"


async def test_maybe_resolve_pending_endpoint_identity_resolves_once_bridge_reports_phone() -> None:
    connector = _connector_with_mocks()
    connector._config.endpoint_identity = "whatsapp:pending"
    connector._bridge_manager = SimpleNamespace(
        get_status=AsyncMock(return_value={"state": "connected", "phone": "12025551234"})
    )

    await connector._maybe_resolve_pending_endpoint_identity()

    assert connector._config.endpoint_identity == "whatsapp:+12025551234"


async def test_maybe_resolve_pending_endpoint_identity_idempotent_after_resolution() -> None:
    """Once resolved, subsequent calls (e.g. every _sse_event_loop pass) are
    no-ops — the check on ``endpoint_identity`` itself is the guard."""
    connector = _connector_with_mocks()
    connector._config.endpoint_identity = "whatsapp:pending"
    status_mock = AsyncMock(return_value={"state": "connected", "phone": "12025551234"})
    connector._bridge_manager = SimpleNamespace(get_status=status_mock)

    await connector._maybe_resolve_pending_endpoint_identity()
    await connector._maybe_resolve_pending_endpoint_identity()

    assert connector._config.endpoint_identity == "whatsapp:+12025551234"
    status_mock.assert_awaited_once()


async def test_sse_event_loop_retries_pending_identity_resolution_on_each_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through _sse_event_loop: a first-time setup that boots into
    pair_required (endpoint_identity still 'whatsapp:pending') must resolve
    the real phone as soon as the bridge reports connected, without needing
    the connector to restart."""
    connector = _connector_with_mocks()
    connector._config.endpoint_identity = "whatsapp:pending"
    bridge_manager = SimpleNamespace(
        is_degraded=True,
        is_awaiting_pairing=True,
        degraded_reason="pair_required",
        get_status=AsyncMock(return_value={"state": "connected", "phone": "12025551234"}),
    )

    async def _wait_until_connected(*, timeout: float) -> None:
        assert timeout == _SSE_PAIRING_WAIT_TIMEOUT_S
        bridge_manager.is_degraded = False

    bridge_manager.wait_until_connected = AsyncMock(side_effect=_wait_until_connected)
    connector._bridge_manager = bridge_manager
    connector._running = True

    async def _empty_stream(_socket: str):
        connector._running = False
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("butlers.connectors.whatsapp_user_client._sse_event_stream", _empty_stream)

    await connector._sse_event_loop()

    assert connector._config.endpoint_identity == "whatsapp:+12025551234"


# ---------------------------------------------------------------------------
# Discretion auth health surfaced on /status (bu-ur7go)
# ---------------------------------------------------------------------------


def test_get_health_state_degrades_on_discretion_auth_failure(
    owner_connector: WhatsAppUserClientConnector,
) -> None:
    """A degraded discretion auth-health snapshot must surface as an overall
    "degraded" connector health state, not stay silent behind bridge/socket
    checks alone (bu-ofo3i: /status reported healthy while every discretion
    call 401'd)."""
    owner_connector._running = True
    owner_connector._bridge_manager = None
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
    assert "codex" in error_msg


def test_get_health_state_healthy_when_discretion_auth_ok(
    owner_connector: WhatsAppUserClientConnector,
) -> None:
    """A healthy discretion auth-health snapshot must not force a degraded
    overall state."""
    owner_connector._running = True
    owner_connector._bridge_manager = None
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
