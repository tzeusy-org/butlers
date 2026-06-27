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


# ---------------------------------------------------------------------------
# reply_to_outbound rule: sent_message_ids population (bu-zn9zu)
# ---------------------------------------------------------------------------


def _fake_http_for_sent(
    list_pages: list[dict[str, Any]],
    message_id_headers: dict[str, str | None],
) -> MagicMock:
    """Build a fake httpx client serving SENT list pages + per-message metadata.

    list_pages: sequential messages.list responses (dicts with 'messages'/'nextPageToken').
    message_id_headers: maps Gmail message id -> Message-ID header value (or None to omit).
    """
    pages = iter(list_pages)

    def _make_resp(payload: dict[str, Any]) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=payload)
        return resp

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/messages"):
            return _make_resp(next(pages))
        mid = url.rsplit("/", 1)[-1]
        header_val = message_id_headers.get(mid)
        headers = [{"name": "Message-ID", "value": header_val}] if header_val is not None else []
        return _make_resp({"payload": {"headers": headers}})

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    return client


async def test_fetch_sent_message_ids_parses_headers(
    gmail_config: GmailConnectorConfig,
) -> None:
    """_fetch_sent_message_ids returns angle-bracketed Message-IDs from SENT mail."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._get_access_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
    runtime._http_client = _fake_http_for_sent(  # type: ignore[assignment]
        list_pages=[{"messages": [{"id": "s1"}, {"id": "s2"}], "nextPageToken": None}],
        message_id_headers={
            # one already bracketed, one bare — both normalize to bracketed form
            "s1": "<sent-1@example.com>",
            "s2": "sent-2@example.com",
        },
    )

    sent = await runtime._fetch_sent_message_ids()

    assert sent == frozenset({"<sent-1@example.com>", "<sent-2@example.com>"})


async def test_reply_to_outbound_fires_via_real_policy_path(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Inbound reply to an owner-sent Message-ID is classified high_priority.

    Drives the real policy path: populate sent_message_ids via the connector's
    refresh, then evaluate inbound mail through evaluate_message_policy.
    """
    from butlers.connectors.gmail_policy import (
        POLICY_TIER_DEFAULT,
        POLICY_TIER_HIGH_PRIORITY,
        RULE_REPLY_TO_OUTBOUND,
        evaluate_message_policy,
    )

    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    # Owner sent exactly one message.
    runtime._fetch_sent_message_ids = AsyncMock(  # type: ignore[method-assign]
        return_value=frozenset({"<sent-1@example.com>"})
    )
    runtime._gmail_policy_evaluator.get_known_contacts = AsyncMock(  # type: ignore[method-assign]
        return_value=frozenset()
    )

    await runtime._refresh_policy_tier_assigner()
    assert runtime._policy_tier_assigner.sent_message_ids == frozenset({"<sent-1@example.com>"})

    # Inbound email replying to the owner's sent message -> high_priority.
    reply_msg: dict[str, Any] = {
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "stranger@external.com"},
                {"name": "In-Reply-To", "value": "<sent-1@example.com>"},
            ]
        },
    }
    reply_result = evaluate_message_policy(
        reply_msg,
        label_filter=runtime._label_filter,
        tier_assigner=runtime._policy_tier_assigner,
        endpoint_identity="test",
    )
    assert reply_result.policy_tier == POLICY_TIER_HIGH_PRIORITY
    assert reply_result.assignment_rule == RULE_REPLY_TO_OUTBOUND

    # Unrelated inbound email (replies to an unknown id, owner not a recipient)
    # must NOT be falsely elevated.
    unrelated_msg: dict[str, Any] = {
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "stranger@external.com"},
                {"name": "In-Reply-To", "value": "<not-ours@external.com>"},
            ]
        },
    }
    unrelated_result = evaluate_message_policy(
        unrelated_msg,
        label_filter=runtime._label_filter,
        tier_assigner=runtime._policy_tier_assigner,
        endpoint_identity="test",
    )
    assert unrelated_result.policy_tier == POLICY_TIER_DEFAULT


async def test_refresh_sent_message_ids_fail_open_retains_previous(
    gmail_config: GmailConnectorConfig,
) -> None:
    """A failed SENT refresh retains the previous cache instead of clearing it."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._sent_ids_cache = frozenset({"<prev@example.com>"})
    # Force a refresh attempt (expire the TTL window) that then raises.
    runtime._sent_ids_loaded_at = float("-inf")
    runtime._fetch_sent_message_ids = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("Gmail API down")
    )

    await runtime._refresh_sent_message_ids()

    assert runtime._policy_tier_assigner.sent_message_ids == frozenset({"<prev@example.com>"})
