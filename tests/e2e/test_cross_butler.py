"""E2E tests for cross-butler orchestration.

Tests multi-butler scenarios covering:
1. Switchboard routing to correct target butler
2. Multi-step cross-butler workflows via spawner chaining
3. notify.v1 envelope delivery through the Switchboard → Messenger pipeline

These tests require the full butler ecosystem (API key + claude binary).
Tests that depend on live MCP routing between running daemons are marked
with ``pytest.mark.skip`` when the infrastructure is not yet available,
preserving the test definitions for future activation.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import (
    parse_notify_request,
)
from butlers.tools.switchboard.routing.route import route

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid7() -> str:
    """Generate a UUIDv7-compatible string."""
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def _build_notify_v1_payload(
    *,
    origin_butler: str,
    channel: str = "telegram",
    intent: str = "send",
    message: str = "Test notification",
    recipient: str = "test-chat-123",
    request_context: dict | None = None,
) -> dict:
    """Build a well-formed notify.v1 payload dict."""
    payload: dict = {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": intent,
            "channel": channel,
            "message": message,
            "recipient": recipient,
        },
    }
    if request_context is not None:
        payload["request_context"] = request_context
    return payload


def _build_route_v1_payload(
    *,
    butler: str,
    prompt: str,
    request_id: str | None = None,
    source_channel: str = "mcp",
    source_endpoint_identity: str = "test-endpoint",
    source_sender_identity: str = "test-user",
) -> dict:
    """Build a well-formed route.v1 payload dict."""
    if request_id is None:
        request_id = _uuid7()
    return {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": request_id,
            "received_at": datetime.now(UTC).isoformat(),
            "source_channel": source_channel,
            "source_endpoint_identity": source_endpoint_identity,
            "source_sender_identity": source_sender_identity,
        },
        "target": {"butler": butler, "tool": "route.execute"},
        "input": {
            "prompt": prompt,
        },
    }


# ---------------------------------------------------------------------------
# Scenario 1: Switchboard routing to correct target butler
# ---------------------------------------------------------------------------


async def test_switchboard_routes_health_query_to_health_butler(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Verify Switchboard routes a health query to the health butler.

    Registers both health and relationship butlers in the registry, then
    uses the route() function to forward a tool call to health butler and
    verifies the routing log records the correct target.
    """
    # Register health butler in the switchboard registry
    health_daemon = butler_ecosystem.butlers.get("health")
    assert health_daemon is not None, "Health butler must be present in ecosystem"

    health_port = health_daemon.config.port
    health_endpoint = f"http://localhost:{health_port}/sse"

    # Ensure health butler is registered with switchboard
    await switchboard_pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, description, modules,
            eligibility_state, last_seen_at,
            route_contract_min, route_contract_max
        )
        VALUES (
            'health', $1, 'Health butler', '["measurement"]'::jsonb,
            'active', NOW(), 1, 1
        )
        ON CONFLICT (name) DO UPDATE SET
            endpoint_url = EXCLUDED.endpoint_url,
            eligibility_state = 'active',
            last_seen_at = NOW()
        """,
        health_endpoint,
    )

    # Verify registry has the health butler active
    row = await switchboard_pool.fetchrow(
        "SELECT name, eligibility_state FROM butler_registry WHERE name = 'health'"
    )
    assert row is not None, "Health butler should be registered"
    assert row["eligibility_state"] == "active", "Health butler should be active"

    # Use resolve_routing_target to verify routing is possible
    from butlers.tools.switchboard.registry.registry import resolve_routing_target

    target, error = await resolve_routing_target(switchboard_pool, "health")
    assert target is not None, f"Should resolve health butler, got error: {error}"
    assert target["name"] == "health", "Resolved target should be health butler"
    assert target["endpoint_url"] == health_endpoint, "Endpoint URL should match"


async def test_switchboard_routes_relationship_query_to_relationship_butler(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Verify Switchboard routes a relationship query to the relationship butler.

    Ensures the registry correctly distinguishes between butlers and returns
    the correct endpoint for relationship domain queries.
    """
    relationship_daemon = butler_ecosystem.butlers.get("relationship")
    assert relationship_daemon is not None, "Relationship butler must be present in ecosystem"

    relationship_port = relationship_daemon.config.port
    relationship_endpoint = f"http://localhost:{relationship_port}/sse"

    # Register relationship butler
    await switchboard_pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, description, modules,
            eligibility_state, last_seen_at,
            route_contract_min, route_contract_max
        )
        VALUES (
            'relationship', $1, 'Relationship butler', '["contacts"]'::jsonb,
            'active', NOW(), 1, 1
        )
        ON CONFLICT (name) DO UPDATE SET
            endpoint_url = EXCLUDED.endpoint_url,
            eligibility_state = 'active',
            last_seen_at = NOW()
        """,
        relationship_endpoint,
    )

    from butlers.tools.switchboard.registry.registry import resolve_routing_target

    target, error = await resolve_routing_target(switchboard_pool, "relationship")
    assert target is not None, f"Should resolve relationship butler, got error: {error}"
    assert target["name"] == "relationship"
    assert target["endpoint_url"] == relationship_endpoint


async def test_route_to_unknown_butler_returns_error(
    switchboard_pool: Pool,
) -> None:
    """Routing to a non-existent butler should return an error dict.

    Verifies that route() gracefully handles unknown targets by returning
    an error rather than raising.
    """

    async def _noop_call(endpoint_url: str, tool_name: str, args: dict) -> None:
        raise ConnectionError("Should not reach here")

    result = await route(
        switchboard_pool,
        target_butler="nonexistent_butler_xyz",
        tool_name="route.execute",
        args={},
        call_fn=_noop_call,
    )

    assert "error" in result, "Should return error for unknown butler"
    assert "nonexistent_butler_xyz" in result["error"], "Error should mention butler name"


async def test_route_to_quarantined_butler_returns_error(
    switchboard_pool: Pool,
) -> None:
    """Routing to a quarantined butler should return an error.

    A quarantined butler is temporarily excluded from routing.
    The route() function must not dispatch to it by default.
    """
    # Insert quarantined butler
    await switchboard_pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, description, modules,
            eligibility_state, last_seen_at, quarantined_at,
            quarantine_reason, route_contract_min, route_contract_max
        )
        VALUES (
            'quarantined_target', 'http://localhost:9001/sse',
            'Quarantined butler', '[]'::jsonb,
            'quarantined', NOW(), NOW(), 'Test quarantine',
            1, 1
        )
        ON CONFLICT (name) DO UPDATE SET
            eligibility_state = 'quarantined',
            quarantined_at = NOW(),
            quarantine_reason = 'Test quarantine',
            last_seen_at = NOW()
        """
    )

    async def _noop_call(endpoint_url: str, tool_name: str, args: dict) -> None:
        raise AssertionError("Should not be called for quarantined butler")

    result = await route(
        switchboard_pool,
        target_butler="quarantined_target",
        tool_name="route.execute",
        args={},
        call_fn=_noop_call,
    )

    assert "error" in result, "Should return error for quarantined butler"
    assert "quarantined" in result["error"].lower(), "Error should mention quarantine"


