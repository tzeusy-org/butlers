"""Condensed WhatsApp user-client connector tests — ingest.v1 contract only.

Replaces root tests/test_whatsapp_user_client.py.

Verifies:
- ingest.v1 envelope production for single events
- Batch envelope schema_version
- Idempotency key format

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
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


def test_single_event_schema_version(connector: WhatsAppUserClientConnector) -> None:
    """Single event envelope must carry schema_version='ingest.v1'."""
    event: dict[str, Any] = {
        "message_id": "msg-001",
        "chat_jid": "15551234@s.whatsapp.net",
        "sender_jid": "15559876@s.whatsapp.net",
        "timestamp": 1711447200,
        "type": "text",
        "text": "Hello there!",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "whatsapp_user_client"
    assert env["source"]["provider"] == "whatsapp"
    assert env["source"]["endpoint_identity"] == _ENDPOINT


def test_single_event_field_mapping(connector: WhatsAppUserClientConnector) -> None:
    """Event fields must map correctly from bridge event."""
    event: dict[str, Any] = {
        "message_id": "msg-abc",
        "chat_jid": "chat-123",
        "sender_jid": "sender-456",
        "timestamp": 1711447200,
        "text": "Test message",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    assert env["event"]["external_event_id"] == "msg-abc"
    assert env["event"]["external_thread_id"] == "chat-123"
    assert env["sender"]["identity"] == "sender-456"


def test_single_event_idempotency_key_format(connector: WhatsAppUserClientConnector) -> None:
    """Idempotency key must follow 'whatsapp:<endpoint>:<msg_id>' format."""
    event: dict[str, Any] = {
        "message_id": "idem-msg",
        "chat_jid": "ch1",
        "text": "test",
    }
    env = connector._normalize_single_event_to_ingest_v1(event)
    key = env["control"]["idempotency_key"]
    assert "whatsapp:" in key
    assert "idem-msg" in key


def test_batch_envelope_schema_version(connector: WhatsAppUserClientConnector) -> None:
    """Batch envelope must carry schema_version='ingest.v1'."""
    events: list[dict[str, Any]] = [
        {"message_id": f"m{i}", "chat_jid": "ch1", "text": f"msg {i}"} for i in range(3)
    ]
    env = connector._build_batch_envelope("ch1", events, "batch-001")
    assert env["schema_version"] == "ingest.v1"


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

    drain_mock.assert_awaited_once_with(
        connector._db_pool,
        "whatsapp_user_client",
        connector._config.endpoint_identity,
        submit_mock,
        pytest.importorskip("butlers.connectors.whatsapp_user_client").logger,
    )
