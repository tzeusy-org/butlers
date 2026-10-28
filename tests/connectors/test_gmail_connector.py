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


async def test_build_ingest_envelope_withholds_bearer_material(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Codes never leave the connector: text, raw base64 body and flag are all scrubbed."""
    message = _make_message(body_text="Your code is 482913")
    envelope = await gmail_runtime._build_ingest_envelope(message)
    assert "482913" not in envelope["payload"]["normalized_text"]
    assert "482913" not in str(envelope["payload"]["raw"])
    assert base64.urlsafe_b64encode(b"482913").decode() not in str(envelope["payload"]["raw"])
    assert envelope["control"]["bearer_scrubbed"] is True


async def test_build_ingest_envelope_event_fields(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Envelope event fields map correctly from message headers."""
    envelope = await gmail_runtime._build_ingest_envelope(_make_message())
    assert envelope["event"]["external_event_id"] == "<unique-msg-id@example.com>"
    assert envelope["event"]["external_conversation_id"] == "thread456"
    assert envelope["event"]["reply_target_ref"] == "thread456"
    assert envelope["sender"]["identity"] == "sender@example.com"


async def test_build_ingest_envelope_captures_from_display_name(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """The raw From: display name is captured into sender.display_name (bu-vs9cr)."""
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(from_addr="John Doe <John.Doe@Example.com>")
    )
    # identity stays normalized (bare, lowercased); display_name is verbatim.
    assert envelope["sender"]["identity"] == "john.doe@example.com"
    assert envelope["sender"]["display_name"] == "John Doe"


async def test_build_ingest_envelope_display_name_none_for_bare_address(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A bare From: address (no display part) yields sender.display_name=None."""
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(from_addr="sender@example.com")
    )
    assert envelope["sender"]["display_name"] is None


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

    from butlers.tools.switchboard.contracts import parse_ingest_envelope

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
# Sender identity normalization at ingest (bu-qeaou)
# ---------------------------------------------------------------------------


async def test_full_tier_sender_identity_is_normalized_to_bare_address(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A raw 'Name <addr>' From header must be reduced to a bare lowercased
    address in envelope.sender.identity — the display name remains recoverable
    from payload.raw.payload.headers for Tier 1 (full) envelopes only."""
    message = _make_message(from_addr="John Doe <John.Doe@Example.com>")

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert envelope["sender"]["identity"] == "john.doe@example.com"
    # Display name still recoverable from the untouched raw payload.
    headers = envelope["payload"]["raw"]["payload"]["headers"]
    from_header = next(h["value"] for h in headers if h["name"] == "From")
    assert from_header == "John Doe <John.Doe@Example.com>"


async def test_metadata_tier_sender_identity_is_normalized_to_bare_address(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Same normalization applies to the Tier 2 (metadata-only) envelope."""
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
    message = _make_message(from_addr="Jane Roe <Jane.Roe@Example.com>")

    envelope = await runtime._build_ingest_envelope(message, policy_result=policy_result)

    assert envelope["sender"]["identity"] == "jane.roe@example.com"


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
    """A transient SENT refresh failure retains the cache and degrades health."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._sent_ids_cache = frozenset({"<prev@example.com>"})
    # Force a refresh attempt (expire the TTL window) that then raises.
    runtime._sent_ids_loaded_at = float("-inf")
    runtime._fetch_sent_message_ids = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("Gmail API down")
    )

    await runtime._refresh_sent_message_ids()

    assert runtime._policy_tier_assigner.sent_message_ids == frozenset({"<prev@example.com>"})
    assert runtime._source_api_ok is False
    assert runtime._auth_error is False
    assert runtime._source_api_error_message is not None
    assert "Gmail API down" in runtime._source_api_error_message
    assert runtime._get_health_state() == ("degraded", runtime._source_api_error_message)


async def test_refresh_sent_message_ids_auth_failure_retains_cache_and_marks_error(
    gmail_config: GmailConnectorConfig,
) -> None:
    """An OAuth revocation preserves the cache but records an actionable error state."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._sent_ids_cache = frozenset({"<prev@example.com>"})
    runtime._sent_ids_loaded_at = float("-inf")
    runtime._fetch_sent_message_ids = AsyncMock(  # type: ignore[method-assign]
        side_effect=_http_error_with_response(
            {"error": "invalid_grant", "error_description": "Token revoked"}, 400
        )
    )

    await runtime._refresh_sent_message_ids()

    assert runtime._policy_tier_assigner.sent_message_ids == frozenset({"<prev@example.com>"})
    assert runtime._source_api_ok is False
    assert runtime._auth_error is True
    assert runtime._source_api_error_message is not None
    assert "invalid_grant" in runtime._source_api_error_message
    assert runtime._get_health_state() == ("error", runtime._source_api_error_message)


# ---------------------------------------------------------------------------
# Health-state honesty (bu-dej20)
#
# Diagnosis: connector_heartbeat_log on the live dev DB shows exactly one
# ``error`` heartbeat in 1734 over 10 days for gmail:user:owner@example.com
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


def test_classify_source_api_error_detects_unauthorized_client() -> None:
    """An exact unauthorized_client token-endpoint response also needs reauthorization."""
    exc = _http_error_with_response(
        {"error": "unauthorized_client", "error_description": "client not allowed"},
        401,
    )

    is_auth_revocation, description = _classify_source_api_error(exc)

    assert is_auth_revocation is True
    assert "unauthorized_client" in description


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

    # Actual startup composition publishes bounded unloaded evidence before
    # provider work. Provider/network boundaries alone are synthetic here.
    from butlers.core.known_contact_state import CLASSIFICATION_KEY

    runtime._config = gmail_config.model_copy(
        update={
            "gmail_pubsub_enabled": True,
            "connector_backfill_enabled": False,
        }
    )
    observed = []

    async def accepted(_name, envelope):
        check = envelope["capabilities"][CLASSIFICATION_KEY]
        observed.append(check)
        assert check["state"] == "unloaded" and check["generation"] == 0
        return {
            "status": "accepted",
            "classification_ack": {
                "admitted": True,
                "instance_id": check["instance_id"],
                "generation": 0,
                "request_admission_epoch": None,
                "admission_epoch": "22222222-2222-4222-8222-222222222222",
                "reason": "none",
            },
        }

    async def provider_work():
        assert len(observed) == 1
        assert runtime._heartbeat._admission_epoch is not None

    runtime._mcp_client.call_tool = AsyncMock(side_effect=accepted)
    with (
        patch.dict("os.environ", {"CONNECTOR_HEARTBEAT_ENABLED": "true"}),
        patch.object(runtime, "_start_health_server"),
        patch.object(runtime, "_start_webhook_server"),
        patch.object(runtime, "_gmail_watch_start", side_effect=provider_work),
        patch("butlers.connectors.gmail.wait_for_switchboard_ready", new_callable=AsyncMock),
        patch.object(runtime, "_ensure_cursor", new_callable=AsyncMock),
        patch.object(runtime._ingestion_policy, "ensure_loaded", new_callable=AsyncMock),
        patch.object(runtime._global_ingestion_policy, "ensure_loaded", new_callable=AsyncMock),
        patch.object(runtime, "_run_ingestion_loop", new_callable=AsyncMock),
    ):
        await runtime.start()
    assert runtime._get_health_state() == ("healthy", None)
    assert runtime._gmail_policy_evaluator.peek_snapshot().state == "unloaded"
    assert runtime._heartbeat._task is None


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


# ---------------------------------------------------------------------------
# Filtered-content privacy tier (bu-glbjx)
#
# Content the connector deliberately does NOT submit (status='filtered':
# connector-rule block, global skip) persists a bounded preview only — the
# full raw message payload MUST NOT be retained (full_payload.payload.raw == {}).
# Errored content (status='error') is exempt and keeps its payload for
# diagnosis and replay.
# ---------------------------------------------------------------------------


async def test_connector_rule_block_persists_no_raw_payload(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A connector-scope policy block records a filtered event whose
    full_payload.payload.raw is empty; the subject preview is retained."""
    from butlers.ingestion_policy import PolicyDecision

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(subject="Blocked Subject"),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="block", matched_rule_type="sender_domain"),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    rows = gmail_runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "filtered"
    assert row[9]["payload"]["raw"] == {}
    assert row[6] == "Blocked Subject"


async def test_connector_rule_block_preview_bounded_to_200_chars(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A subject longer than 200 chars is truncated before persistence."""
    from butlers.ingestion_policy import PolicyDecision

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(subject="z" * 500),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="block", matched_rule_type="sender_domain"),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    rows = gmail_runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    assert rows[0][6] == "z" * 200


async def test_global_skip_persists_no_raw_payload(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A global-scope skip records a filtered event with an empty raw payload."""
    from butlers.ingestion_policy import PolicyDecision

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
        patch.object(
            gmail_runtime._global_ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="skip", matched_rule_type="keyword"),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    rows = gmail_runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "filtered"
    assert row[9]["payload"]["raw"] == {}


# ---------------------------------------------------------------------------
# Dropped-from-someone-you-know marker (bu-q7vx1q.4)
#
# A drop of mail from a known contact is stamped drop_context.important_dropped
# so the dashboard cannot read "all gates clear" over it. Every drop site is
# covered; a stranger's drop and the replayed envelope stay unmarked/clean.
# ---------------------------------------------------------------------------


def _drop_known_message(site: str, from_addr: str) -> tuple[dict[str, Any], list[Any]]:
    """Return (message, policy patches) that force the named gmail drop site."""
    from butlers.ingestion_policy import PolicyDecision

    message = _make_message(from_addr=from_addr)
    block = PolicyDecision(action="block", matched_rule_type="sender_domain")
    skip = PolicyDecision(action="skip", matched_rule_type="sender_domain")
    passthrough = PolicyDecision(action="pass_through")
    if site == "label_exclude":
        message["labelIds"] = ["SPAM"]
    return message, [
        block if site == "connector_rule" else passthrough,
        skip if site == "global_rule" else passthrough,
    ]


# REQ-ingestion-policy-001: synthetic provider, actual three drop paths and frozen metadata.
@pytest.mark.parametrize("site", ["label_exclude", "connector_rule", "global_rule"])
@pytest.mark.parametrize(
    ("from_addr", "marked"),
    [
        ("Alice <alice@known.example>", True),
        ("stranger@other.example", False),
    ],
)
async def test_known_contact_drop_is_marked_important(
    gmail_runtime: GmailConnectorRuntime, site: str, from_addr: str, marked: bool
) -> None:
    evaluator = gmail_runtime._gmail_policy_evaluator
    evaluator._db_pool = AsyncMock()
    evaluator._db_pool.fetch.return_value = [{"value": "alice@known.example"}]
    loaded = await evaluator.get_snapshot()
    assert loaded.state == "loaded"
    gmail_runtime._policy_tier_assigner.known_contacts = loaded.contacts
    message, (connector_decision, global_decision) = _drop_known_message(site, from_addr)

    with (
        patch.object(gmail_runtime, "_fetch_message", new_callable=AsyncMock, return_value=message),
        patch.object(gmail_runtime._ingestion_policy, "evaluate", return_value=connector_decision),
        patch.object(
            gmail_runtime._global_ingestion_policy, "evaluate", return_value=global_decision
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    rows = gmail_runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    stored = rows[0][9]
    assert rows[0][8] == "filtered"
    if marked:
        assert stored["drop_context"]["important_dropped"] is True
        assert stored["drop_context"]["basis"] == "known_contact"
    else:
        assert "important_dropped" not in stored["drop_context"]
    # The marker carries no message content: the privacy-tier raw={} still holds.
    assert stored["payload"]["raw"] == {}

    classification = stored["drop_context"]["classification"]
    assert classification["state"] == "loaded"
    observed = datetime.fromisoformat(classification["observed_at"])
    assert loaded.last_success_at <= observed
    assert (observed - loaded.last_success_at).total_seconds() <= 900
    frozen = dict(classification)
    # Provider event time is independent of the classification decision; no
    # classification refresh is performed merely to discard another message.
    for provider_date in ["1", "4102444800000", "not-a-date"]:
        message["internalDate"] = provider_date
        with (
            patch.object(
                gmail_runtime, "_fetch_message", new_callable=AsyncMock, return_value=message
            ),
            patch.object(
                gmail_runtime._ingestion_policy, "evaluate", return_value=connector_decision
            ),
            patch.object(
                gmail_runtime._global_ingestion_policy, "evaluate", return_value=global_decision
            ),
        ):
            await gmail_runtime._ingest_single_message("msg123")
        next_stored = gmail_runtime._filtered_event_buffer._rows[-1][9]
        next_class = next_stored["drop_context"]["classification"]
        assert next_class["state"] == "loaded" and next_class["generation"] == loaded.generation
        assert next_stored["payload"]["raw"] == {}
        assert next_class["observed_at"] != next_stored["event"]["observed_at"]
    evaluator._cache_loaded_at -= 901
    evaluator._db_pool.fetch.side_effect = RuntimeError("private error-tail")
    await evaluator.get_snapshot()
    with (
        patch.object(gmail_runtime, "_fetch_message", new_callable=AsyncMock, return_value=message),
        patch.object(gmail_runtime._ingestion_policy, "evaluate", return_value=connector_decision),
        patch.object(
            gmail_runtime._global_ingestion_policy, "evaluate", return_value=global_decision
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")
    retained = gmail_runtime._filtered_event_buffer._rows[-1][9]
    assert retained["drop_context"]["classification"]["state"] == "failed"
    assert bool(retained["drop_context"].get("important_dropped")) is marked
    assert stored["drop_context"]["classification"] == frozen
    assert gmail_runtime._get_health_state() == ("healthy", None)


async def test_submission_error_retains_raw_payload(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Errored content (status='error') is exempt: the raw payload is retained."""
    from butlers.ingestion_policy import PolicyDecision

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
        patch.object(
            gmail_runtime._global_ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
        patch.object(
            gmail_runtime,
            "_submit_to_ingest_api",
            new=AsyncMock(side_effect=RuntimeError("switchboard down")),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    rows = gmail_runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "error"
    assert row[9]["payload"]["raw"] == {"message_id": "msg123"}


# ---------------------------------------------------------------------------
# Provider-supplied header entries with non-string fields (bu-qdi56)
#
# Gmail header entries arrive as untrusted provider JSON. ``.get("name", "")``
# only returns the default when the key is ABSENT: a present ``null``/list/int
# is returned as-is and the following string method raises AttributeError.
# ---------------------------------------------------------------------------

_MALFORMED_HEADER_ENTRIES: list[Any] = [
    "Content-Type: text/plain",  # entry is not a dict at all
    ["Content-Type", "text/plain"],
    {"name": None, "value": "text/plain; charset=iso-8859-1"},
    {"name": ["Content-Type"], "value": "text/plain; charset=iso-8859-1"},
    {"name": 42, "value": "text/plain; charset=iso-8859-1"},
]


@pytest.mark.parametrize("header", _MALFORMED_HEADER_ENTRIES)
def test_charset_from_headers_skips_malformed_entries(header: Any) -> None:
    """A malformed header entry is skipped, not crashed on."""
    assert GmailConnectorRuntime._charset_from_headers([header]) == "utf-8"


@pytest.mark.parametrize("bad_value", [None, 42, ["utf-8"], {"charset": "utf-8"}])
def test_charset_from_headers_skips_non_string_value(bad_value: Any) -> None:
    """A Content-Type header whose value is not a string falls back to utf-8."""
    headers = [{"name": "Content-Type", "value": bad_value}]
    assert GmailConnectorRuntime._charset_from_headers(headers) == "utf-8"


def test_charset_from_headers_still_reads_declared_charset() -> None:
    """The happy path is unchanged: a declared, known charset is returned."""
    headers = [
        {"name": None, "value": "junk"},
        {"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"},
    ]
    assert GmailConnectorRuntime._charset_from_headers(headers) == "iso-8859-1"


@pytest.mark.parametrize("bad_name", [None, 42, ["Subject"]])
def test_get_subject_skips_header_with_non_string_name(bad_name: Any) -> None:
    """A non-string header ``name`` is skipped instead of raising."""
    message_data = {"payload": {"headers": [{"name": bad_name, "value": "Hello"}]}}
    assert GmailConnectorRuntime._get_subject(message_data) == "(no subject)"


@pytest.mark.parametrize("bad_value", [None, 42, ["Hello"]])
def test_get_subject_rejects_non_string_value(bad_value: Any) -> None:
    """A non-string Subject value degrades to the documented default."""
    message_data = {"payload": {"headers": [{"name": "Subject", "value": bad_value}]}}
    assert GmailConnectorRuntime._get_subject(message_data) == "(no subject)"


@pytest.mark.parametrize("bad_name", [None, 42, ["From"]])
def test_get_from_header_skips_header_with_non_string_name(bad_name: Any) -> None:
    """A non-string header ``name`` is skipped instead of raising."""
    message_data = {"payload": {"headers": [{"name": bad_name, "value": "a@example.com"}]}}
    assert GmailConnectorRuntime._get_from_header(message_data) == ""


@pytest.mark.parametrize("bad_value", [None, 42, ["a@example.com"]])
def test_get_from_header_rejects_non_string_value(bad_value: Any) -> None:
    """A non-string From value degrades to the empty string."""
    message_data = {"payload": {"headers": [{"name": "From", "value": bad_value}]}}
    assert GmailConnectorRuntime._get_from_header(message_data) == ""


def test_get_subject_and_from_still_read_valid_headers() -> None:
    """The happy path is unchanged when a malformed entry precedes a valid one."""
    message_data = {
        "payload": {
            "headers": [
                {"name": None, "value": "junk"},
                {"name": "Subject", "value": "Quarterly report"},
                {"name": "From", "value": "sender@example.com"},
            ]
        }
    }
    assert GmailConnectorRuntime._get_subject(message_data) == "Quarterly report"
    assert GmailConnectorRuntime._get_from_header(message_data) == "sender@example.com"


def _fake_http_for_sent_raw(headers_by_id: dict[str, list[Any]]) -> MagicMock:
    """Fake httpx client serving one SENT page plus verbatim header lists."""

    def _make_resp(payload: dict[str, Any]) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=payload)
        return resp

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/messages"):
            return _make_resp(
                {"messages": [{"id": mid} for mid in headers_by_id], "nextPageToken": None}
            )
        mid = url.rsplit("/", 1)[-1]
        return _make_resp({"payload": {"headers": headers_by_id[mid]}})

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    return client


async def test_fetch_sent_message_ids_skips_malformed_headers(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Malformed SENT headers are skipped; valid Message-IDs still collected.

    An AttributeError here escapes into ``_refresh_sent_message_ids``'s broad
    handler, which misreports it as a source-API/auth failure and flips the
    connector's health to degraded.
    """
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._get_access_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
    runtime._http_client = _fake_http_for_sent_raw(  # type: ignore[assignment]
        {
            "s1": [{"name": None, "value": "<sent-1@example.com>"}],
            "s2": [{"name": "Message-ID", "value": ["<sent-2@example.com>"]}],
            "s3": ["Message-ID: <sent-3@example.com>"],
            "s4": [{"name": "Message-ID", "value": "<sent-4@example.com>"}],
        }
    )

    sent = await runtime._fetch_sent_message_ids()

    assert sent == frozenset({"<sent-4@example.com>"})


# ---------------------------------------------------------------------------
# Provider-supplied payload/header containers (bu-49dm3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [None, [], {"headers": None}, {"headers": {}}],
)
def test_header_extractors_treat_malformed_payload_containers_as_absent(payload: Any) -> None:
    """A present non-mapping payload or non-list headers must not crash lookup."""
    message_data = {"payload": payload}

    assert GmailConnectorRuntime._get_from_header(message_data) == ""
    assert GmailConnectorRuntime._get_subject(message_data) == "(no subject)"


def test_build_ingestion_envelope_skips_malformed_header_entries() -> None:
    """Policy input only retains headers whose provider name and value are strings."""
    envelope = GmailConnectorRuntime._build_ingestion_envelope(
        {
            "payload": {
                "headers": [
                    {"name": None, "value": "ignored"},
                    {"name": "From", "value": None},
                    {"name": "Subject", "value": "Valid subject"},
                ]
            }
        }
    )

    assert envelope.sender_address == ""
    assert envelope.headers == {"Subject": "Valid subject"}


async def test_build_ingest_envelope_treats_bad_header_container_as_empty(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Malformed headers retain the email with documented subject/sender fallbacks."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    message = _make_message()
    message["payload"] = None

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert envelope["sender"]["identity"] == "unknown"
    assert "(no subject)" in envelope["payload"]["normalized_text"]
    parse_ingest_envelope(envelope)


async def test_build_ingest_envelope_treats_non_mapping_body_as_empty(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A present null body does not prevent the message from reaching ingest."""
    message = _make_message()
    message["payload"] = {"mimeType": "text/plain", "headers": [], "body": None}

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert "(no body)" in envelope["payload"]["normalized_text"]


async def test_build_ingest_envelope_skips_nested_multipart_parts_none(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A null nested MIME parts collection does not drop the email."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    message = _make_message()
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "body": {},
                "parts": None,
            }
        ],
    }

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert "(no body)" in envelope["payload"]["normalized_text"]
    assert envelope["payload"]["attachments"] is None
    parse_ingest_envelope(envelope)


@pytest.mark.parametrize(
    "malformed_parts",
    ["not-a-parts-list", {"not": "a-parts-list"}, [None]],
)
async def test_build_ingest_envelope_skips_nested_multipart_malformed_parts(
    gmail_runtime: GmailConnectorRuntime,
    malformed_parts: Any,
) -> None:
    """Malformed nested MIME parts do not prevent the email from reaching ingest."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    message = _make_message()
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "body": {},
                "parts": malformed_parts,
            }
        ],
    }

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert "(no body)" in envelope["payload"]["normalized_text"]
    assert envelope["payload"]["attachments"] is None
    parse_ingest_envelope(envelope)


async def test_build_ingest_envelope_skips_nested_attachment_body_none(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A null nested attachment body does not drop the email."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    message = _make_message()
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "application/pdf",
                "filename": "receipt.pdf",
                "body": None,
            }
        ],
    }

    envelope = await gmail_runtime._build_ingest_envelope(message)

    assert "(no body)" in envelope["payload"]["normalized_text"]
    assert envelope["payload"]["attachments"] is None
    parse_ingest_envelope(envelope)


def test_extract_body_treats_non_mapping_body_as_no_body(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """A present null MIME body falls back instead of failing the entire email."""
    assert (
        gmail_runtime._extract_body_from_payload({"mimeType": "text/plain", "body": None})
        == "(no body)"
    )


def test_decode_part_bytes_treats_non_list_headers_as_absent(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """Malformed MIME headers use the established UTF-8 fallback."""
    assert gmail_runtime._decode_part_bytes(b"plain text", {"headers": None}) == "plain text"


def _fake_http_for_sent_message_body(message_body: Any) -> MagicMock:
    """Fake one SENT listing plus a deliberately arbitrary message response."""

    def _make_resp(payload: Any) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=payload)
        return resp

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/messages"):
            return _make_resp({"messages": [{"id": "s1"}], "nextPageToken": None})
        return _make_resp(message_body)

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    return client


@pytest.mark.parametrize("message_body", [{"payload": None}, {"payload": {"headers": None}}, []])
async def test_fetch_sent_message_ids_treats_bad_header_container_as_empty(
    gmail_config: GmailConnectorConfig,
    message_body: Any,
) -> None:
    """Malformed SENT metadata does not falsely mark source API health degraded."""
    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
    runtime._get_access_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
    runtime._http_client = _fake_http_for_sent_message_body(message_body)  # type: ignore[assignment]

    assert await runtime._fetch_sent_message_ids() == frozenset()


def test_policy_evaluation_treats_bad_header_container_as_empty(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Malformed provider headers keep the default pass-through policy rather than erroring."""
    from butlers.connectors.gmail_policy import POLICY_TIER_DEFAULT, evaluate_message_policy

    runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())

    result = evaluate_message_policy(
        {"labelIds": ["INBOX"], "payload": None},
        label_filter=runtime._label_filter,
        tier_assigner=runtime._policy_tier_assigner,
    )

    assert result.should_ingest is True
    assert result.policy_tier == POLICY_TIER_DEFAULT


# Spec: connector-gmail, Aggregated Health Status (gmail-token-origin-health).
# Spec: REQ-connector-gmail-001
def test_source_auth_classification_requires_response_bound_token_post() -> None:
    """Only the actual token POST supplies authority; diagnostics still survive."""
    token_url = "https://oauth2.googleapis.com/token"
    cases = [
        ("POST", token_url, {"error": "invalid_grant"}, True),
        ("POST", token_url, {"error": "unauthorized_client"}, True),
        ("GET", token_url, {"error": "invalid_grant"}, False),
        ("PUT", token_url, {"error": "unauthorized_client"}, False),
        ("POST", "http://oauth2.googleapis.com/token", {"error": "invalid_grant"}, False),
        (
            "POST",
            "https://oauth2.googleapis.com.attacker.test/token",
            {"error": "invalid_grant"},
            False,
        ),
        ("POST", "https://attacker.test/token", {"error": "unauthorized_client"}, False),
        ("POST", "https://oauth2.googleapis.com:444/token", {"error": "invalid_grant"}, False),
        ("POST", "https://oauth2.googleapis.com/token/", {"error": "invalid_grant"}, False),
        ("POST", "https://oauth2.googleapis.com/token?scope=x", {"error": "invalid_grant"}, False),
        ("POST", "https://oauth2.googleapis.com/token#x", {"error": "invalid_grant"}, False),
        ("POST", "https://oauth2.googleapis.com/t%6fken", {"error": "invalid_grant"}, False),
        ("POST", "https://userinfo@oauth2.googleapis.com/token", {"error": "invalid_grant"}, False),
        (
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            {"error": "invalid_grant"},
            False,
        ),
        (
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            {"error": "unauthorized_client"},
            False,
        ),
        ("POST", token_url, {"error": {"code": "invalid_grant", "message": "nested"}}, False),
        ("POST", token_url, {"error": "temporarily_unavailable"}, False),
        ("POST", token_url, {"error": "INVALID_GRANT"}, False),
        ("POST", token_url, {"error": "invalid_grant "}, False),
        ("POST", token_url, {"error": None}, False),
        ("POST", token_url, {"error": 1}, False),
        ("POST", token_url, {"error": ["invalid_grant"]}, False),
        ("POST", token_url, {"error_description": "invalid_grant"}, False),
        ("POST", token_url, ["invalid_grant"], False),
        ("POST", token_url, "invalid_grant", False),
        ("POST", token_url, None, False),
        ("POST", token_url, {}, False),
    ]
    for method, url, payload, actionable in cases:
        request = httpx.Request(method, url)
        response = httpx.Response(400, json=payload, request=request)
        exc = httpx.HTTPStatusError("source failure", request=request, response=response)
        result, description = _classify_source_api_error(exc)
        assert result is actionable, (method, url, payload)
        assert isinstance(description, str) and description

    request = httpx.Request("POST", token_url)
    for content in (b"", b"not json"):
        response = httpx.Response(400, content=content, request=request)
        exc = httpx.HTTPStatusError("source failure", request=request, response=response)
        assert _classify_source_api_error(exc) == (False, "http_error: source failure")

    # Exception metadata cannot replace the actual response's provenance.
    for response in (
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(
            400,
            json={"error": "invalid_grant"},
            request=httpx.Request("GET", "https://gmail.googleapis.com/data"),
        ),
    ):
        exc = httpx.HTTPStatusError("source failure", request=request, response=response)
        assert _classify_source_api_error(exc) == (False, "error=invalid_grant")


async def test_source_origin_fetch_and_token_health_preserve_failure_and_reset(
    gmail_config: GmailConnectorConfig,
) -> None:
    """Real source callbacks keep diagnostics, metrics, isolated caches and reset."""
    from butlers.connectors.metrics import get_error_type

    failures = [
        (
            {"error": "invalid_grant", "error_description": "diagnostic"},
            "error=invalid_grant, description=diagnostic",
        ),
        (
            {"error": {"code": 503, "status": "UNAVAILABLE", "message": "retry later"}},
            "code=503, status=UNAVAILABLE, message=retry later",
        ),
        (b"", "http_error:"),
        (b"not json", "http_error:"),
        (httpx.ReadTimeout("source timeout"), "timeout: source timeout"),
    ]
    for failure, expected_description in failures:
        runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
        sibling = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
        runtime._metrics = MagicMock()
        runtime._access_token = "synthetic-access"
        runtime._token_expires_at = datetime.max.replace(tzinfo=UTC)
        runtime._last_history_id = "cursor-preserved"
        runtime._sent_ids_cache = frozenset({"sent-preserved"})
        runtime._last_checkpoint_save = 123.0
        before = (
            runtime._access_token,
            runtime._token_expires_at,
            runtime._last_history_id,
            runtime._sent_ids_cache,
            runtime._last_checkpoint_save,
        )
        primary = []
        primary_args = []
        primary_tracebacks = []
        requests = []

        async def transport(
            request,
            *,
            requests=requests,
            failure=failure,
            primary=primary,
            primary_args=primary_args,
            primary_tracebacks=primary_tracebacks,
        ):
            requests.append((request.method, str(request.url)))
            if len(requests) == 1:
                if isinstance(failure, Exception):
                    primary.append(failure)
                    primary_args.append(failure.args)
                    raise failure
                response = httpx.Response(
                    400,
                    **({"content": failure} if isinstance(failure, bytes) else {"json": failure}),
                    request=request,
                )
                original_raise = response.raise_for_status

                def capture_primary():
                    try:
                        original_raise()
                    except httpx.HTTPStatusError as exc:
                        primary.append(exc)
                        primary_args.append(exc.args)
                        primary_tracebacks.append(exc.__traceback__)
                        raise

                response.raise_for_status = capture_primary
                return response
            return httpx.Response(200, json={"id": "m1"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            runtime._http_client = client
            with pytest.raises(
                type(failure) if isinstance(failure, Exception) else httpx.HTTPStatusError
            ) as raised:
                await runtime._fetch_message("m1")
            assert raised.value is primary[0]
            assert raised.value.args == primary_args[0]
            traceback_nodes = []
            tb = raised.value.__traceback__
            while tb is not None:
                traceback_nodes.append(tb)
                tb = tb.tb_next
            assert any(tb.tb_frame.f_code.co_name == "_fetch_message" for tb in traceback_nodes)
            if primary_tracebacks:
                assert any(tb is primary_tracebacks[0] for tb in traceback_nodes)
            else:
                assert any(tb.tb_frame.f_code.co_name == "transport" for tb in traceback_nodes)
            state, description = runtime._get_health_state()
            assert state == "degraded"
            assert description is not None and expected_description in description
            assert sibling._get_health_state() == ("healthy", None)
            assert before == (
                runtime._access_token,
                runtime._token_expires_at,
                runtime._last_history_id,
                runtime._sent_ids_cache,
                runtime._last_checkpoint_save,
            )
            assert requests == [
                ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1?format=full")
            ]
            runtime._metrics.record_source_api_call.assert_called_once_with(
                api_method="messages.get", status="error"
            )
            runtime._metrics.record_error.assert_called_once_with(
                error_type=get_error_type(primary[0]), operation="fetch_message"
            )
            assert await runtime._fetch_message("m1") == {"id": "m1"}
            assert runtime._get_health_state() == ("healthy", None)
            runtime._metrics.record_source_api_call.assert_called_with(
                api_method="messages.get", status="success"
            )

    for code in ("invalid_grant", "unauthorized_client"):
        runtime = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
        sibling = GmailConnectorRuntime(gmail_config, cursor_pool=MagicMock())
        runtime._metrics = MagicMock()
        requests = []

        async def token_transport(request, *, requests=requests, code=code):
            requests.append((request.method, str(request.url)))
            if len(requests) == 1:
                return httpx.Response(400, json={"error": code}, request=request)
            return httpx.Response(
                200,
                json={"access_token": "synthetic-new", "expires_in": 3600, "token_type": "Bearer"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(token_transport)) as client:
            runtime._http_client = client
            with pytest.raises(httpx.HTTPStatusError):
                await runtime._get_access_token()
            assert runtime._get_health_state() == ("error", f"error={code}")
            assert sibling._get_health_state() == ("healthy", None)
            assert runtime._access_token is None and runtime._token_expires_at is None
            runtime._metrics.record_source_api_call.assert_called_once_with(
                api_method="token_refresh", status="error"
            )
            assert await runtime._get_access_token() == "synthetic-new"
            assert runtime._get_health_state() == ("healthy", None)
            assert requests == [("POST", "https://oauth2.googleapis.com/token")] * 2


async def test_filter_drop_logs_message_id_not_subject(
    gmail_runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    """Filter-drop log lines identify the message by id; the preview is still recorded."""
    import logging

    from butlers.ingestion_policy import PolicyDecision

    with (
        patch.object(
            gmail_runtime,
            "_fetch_message",
            new_callable=AsyncMock,
            return_value=_make_message(subject="Your verification code is 482913"),
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="block", matched_rule_type="sender_domain"),
        ),
        caplog.at_level(logging.INFO),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    assert "msg123" in caplog.text and "filtered out" in caplog.text
    assert "482913" not in caplog.text and "verification code" not in caplog.text
    assert "482913" not in gmail_runtime._filtered_event_buffer._rows[0][6]


async def test_auto_reply_is_ingested_as_tier2_with_its_marker_even_under_a_pass_through_rule(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    """bu-s11n0s.18: end to end, an auto-reply never reaches the Tier 1 envelope."""
    from butlers.ingestion_policy import PolicyDecision

    message = _make_message()
    message["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-replied"})
    submitted: list[dict[str, Any]] = []

    async def fake_submit(env: dict[str, Any]) -> None:
        submitted.append(env)

    with (
        patch.object(gmail_runtime, "_fetch_message", new_callable=AsyncMock, return_value=message),
        patch.object(
            gmail_runtime, "_submit_to_ingest_api", new=AsyncMock(side_effect=fake_submit)
        ),
        patch.object(
            gmail_runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
        patch.object(
            gmail_runtime._global_ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="pass_through"),
        ),
    ):
        await gmail_runtime._ingest_single_message("msg123")

    (envelope,) = submitted
    assert envelope["payload"]["raw"] is None
    assert envelope["control"]["ingestion_tier"] == "metadata"
    assert envelope["control"]["automated_kind"] == "auto_reply"


# ---------------------------------------------------------------------------
# Untrusted span marking (bu-xmueeq, REQ-connector-base-spec-004)
# ---------------------------------------------------------------------------


async def test_quoted_reply_marks_only_the_quoted_block(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    body = (
        "Sounds good, go ahead.\n\n"
        "On Tue, Oct 21, 2026 at 9:00 AM Bob <bob@example.com> wrote:\n"
        "> Please wire the deposit today.\n"
        "> Thanks, Bob"
    )
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(subject="Re: deposit", body_text=body)
    )

    text = envelope["payload"]["normalized_text"]
    (span,) = envelope["payload"]["untrusted_spans"]
    assert span["kind"] == "quoted"
    assert text[span["start"] : span["end"]].startswith("On Tue, Oct 21")
    assert text[span["start"] : span["end"]].endswith("Thanks, Bob")
    assert "Sounds good" not in text[span["start"] : span["end"]]
    parse_ingest_envelope(envelope)


async def test_forward_marker_starts_a_forwarded_span(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    body = "FYI\n\n---------- Forwarded message ---------\nFrom: Eve\nClick this link now"
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(subject="Fwd: urgent", body_text=body)
    )

    text = envelope["payload"]["normalized_text"]
    (span,) = envelope["payload"]["untrusted_spans"]
    assert span["kind"] == "forwarded"
    assert text[span["start"] :].startswith("---------- Forwarded message")
    assert span["end"] == len(text)

    from butlers.ingestion_policy import PolicyDecision

    message = _make_message(subject="Fwd: urgent", body_text=body)
    for stage in ("connector", "global", "submission", "label"):
        gmail_runtime._filtered_event_buffer._rows.clear()
        allow = PolicyDecision(action="pass_through")
        with (
            patch.object(gmail_runtime, "_fetch_message", AsyncMock(return_value=message)),
            patch.object(
                gmail_runtime._ingestion_policy,
                "evaluate",
                return_value=(
                    PolicyDecision(action="block", matched_rule_type="sender_domain")
                    if stage == "connector"
                    else allow
                ),
            ),
            patch.object(
                gmail_runtime._global_ingestion_policy,
                "evaluate",
                return_value=(
                    PolicyDecision(action="skip", matched_rule_type="sender_domain")
                    if stage == "global"
                    else allow
                ),
            ),
            patch.object(
                gmail_runtime, "_submit_to_ingest_api", AsyncMock(side_effect=ValueError("failed"))
            ),
        ):
            message["labelIds"] = ["CATEGORY_PROMOTIONS"] if stage == "label" else []
            if stage == "label":
                from dataclasses import replace

                gmail_runtime._label_filter = replace(
                    gmail_runtime._label_filter, exclude_labels=frozenset({"CATEGORY_PROMOTIONS"})
                )
            await gmail_runtime._ingest_single_message("msg123")
        stored = gmail_runtime._filtered_event_buffer._rows[-1][9]
        assert stored["sender"]["identity"] == "sender@example.com"
        assert stored["payload"]["untrusted_spans"] == [
            {"kind": "forwarded", "start": 0, "end": len("[no text]")}
        ]
        assert "normalized_text" not in stored["payload"]
        assert body not in str(stored)


async def test_forward_subject_without_marker_marks_the_whole_text(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    envelope = await gmail_runtime._build_ingest_envelope(
        _make_message(subject="Fwd: invoice", body_text="see below, the boundary got lost")
    )

    text = envelope["payload"]["normalized_text"]
    assert envelope["payload"]["untrusted_spans"] == [
        {"kind": "forwarded", "start": 0, "end": len(text)}
    ]


async def test_attached_message_marks_the_whole_text_embedded(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    message = _make_message(body_text="see attached")
    plain = message["payload"]
    message["payload"] = {
        "headers": plain["headers"],
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": plain["body"]},
            {"mimeType": "message/rfc822", "filename": "fwd.eml", "body": {"size": 10}},
        ],
    }
    envelope = await gmail_runtime._build_ingest_envelope(message)

    text = envelope["payload"]["normalized_text"]
    assert envelope["payload"]["untrusted_spans"] == [
        {"kind": "embedded", "start": 0, "end": len(text)}
    ]


async def test_plain_email_carries_no_spans(gmail_runtime: GmailConnectorRuntime) -> None:
    envelope = await gmail_runtime._build_ingest_envelope(_make_message())
    assert "untrusted_spans" not in envelope["payload"]


async def test_html_citation_metadata_tier_and_replay_keep_provider_quote(gmail_runtime):
    from butlers.connectors.filtered_event_buffer import _sanitize_replay_payload
    from butlers.connectors.gmail_policy import INGESTION_TIER_METADATA, MessagePolicyResult
    from butlers.ingestion_policy import PolicyDecision
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    message = _make_message(subject="Re: question", from_addr="Owner <owner@example.com>")
    markup = "owner before<blockquote type='cite'>quoted &lt;command&gt;</blockquote>owner after"
    message["payload"]["mimeType"] = "text/html"
    message["payload"]["body"]["data"] = base64.urlsafe_b64encode(markup.encode()).decode()
    envelope = await gmail_runtime._build_ingest_envelope(message)
    text = envelope["payload"]["normalized_text"]
    (span,) = envelope["payload"]["untrusted_spans"]
    assert text[span["start"] : span["end"]] == "quoted &lt;command&gt;"
    assert "owner before" not in text[span["start"] : span["end"]]
    assert envelope["sender"]["identity"] == "owner@example.com"
    parse_ingest_envelope(envelope)
    policy = MessagePolicyResult(
        should_ingest=True,
        ingestion_tier=INGESTION_TIER_METADATA,
        policy_tier="passive",
        assignment_rule="test",
        filter_reason="label_allowed",
        triage_action="pass_through",
    )
    for candidate in (message, _make_message(subject="Fwd: question", body_text="forwarded")):
        slim = await gmail_runtime._build_ingest_envelope(candidate, policy_result=policy)
        assert slim["payload"]["raw"] is None
        assert "owner before" not in slim["payload"]["normalized_text"]
        assert slim["payload"]["untrusted_spans"][0]["end"] == len(
            slim["payload"]["normalized_text"].strip()
        )
        parse_ingest_envelope(slim)
    malformed = _make_message(subject="Fwd: still metadata")
    malformed["payload"]["body"]["data"] = "a"
    slim = await gmail_runtime._build_ingest_envelope(malformed, policy_result=policy)
    assert slim["payload"]["raw"] is None
    assert slim["payload"]["untrusted_spans"][0]["kind"] == "forwarded"
    # Exercise the real entrypoint: preliminary decoding must not prevent
    # metadata policy or discard independently known forward facts on error.
    for subject in ("Fwd: still metadata", "ordinary unmarked subject"):
        for action in ("metadata_only", "pass_through"):
            broken = _make_message(subject=subject)
            broken["payload"]["body"]["data"] = "a"
            gmail_runtime._filtered_event_buffer._rows.clear()
            submitted = AsyncMock()
            with (
                patch.object(gmail_runtime, "_fetch_message", AsyncMock(return_value=broken)),
                patch.object(gmail_runtime, "_submit_to_ingest_api", submitted),
                patch.object(
                    gmail_runtime._ingestion_policy,
                    "evaluate",
                    return_value=PolicyDecision(action="pass_through"),
                ),
                patch.object(
                    gmail_runtime._global_ingestion_policy,
                    "evaluate",
                    return_value=PolicyDecision(action=action),
                ),
                patch.object(
                    gmail_runtime, "_resolve_body_attachment_refs", AsyncMock()
                ) as resolve,
            ):
                await gmail_runtime._ingest_single_message("msg123")
            if action == "metadata_only":
                submitted.assert_awaited_once()
                resolve.assert_not_awaited()
                delivered = submitted.await_args.args[0]
                assert delivered["payload"]["raw"] is None
                assert delivered["control"]["ingestion_tier"] == "metadata"
                assert delivered["payload"]["normalized_text"].strip() == f"Subject: {subject}"
                assert not gmail_runtime._filtered_event_buffer._rows
                parse_ingest_envelope(delivered)
            else:
                submitted.assert_not_awaited()
                row = gmail_runtime._filtered_event_buffer._rows[-1]
                assert row[8] == "error"
                delivered = row[9]
                _sanitize_replay_payload(delivered)
                assert delivered["payload"]["raw"] == {"message_id": "msg123"}
                assert delivered["payload"]["normalized_text"] == "[no text]"
            if subject.startswith("Fwd:"):
                assert delivered["payload"]["untrusted_spans"][0]["kind"] == "forwarded"
            else:
                assert "untrusted_spans" not in delivered["payload"]

    for quote_markup in (
        "<div class='gmail_quote'>quoted &lt;command&gt;</div>",
        "<div type='cite'>quoted &lt;command&gt;</div>",
        "<blockquote>quoted &lt;command&gt;",
    ):
        alternate = _make_message(subject="Re: question")
        alternate["payload"]["mimeType"] = "text/html"
        alternate["payload"]["body"]["data"] = base64.urlsafe_b64encode(
            ("owner before" + quote_markup).encode()
        ).decode()
        marked = await gmail_runtime._build_ingest_envelope(alternate)
        (range_,) = marked["payload"]["untrusted_spans"]
        assert marked["payload"]["normalized_text"][range_["start"] : range_["end"]] == (
            "quoted &lt;command&gt;"
        )

    # MIME preference remains text/plain, not an unselected HTML alternative.
    import copy

    mixed = copy.deepcopy(message)
    mixed["payload"] = {
        "mimeType": "multipart/alternative",
        "headers": message["payload"]["headers"],
        "parts": [message["payload"], _make_message(body_text="ordinary owner text")["payload"]],
    }
    plain = await gmail_runtime._build_ingest_envelope(mixed)
    assert "ordinary owner text" in plain["payload"]["normalized_text"]
    assert "untrusted_spans" not in plain["payload"]
    for stage in ("connector", "global", "submission"):
        gmail_runtime._filtered_event_buffer._rows.clear()
        with (
            patch.object(gmail_runtime, "_fetch_message", AsyncMock(return_value=message)),
            patch.object(
                gmail_runtime._ingestion_policy,
                "evaluate",
                return_value=(
                    PolicyDecision(action="block", matched_rule_type="sender_domain")
                    if stage == "connector"
                    else PolicyDecision(action="pass_through")
                ),
            ),
            patch.object(
                gmail_runtime._global_ingestion_policy,
                "evaluate",
                return_value=(
                    PolicyDecision(action="skip", matched_rule_type="sender_domain")
                    if stage == "global"
                    else PolicyDecision(action="pass_through")
                ),
            ),
            patch.object(
                gmail_runtime, "_submit_to_ingest_api", AsyncMock(side_effect=ValueError("failed"))
            ),
        ):
            await gmail_runtime._ingest_single_message("msg123")
        stored = gmail_runtime._filtered_event_buffer._rows[-1][9]
        _sanitize_replay_payload(stored)
        assert stored["payload"]["untrusted_spans"] == [
            {"kind": "quoted", "start": 0, "end": len("[no text]")}
        ]
        assert "owner before" not in str(stored)


async def test_static_identity_env_cannot_relabel_real_gmail_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB-injected credentials and the real envelope retain Gmail identity."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("GMAIL_USER_EMAIL", "test@example.com")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "ignored-provider")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "ignored-channel")
    config = GmailConnectorConfig.from_env(
        gmail_client_id="synthetic-client",
        gmail_client_secret="synthetic-secret",
        gmail_refresh_token="synthetic-refresh",
    )
    runtime = GmailConnectorRuntime(config, cursor_pool=MagicMock())
    first = await runtime._build_ingest_envelope(_make_message())
    replay = await runtime._build_ingest_envelope(_make_message())
    assert first["source"] == {
        "channel": "email",
        "provider": "gmail",
        "endpoint_identity": "gmail:user:test@example.com",
    }
    assert first["control"]["idempotency_key"] == (
        "gmail:gmail:user:test@example.com:<unique-msg-id@example.com>"
    )
    assert first["control"]["idempotency_key"] == replay["control"]["idempotency_key"]
    assert first["event"]["external_conversation_id"] == "thread456"


@pytest.mark.parametrize(
    ("raw", "legacy_pubsub", "process_pubsub", "legacy_backfill", "process_backfill"),
    [
        (None, False, False, True, True),
        ("true", True, True, True, True),
        ("1", True, True, True, True),
        ("yes", True, True, True, True),
        ("on", False, True, True, True),
        ("false", False, False, False, False),
        ("0", False, False, False, False),
        ("no", False, False, False, False),
        ("off", False, False, False, False),
        ("other", False, False, True, False),
        ("", False, False, True, False),
    ],
)
def test_shared_gmail_loader_keeps_each_boolean_contract(
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
    legacy_pubsub: bool,
    process_pubsub: bool,
    legacy_backfill: bool,
    process_backfill: bool,
) -> None:
    from butlers.connectors.gmail import GmailProcessConfig

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "synthetic-topic")
    for key in ("GMAIL_PUBSUB_ENABLED", "CONNECTOR_BACKFILL_ENABLED"):
        if raw is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, raw)
    legacy = GmailConnectorConfig._load_non_secret_env_config()
    process = GmailProcessConfig.from_env()
    assert legacy["gmail_pubsub_enabled"] is legacy_pubsub
    assert process.gmail_pubsub_enabled is process_pubsub
    assert legacy["connector_backfill_enabled"] is legacy_backfill
    assert process.connector_backfill_enabled is process_backfill


def test_shared_gmail_defaults_keep_real_account_overrides_and_credential_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butlers.connectors.gmail import GmailProcessConfig

    settings = {
        "SWITCHBOARD_MCP_URL": "http://switchboard.test/sse",
        "CONNECTOR_MAX_INFLIGHT": "17",
        "CONNECTOR_HEALTH_PORT": "40123",
        "GMAIL_WATCH_RENEW_INTERVAL_S": "321",
        "GMAIL_POLL_INTERVAL_S": "23",
        "GMAIL_PUBSUB_ENABLED": "true",
        "GMAIL_PUBSUB_TOPIC": "shared-topic",
        "GMAIL_PUBSUB_WEBHOOK_PORT": "40124",
        "GMAIL_PUBSUB_WEBHOOK_PATH": "/synthetic-hook",
        "GMAIL_PUBSUB_WEBHOOK_TOKEN": "synthetic-hook-token",
        "GMAIL_LABEL_INCLUDE": "INBOX",
        "GMAIL_LABEL_EXCLUDE": "TRASH",
        "CONNECTOR_BACKFILL_ENABLED": "false",
        "CONNECTOR_BACKFILL_POLL_INTERVAL_S": "24",
        "CONNECTOR_BACKFILL_PROGRESS_INTERVAL": "25",
        "GMAIL_ACCOUNT_RESCAN_INTERVAL_S": "26",
        "GMAIL_USER_EMAIL": "legacy@example.com",
        "GMAIL_SENT_LOOKBACK_DAYS": "27",
        "GMAIL_SENT_MAX_MESSAGES": "28",
        "GMAIL_CLIENT_SECRET": "ignored-env-secret",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    runtime = GmailConnectorConfig.from_env(
        gmail_client_id=" client ",
        gmail_client_secret=" injected ",
        gmail_refresh_token=" refresh ",
    )
    process = GmailProcessConfig.from_env()
    common = {
        "switchboard_mcp_url",
        "connector_max_inflight",
        "connector_health_port",
        "gmail_watch_renew_interval_s",
        "gmail_poll_interval_s",
        "gmail_pubsub_enabled",
        "gmail_pubsub_topic",
        "gmail_pubsub_webhook_port",
        "gmail_pubsub_webhook_path",
        "gmail_pubsub_webhook_token",
        "gmail_label_include",
        "gmail_label_exclude",
        "connector_backfill_enabled",
        "connector_backfill_poll_interval_s",
        "connector_backfill_progress_interval",
    }
    assert {k: getattr(runtime, k) for k in common} == {k: getattr(process, k) for k in common}
    assert runtime.gmail_client_secret == "injected"
    assert runtime.connector_endpoint_identity == "gmail:user:legacy@example.com"
    assert (runtime.gmail_sent_lookback_days, runtime.gmail_sent_max_messages) == (27, 28)
    assert process.gmail_account_rescan_interval_s == 26
    first = process.make_account_config(
        "one@example.com",
        "one-client",
        "one-secret",
        "one-refresh",
        {
            "label_include": "STARRED",
            "label_exclude": "SPAM",
            "poll_interval_s": 29,
            "pubsub_enabled": False,
            "pubsub_topic": "one-topic",
        },
        pubsub_webhook_token="one-hook",
    )
    second = process.make_account_config(
        "two@example.com", "two-client", "two-secret", "two-refresh"
    )
    assert (first.connector_endpoint_identity, second.connector_endpoint_identity) == (
        "gmail:user:one@example.com",
        "gmail:user:two@example.com",
    )
    assert first.connector_health_port == second.connector_health_port == 0
    assert first.gmail_client_secret == "one-secret" and second.gmail_client_secret == "two-secret"
    assert first.gmail_pubsub_webhook_token == "one-hook"
    assert second.gmail_pubsub_webhook_token == "synthetic-hook-token"
    assert (first.gmail_label_include, first.gmail_label_exclude, first.gmail_poll_interval_s) == (
        ("STARRED",),
        ("SPAM",),
        29,
    )
    assert first.gmail_pubsub_enabled is False and first.gmail_pubsub_topic == "one-topic"
    assert (
        second.gmail_label_include,
        second.gmail_label_exclude,
        second.gmail_poll_interval_s,
    ) == (("INBOX",), ("TRASH",), 23)
    assert second.gmail_pubsub_enabled is True and second.gmail_pubsub_topic == "shared-topic"
    with pytest.raises(ValueError, match="DB-resolved Gmail credentials"):
        GmailConnectorConfig.from_env(
            gmail_client_id="client", gmail_client_secret=" ", gmail_refresh_token="refresh"
        )


@pytest.mark.parametrize("entry", ["runtime", "process"])
def test_gmail_loader_preserves_error_order_and_path_only_inputs(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    from butlers.connectors.gmail import GmailProcessConfig

    monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "bad")
    monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "true")
    monkeypatch.delenv("GMAIL_PUBSUB_TOPIC", raising=False)
    loader = (
        GmailConnectorConfig._load_non_secret_env_config
        if entry == "runtime"
        else GmailProcessConfig.from_env
    )
    with pytest.raises(
        ValueError, match="CONNECTOR_MAX_INFLIGHT" if entry == "runtime" else "GMAIL_PUBSUB_TOPIC"
    ):
        loader()
    monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "false")
    if entry == "process":
        with pytest.raises(KeyError, match="SWITCHBOARD_MCP_URL"):
            loader()
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "8")
    monkeypatch.setenv("GMAIL_SENT_LOOKBACK_DAYS", "bad")
    monkeypatch.setenv("GMAIL_ACCOUNT_RESCAN_INTERVAL_S", "bad")
    with pytest.raises(
        ValueError,
        match="GMAIL_SENT_LOOKBACK_DAYS"
        if entry == "runtime"
        else "GMAIL_ACCOUNT_RESCAN_INTERVAL_S",
    ):
        loader()