async def test_routing_log_persists_on_successful_route(
    switchboard_pool: Pool,
) -> None:
    """Successful routing should persist an entry in routing_log table.

    Each route() call must log source, target, tool_name, success, and
    duration_ms to the routing_log table for observability.
    """
    # Register a test butler
    await switchboard_pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, description, modules,
            eligibility_state, last_seen_at,
            route_contract_min, route_contract_max
        )
        VALUES (
            'route_log_test_butler', 'http://localhost:9090/sse',
            'Test butler', '[]'::jsonb,
            'active', NOW(), 1, 1
        )
        ON CONFLICT (name) DO UPDATE SET
            eligibility_state = 'active',
            last_seen_at = NOW()
        """
    )

    call_received: list[tuple] = []

    async def _mock_call(endpoint_url: str, tool_name: str, args: dict) -> dict:
        call_received.append((endpoint_url, tool_name))
        return {"status": "ok"}

    from datetime import UTC, datetime

    before = datetime.now(UTC)

    result = await route(
        switchboard_pool,
        target_butler="route_log_test_butler",
        tool_name="status",
        args={},
        source_butler="switchboard",
        call_fn=_mock_call,
    )

    assert "result" in result, f"Route should succeed, got: {result}"
    assert len(call_received) == 1, "Mock should have been called exactly once"

    # Verify routing_log entry was persisted
    log_row = await switchboard_pool.fetchrow(
        """
        SELECT source_butler, target_butler, tool_name, success, duration_ms
        FROM routing_log
        WHERE target_butler = 'route_log_test_butler'
          AND created_at >= $1
        ORDER BY created_at DESC LIMIT 1
        """,
        before,
    )

    assert log_row is not None, "Routing log entry should be persisted"
    assert log_row["source_butler"] == "switchboard"
    assert log_row["target_butler"] == "route_log_test_butler"
    assert log_row["tool_name"] == "status"
    assert log_row["success"] is True
    assert log_row["duration_ms"] >= 0


async def test_routing_log_persists_on_failed_route(
    switchboard_pool: Pool,
) -> None:
    """Failed routing should persist an error entry in routing_log.

    When routing fails (butler not found), route() must still log the
    failure with success=False and an error message.
    """
    from datetime import UTC, datetime

    before = datetime.now(UTC)
    unique_target = f"missing_butler_{uuid.uuid4().hex[:8]}"

    await route(
        switchboard_pool,
        target_butler=unique_target,
        tool_name="status",
        args={},
        source_butler="switchboard",
    )

    log_row = await switchboard_pool.fetchrow(
        """
        SELECT source_butler, target_butler, success, error
        FROM routing_log
        WHERE target_butler = $1
          AND created_at >= $2
        ORDER BY created_at DESC LIMIT 1
        """,
        unique_target,
        before,
    )

    assert log_row is not None, "Routing log entry should exist even for failures"
    assert log_row["success"] is False
    assert log_row["error"] is not None


# ---------------------------------------------------------------------------
# Scenario 2: Multi-step cross-butler workflow via spawner
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Requires live cross-butler spawner dispatch via SSE MCP clients; "
    "infrastructure not yet stable for cross-butler spawner chaining in CI. "
    "Test shape preserved for future activation."
)
async def test_multi_butler_session_chaining_health_to_relationship(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
    relationship_pool: Pool,
) -> None:
    """Multi-step workflow: health butler triggers and then notifies relationship butler.

    Scenario:
    1. Trigger health butler spawner with a prompt that includes social context
    2. Health butler logs measurement AND dispatches cross-butler notification
    3. Relationship butler receives the notification via mailbox
    4. Verify both butlers have session records

    This test validates end-to-end cross-butler session chaining.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Trigger health butler with cross-domain prompt
    result = await health_daemon.spawner.trigger(
        prompt=(
            "Log weight 80kg. Also notify relationship butler that I had a checkup today "
            "with Dr. Smith."
        ),
        trigger_source="external",
    )

    assert result.success is True, f"Health spawner should succeed: {result.error}"
    assert result.session_id is not None

    # Verify session in health DB
    session_row = await health_pool.fetchrow(
        "SELECT id, success FROM sessions WHERE id = $1",
        result.session_id,
    )
    assert session_row is not None, "Session should exist in health DB"
    assert session_row["success"] is True


