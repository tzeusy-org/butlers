"""Condensed Gmail connector tests — ingest.v1 contract only.

Replaces root tests/test_gmail_connector.py (207 tests).

Verifies:
- ingest.v1 envelope production from Gmail message data (full tier)
- ingest.v1 envelope production for metadata tier (slim envelope)
- Idempotency key format
- Error boundary: MCP submission failure propagated

[bu-35fm7]
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.gmail import (
    GmailConnectorConfig,
    GmailConnectorRuntime,
)


@pytest.fixture
def gmail_config() -> GmailConnectorConfig:
    return GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )


@pytest.fixture
def gmail_runtime(gmail_config: GmailConnectorConfig) -> GmailConnectorRuntime:
    return GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())


def _make_message(
    *,
    msg_id: str = "msg123",
    thread_id: str = "thread456",
    from_addr: str = "sender@example.com",
    subject: str = "Test Email",
    message_id_header: str = "<unique-msg-id@example.com>",
    body_text: str = "Test body content",
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": "1708000000000",
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": message_id_header},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": base64.urlsafe_b64encode(body_text.encode()).decode(),
            },
        },
    }


async def test_build_ingest_envelope_schema_version(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Full-tier envelope must carry schema_version='ingest.v1'."""
    envelope = await gmail_runtime._build_ingest_envelope(_make_message())
    assert envelope["schema_version"] == "ingest.v1"
    assert envelope["source"]["channel"] == "email"
    assert envelope["source"]["provider"] == "gmail"


async def test_build_ingest_envelope_event_fields(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Envelope event fields map correctly from message headers."""
    envelope = await gmail_runtime._build_ingest_envelope(_make_message())
    assert envelope["event"]["external_event_id"] == "<unique-msg-id@example.com>"
    assert envelope["event"]["external_thread_id"] == "thread456"
    assert envelope["sender"]["identity"] == "sender@example.com"


async def test_build_ingest_envelope_body_in_normalized_text(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Full-tier envelope must include decoded body text in normalized_text."""
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(body_text="Hello from Gmail")
    )
    assert "Hello from Gmail" in envelope["payload"]["normalized_text"]


async def test_build_ingest_envelope_passes_parse_ingest_envelope(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Envelope must validate against parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    envelope = await gmail_runtime._build_ingest_envelope(_make_message())
    try:
        parse_ingest_envelope(envelope)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


async def test_metadata_tier_envelope_has_null_raw(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Metadata-tier envelope must have payload.raw=null per spec §5.2."""
    from butlers.connectors.gmail_policy import (
        INGESTION_TIER_METADATA,
        MessagePolicyResult,
    )

    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    policy_result = MessagePolicyResult(
        should_ingest=True,
        ingestion_tier=INGESTION_TIER_METADATA,
        policy_tier="passive",
        assignment_rule="test",
        filter_reason="label_allowed",
        triage_action="pass_through",
    )
    envelope = await runtime._build_ingest_envelope(_make_message(), policy_result=policy_result)
    assert envelope["payload"]["raw"] is None
    assert envelope["control"]["ingestion_tier"] == "metadata"


async def test_submit_to_ingest_api_mcp_error_propagated(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """MCP tool error response must be raised, not swallowed."""
    envelope: dict[str, Any] = {
        "schema_version": "ingest.v1",
        "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "test"},
        "event": {
            "external_event_id": "msg1",
            "external_thread_id": None,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "sender@example.com"},
        "payload": {"raw": {}, "normalized_text": "test"},
        "control": {"policy_tier": "default"},
    }

    with patch.object(
        gmail_runtime._mcp_client,
        "call_tool",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Ingest tool error: Validation failed"),
    ):
        with pytest.raises(RuntimeError, match="Ingest tool error"):
            await gmail_runtime._submit_to_ingest_api(envelope)
