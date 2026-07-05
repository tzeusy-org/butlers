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

import httpx
import pytest

from butlers.connectors.gmail import (
    GmailAccountLoop,
    GmailConnectorConfig,
    GmailConnectorRuntime,
    _classify_source_api_error,
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


# ---------------------------------------------------------------------------
# Global triage action -> ingestion tier wiring (bu-59ock)
# ---------------------------------------------------------------------------


def _tier1_result() -> Any:
    from butlers.connectors.gmail_policy import INGESTION_TIER_FULL, MessagePolicyResult

    return MessagePolicyResult(
        should_ingest=True,
        ingestion_tier=INGESTION_TIER_FULL,
        policy_tier="default",
        assignment_rule="fallback_default",
        filter_reason="label_allowed",
        triage_action="pass_through",
    )


def test_apply_global_action_tier_metadata_only_downgrades_to_tier2() -> None:
    """A global `metadata_only` decision downgrades a Tier 1 result to Tier 2."""
    from butlers.connectors.gmail_policy import INGESTION_TIER_METADATA
    from butlers.ingestion_policy import PolicyDecision

    result = GmailConnectorRuntime._apply_global_action_tier(
        _tier1_result(), PolicyDecision(action="metadata_only")
    )
    assert result.ingestion_tier == INGESTION_TIER_METADATA
    assert result.triage_action == "metadata_only"


def test_apply_global_action_tier_non_metadata_stays_tier1() -> None:
    """pass_through/route_to/low_priority_queue keep the message at Tier 1."""
    from butlers.connectors.gmail_policy import INGESTION_TIER_FULL
    from butlers.ingestion_policy import PolicyDecision

    for action in ("pass_through", "route_to", "low_priority_queue"):
        result = GmailConnectorRuntime._apply_global_action_tier(
            _tier1_result(), PolicyDecision(action=action)
        )
        assert result.ingestion_tier == INGESTION_TIER_FULL, action


async def test_ingest_single_message_metadata_only_global_rule_builds_tier2(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """End-to-end: a global metadata_only rule yields a slim Tier 2 envelope.

    Regression for the previously hardcoded pass_through: non-skip global
    actions silently degraded to full Tier 1 ingestion.
    """
    from butlers.ingestion_policy import PolicyDecision

    captured: dict[str, Any] = {}

    async def fake_submit(env: dict[str, Any]) -> None:
        captured["env"] = env

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(),
        ),
        patch.object(
            gmail_runtime,
            "_submit_to_ingest_api",
            new=AsyncMock(side_effect=fake_submit),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
        patch.object(
            gmail_runtime._global_ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="metadata_only"),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    assert "env" in captured, "expected a Tier 2 envelope to be submitted"
    assert captured["env"]["payload"]["raw"] is None
    assert captured["env"]["control"]["ingestion_tier"] == "metadata"


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


# ---------------------------------------------------------------------------
# Health-state honesty (bu-dej20)
#
# Diagnosis: connector_heartbeat_log on the live dev DB shows exactly one
# ``error`` heartbeat in 1734 over 10 days for gmail:user:uniquosity@gmail.com
# (2026-07-05 01:12:51), immediately followed by ``healthy`` two minutes
# later while public.ingestion_events kept landing rows through the same
# window. The clearing behavior already worked (state is never sticky past
# the next successful call) — the actual defect was that _get_health_state
# collapsed every failure into one hardcoded "Gmail API unreachable or
# authentication failed" / state="error" pair, discarding whether the
# failure was a genuine OAuth revocation (needs owner re-consent) or a
# transient network/rate-limit hiccup (self-heals, no action needed). These
# tests pin the fix: distinct state, distinct captured error text, and a
# clean return to healthy on the next success.
# ---------------------------------------------------------------------------


def _http_error_with_response(payload: dict[str, Any], status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
    response = httpx.Response(status_code, json=payload, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_classify_source_api_error_detects_invalid_grant() -> None:
    """A genuine invalid_grant from Google's OAuth endpoint is an auth revocation."""
    exc = _http_error_with_response(
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
        400,
    )
    is_auth_revocation, description = _classify_source_api_error(exc)
    assert is_auth_revocation is True
    assert "invalid_grant" in description


def test_classify_source_api_error_transient_5xx_is_not_auth_revocation() -> None:
    """A transient 5xx from the Gmail data API is not an auth revocation."""
    exc = _http_error_with_response(
        {"error": {"code": 503, "status": "UNAVAILABLE", "message": "Backend Error"}},
        503,
    )
    is_auth_revocation, description = _classify_source_api_error(exc)
    assert is_auth_revocation is False
    assert "503" in description or "UNAVAILABLE" in description


def test_classify_source_api_error_falls_back_for_bare_exception() -> None:
    """A network-level exception with no HTTP response still yields a real description."""
    exc = httpx.ConnectError("Connection refused")
    is_auth_revocation, description = _classify_source_api_error(exc)
    assert is_auth_revocation is False
    assert "Connection refused" in description


def test_classify_source_api_error_does_not_false_positive_on_prose_mention() -> None:
    """A Gmail data-API error whose *message* text happens to mention
    "invalid_grant" in prose (not as the actual OAuth error code) must not be
    misclassified as an auth revocation — only the top-level ``error`` field
    of the OAuth token endpoint's exact shape counts.
    """
    exc = _http_error_with_response(
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "Malformed request: field 'invalid_grant' is not recognized",
            }
        },
        400,
    )
    is_auth_revocation, description = _classify_source_api_error(exc)
    assert is_auth_revocation is False
    assert "invalid_grant" in description  # text is preserved, just not misclassified


def test_get_health_state_healthy_by_default(
    gmail_config: GmailConnectorConfig,
) -> None:
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    state, err = runtime._get_health_state()
    assert state == "healthy"
    assert err is None


def test_get_health_state_reports_error_on_auth_revocation(
    gmail_config: GmailConnectorConfig,
) -> None:
    """A genuine OAuth revocation is reported as error with the real error text."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._source_api_ok = False
    runtime._auth_error = True
    runtime._source_api_error_message = "error=invalid_grant, description=Token revoked"

    state, err = runtime._get_health_state()

    assert state == "error"
    assert err == "error=invalid_grant, description=Token revoked"


def test_get_health_state_reports_degraded_for_transient_failure(
    gmail_config: GmailConnectorConfig,
) -> None:
    """A transient (non-auth) source-API failure is degraded, not error."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._source_api_ok = False
    runtime._auth_error = False
    runtime._source_api_error_message = "connect_error: Connection refused"

    state, err = runtime._get_health_state()

    assert state == "degraded"
    assert err == "connect_error: Connection refused"


async def test_health_state_clears_on_next_successful_call(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Regression for bu-dej20: error -> success -> healthy, with no stickiness.

    Mirrors what the live heartbeat log actually showed: one failed
    ``messages.get`` call (simulating the mid-flap snapshot the connector
    summary caught), followed immediately by a successful one. The health
    state must clear back to healthy the moment a call succeeds — it must
    never remain "error"/"degraded" once ingestion is flowing again.
    """
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._get_access_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]

    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            httpx.ConnectError("Connection refused"),
            MagicMock(raise_for_status=MagicMock(), json=MagicMock(return_value={"id": "m1"})),
        ]
    )
    runtime._http_client = client  # type: ignore[assignment]

    with pytest.raises(httpx.ConnectError):
        await runtime._fetch_message("m1")
    state, err = runtime._get_health_state()
    assert state == "degraded"
    assert err is not None

    result = await runtime._fetch_message("m1")
    assert result == {"id": "m1"}
    state, err = runtime._get_health_state()
    assert state == "healthy"
    assert err is None


def test_account_loop_get_health_maps_auth_revocation_to_error(
    gmail_config: GmailConnectorConfig,
) -> None:
    loop = GmailAccountLoop("owner@example.com", gmail_config, cursor_pool=MagicMock())
    loop._runtime._source_api_ok = False
    loop._runtime._auth_error = True
    loop._runtime._source_api_error_message = "error=invalid_grant"

    health = loop.get_health()

    assert health.status == "error"
    assert health.error == "error=invalid_grant"


def test_account_loop_get_health_maps_transient_failure_to_degraded(
    gmail_config: GmailConnectorConfig,
) -> None:
    """A one-off transient failure must not read as "error" on the per-account status.

    Before this fix, any source-API failure (transient or not) mapped to
    "error" here, identical to a genuine revoked token — exactly the
    contradictory/overloaded signal bu-dej20 flagged on the dashboard.
    """
    loop = GmailAccountLoop("owner@example.com", gmail_config, cursor_pool=MagicMock())
    loop._runtime._source_api_ok = False
    loop._runtime._auth_error = False
    loop._runtime._source_api_error_message = "connect_error: timed out"

    health = loop.get_health()

    assert health.status == "degraded"
    assert health.error == "connect_error: timed out"