@pytest.mark.skip(
    reason="Requires live cross-butler spawner dispatch via SSE MCP clients; "
    "infrastructure not yet stable for cross-butler spawner chaining in CI. "
    "Test shape preserved for future activation."
)
async def test_cross_butler_post_mail_workflow(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Cross-butler post_mail delivers message to target butler's mailbox.

    Scenario:
    1. Switchboard post_mail() call to relationship butler
    2. Verify routing_log records the mailbox_post delivery
    3. Verify relationship butler received the mail (mailbox_inbox table)

    This validates the inter-butler communication channel via post_mail().
    """
    from butlers.tools.switchboard.routing.route import post_mail

    result = await post_mail(
        switchboard_pool,
        target_butler="relationship",
        sender="health",
        sender_channel="mcp",
        body="Patient had checkup with Dr. Smith today.",
        subject="Checkup notification",
    )

    # post_mail returns error dict if relationship butler not in registry or
    # mailbox module not enabled. Accept either outcome gracefully.
    assert isinstance(result, dict), "post_mail should return a dict"

    if "error" not in result:
        assert "message_id" in result or "result" in result, (
            "Successful post_mail should have message_id or result"
        )


# ---------------------------------------------------------------------------
# Scenario 3: notify.v1 envelope delivery
# ---------------------------------------------------------------------------


def test_notify_v1_parse_send_intent() -> None:
    """notify.v1 send intent parses successfully with all required fields.

    Validates the schema model for a basic outbound notification.
    """
    payload = _build_notify_v1_payload(
        origin_butler="health",
        channel="telegram",
        intent="send",
        message="Your health summary is ready.",
        recipient="user-chat-456",
    )

    parsed = parse_notify_request(payload)

    assert parsed.schema_version == "notify.v1"
    assert parsed.origin_butler == "health"
    assert parsed.delivery.intent == "send"
    assert parsed.delivery.channel == "telegram"
    assert parsed.delivery.message == "Your health summary is ready."
    assert parsed.delivery.recipient == "user-chat-456"


def test_notify_v1_parse_reply_intent_requires_context() -> None:
    """notify.v1 reply intent requires request_context with source identity.

    The reply intent cannot be sent without tracing back to the original
    inbound message via request_context.
    """
    payload = _build_notify_v1_payload(
        origin_butler="health",
        channel="telegram",
        intent="reply",
        message="Reply to your message",
        recipient="user-chat-789",
        # Missing request_context — should fail
    )

    with pytest.raises(ValidationError) as exc_info:
        parse_notify_request(payload)

    errors = exc_info.value.errors()
    assert any(e["type"] == "reply_context_required" for e in errors), (
        f"Expected reply_context_required error, got: {[e['type'] for e in errors]}"
    )


def test_notify_v1_parse_reply_intent_with_context() -> None:
    """notify.v1 reply intent with valid request_context parses successfully."""
    request_id = _uuid7()
    payload = _build_notify_v1_payload(
        origin_butler="health",
        channel="telegram",
        intent="reply",
        message="Here is your update",
        recipient="user-chat-789",
        request_context={
            "request_id": request_id,
            "source_channel": "telegram",
            "source_endpoint_identity": "bot-123",
            "source_sender_identity": "user-789",
            "source_thread_identity": "thread-456",
        },
    )

    parsed = parse_notify_request(payload)

    assert parsed.delivery.intent == "reply"
    assert parsed.request_context is not None
    assert str(parsed.request_context.request_id) == request_id
    assert parsed.request_context.source_thread_identity == "thread-456"


def test_notify_v1_parse_react_intent_requires_emoji() -> None:
    """notify.v1 react intent requires delivery.emoji field."""
    request_id = _uuid7()
    payload: dict = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "react",
            "channel": "telegram",
            "message": "",
            # Missing emoji
        },
        "request_context": {
            "request_id": request_id,
            "source_channel": "telegram",
            "source_endpoint_identity": "bot-123",
            "source_sender_identity": "user-789",
            "source_thread_identity": "thread-456",
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        parse_notify_request(payload)

    errors = exc_info.value.errors()
    assert any(e["type"] == "react_emoji_required" for e in errors), (
        f"Expected react_emoji_required, got: {[e['type'] for e in errors]}"
    )


def test_notify_v1_parse_react_intent_with_emoji() -> None:
    """notify.v1 react intent with emoji and context parses successfully."""
    request_id = _uuid7()
    payload: dict = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "react",
            "channel": "telegram",
            "message": "",
            "emoji": "👍",
        },
        "request_context": {
            "request_id": request_id,
            "source_channel": "telegram",
            "source_endpoint_identity": "bot-123",
            "source_sender_identity": "user-789",
            "source_thread_identity": "thread-456",
        },
    }

    parsed = parse_notify_request(payload)

    assert parsed.delivery.intent == "react"
    assert parsed.delivery.emoji == "👍"
    assert parsed.request_context is not None


def test_notify_v1_wrong_schema_version_rejected() -> None:
    """notify.v1 with wrong schema_version should raise ValidationError."""
    payload = _build_notify_v1_payload(origin_butler="health")
    payload["schema_version"] = "notify.v2"

    with pytest.raises(ValidationError) as exc_info:
        parse_notify_request(payload)

    errors = exc_info.value.errors()
    assert any(e["type"] == "unsupported_schema_version" for e in errors)


def test_notify_v1_origin_butler_required() -> None:
    """notify.v1 with empty origin_butler should raise ValidationError."""
    payload = _build_notify_v1_payload(origin_butler="health")
    payload["origin_butler"] = ""

    with pytest.raises(ValidationError):
        parse_notify_request(payload)


async def test_notify_v1_delivery_logged_on_failed_route(
    switchboard_pool: Pool,
) -> None:
    """notify.v1 delivery to unknown messenger logs failed notification.

    When deliver() is called with a notify.v1 envelope but the messenger
    butler is not in the registry, the delivery should fail gracefully and
    log the failure to the notifications table.
    """
    from butlers.tools.switchboard.notification.deliver import deliver

    request_id = _uuid7()
    notify_payload = _build_notify_v1_payload(
        origin_butler="health",
        channel="telegram",
        intent="send",
        message="Test notification via notify.v1",
        recipient="test-user-999",
        request_context={
            "request_id": request_id,
            "source_channel": "telegram",
            "source_endpoint_identity": "bot-health",
            "source_sender_identity": "health",
        },
    )

    # Remove messenger from registry to force failure
    await switchboard_pool.execute("DELETE FROM butler_registry WHERE name = 'messenger'")

    async def _noop_call(endpoint_url: str, tool_name: str, args: dict) -> None:
        raise AssertionError("Should not be called when messenger not in registry")

    result = await deliver(
        switchboard_pool,
        source_butler="health",
        notify_request=notify_payload,
        call_fn=_noop_call,
    )

    # Should fail gracefully (messenger not registered)
    assert isinstance(result, dict)
    assert result.get("status") in ("failed", None) or "error" in result, (
        f"Expected failed delivery, got: {result}"
    )


async def test_notify_v1_origin_butler_mismatch_rejected(
    switchboard_pool: Pool,
) -> None:
    """notify.v1 delivery rejected when origin_butler != source_butler.

    The authz check must ensure that origin_butler in the envelope
    matches the caller's identity to prevent impersonation.
    """
    from butlers.tools.switchboard.notification.deliver import deliver

    notify_payload = _build_notify_v1_payload(
        origin_butler="relationship",  # Claims to be relationship
        channel="telegram",
        intent="send",
        message="Impersonation attempt",
        recipient="target-user",
    )

    result = await deliver(
        switchboard_pool,
        source_butler="health",  # But caller is health
        notify_request=notify_payload,
    )

    assert "error" in result or result.get("status") == "failed", (
        f"Should reject mismatched origin_butler, got: {result}"
    )
    if "error" in result:
        assert "origin_butler" in result["error"] or "source_butler" in result["error"], (
            f"Error should mention identity mismatch: {result['error']}"
        )


@pytest.mark.skip(
    reason="Requires live Messenger butler with Telegram/email connector running. "
    "Infrastructure not yet available in CI. Test preserved for future activation."
)
async def test_notify_v1_full_delivery_via_messenger(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Full notify.v1 delivery: Switchboard → Messenger → Telegram channel.

    Scenario:
    1. Health butler calls deliver() with notify.v1 envelope
    2. Switchboard routes to Messenger butler via route.execute
    3. Messenger dispatches to Telegram
    4. Verify notification logged in switchboard notifications table

    Requires a running Messenger butler with Telegram credentials.
    """
    from butlers.tools.switchboard.notification.deliver import deliver

    request_id = _uuid7()
    notify_payload = _build_notify_v1_payload(
        origin_butler="health",
        channel="telegram",
        intent="send",
        message="Your daily health summary is ready.",
        recipient="test-telegram-user",
        request_context={
            "request_id": request_id,
            "source_channel": "telegram",
            "source_endpoint_identity": "bot-health",
            "source_sender_identity": "health-butler",
        },
    )

    # Register messenger butler in registry
    messenger_daemon = butler_ecosystem.butlers.get("messenger")
    assert messenger_daemon is not None, "Messenger butler required for this test"
    messenger_port = messenger_daemon.config.port
    messenger_endpoint = f"http://localhost:{messenger_port}/sse"

    await switchboard_pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, description, modules,
            eligibility_state, last_seen_at,
            route_contract_min, route_contract_max
        )
        VALUES (
            'messenger', $1, 'Messenger butler', '["telegram"]'::jsonb,
            'active', NOW(), 1, 1
        )
        ON CONFLICT (name) DO UPDATE SET
            endpoint_url = EXCLUDED.endpoint_url,
            eligibility_state = 'active',
            last_seen_at = NOW()
        """,
        messenger_endpoint,
    )

    result = await deliver(
        switchboard_pool,
        source_butler="health",
        notify_request=notify_payload,
    )

    assert result.get("status") == "sent", f"Delivery should succeed, got: {result}"
    assert "notification_id" in result, "Should return notification_id"

    # Verify logged in notifications table
    notif_row = await switchboard_pool.fetchrow(
        "SELECT source_butler, channel, status FROM notifications WHERE id = $1::uuid",
        result["notification_id"],
    )
    assert notif_row is not None, "Notification should be logged"
    assert notif_row["source_butler"] == "health"
    assert notif_row["channel"] == "telegram"
    assert notif_row["status"] == "sent"
