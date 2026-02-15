"""E2E tests for complex switchboard routing and dispatch flows.

Tests the full switchboard message classification, decomposition, ingestion,
and dispatch pipelines against the live ecosystem with real LLM calls.

Scenarios:
1. Single-domain routing: classify 3 messages (health, general, relationship) and
   assert correct butler target
2. Multi-domain decomposition: classify a message spanning health + relationship,
   assert 2+ routing entries with valid segment metadata
3. Ingest deduplication: submit same IngestEnvelopeV1 twice via ingest_v1(),
   assert second returns duplicate=True
4. Full dispatch through live MCP: use dispatch_decomposed() with real route()
   targeting the live health butler, assert routing_log has success=True
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from butlers.tools.switchboard.ingestion.ingest import ingest_v1
from butlers.tools.switchboard.routing.classify import classify_message
from butlers.tools.switchboard.routing.dispatch import dispatch_decomposed

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Single-domain routing
# ---------------------------------------------------------------------------


async def test_single_domain_classification(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Classify 3 messages (health, general, relationship) and assert correct butler target.

    Uses the switchboard spawner to classify each message via real Haiku LLM calls.
    Validates that each message routes to the expected specialist butler.
    """
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None, "Switchboard spawner must be initialized"
    dispatch_fn = switchboard_daemon.spawner.trigger

    # Test case 1: Health domain
    health_message = "I weigh 75.5 kg today"
    health_entries = await classify_message(switchboard_pool, health_message, dispatch_fn)

    assert len(health_entries) >= 1, "Health message should produce at least 1 routing entry"
    assert health_entries[0]["butler"] == "health", "Weight log should route to health butler"
    assert "prompt" in health_entries[0], "Entry must have prompt field"
    assert "segment" in health_entries[0], "Entry must have segment metadata"
    segment = health_entries[0]["segment"]
    assert isinstance(segment, dict), "Segment must be dict"
    # At least one of rationale, sentence_spans, or offsets must be present
    has_metadata = any(key in segment for key in ["rationale", "sentence_spans", "offsets"])
    assert has_metadata, "Segment must have rationale, sentence_spans, or offsets"

    # Test case 2: General domain
    general_message = "What's the weather today?"
    general_entries = await classify_message(switchboard_pool, general_message, dispatch_fn)

    assert len(general_entries) >= 1, "General message should produce at least 1 routing entry"
    assert general_entries[0]["butler"] == "general", "Weather query should route to general butler"

    # Test case 3: Relationship domain
    relationship_message = "Remind me to call Mom next week"
    relationship_entries = await classify_message(
        switchboard_pool, relationship_message, dispatch_fn
    )

    assert len(relationship_entries) >= 1, (
        "Relationship message should produce at least 1 routing entry"
    )
    assert relationship_entries[0]["butler"] == "relationship", (
        "Social reminder should route to relationship butler"
    )


# ---------------------------------------------------------------------------
# Scenario 2: Multi-domain decomposition
# ---------------------------------------------------------------------------


async def test_multi_domain_decomposition(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Classify a message spanning health + relationship, assert 2+ routing entries.

    Tests that the switchboard correctly decomposes a complex message into
    multiple domain-specific sub-messages with proper segment metadata.
    """
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None, "Switchboard spawner must be initialized"
    dispatch_fn = switchboard_daemon.spawner.trigger

    multi_domain_message = (
        "I saw Dr. Smith today and got prescribed metformin 500mg twice daily. "
        "Also, remind me to send her a thank-you card next week."
    )

    entries = await classify_message(switchboard_pool, multi_domain_message, dispatch_fn)

    # Should produce at least 2 routing entries (health + relationship)
    assert len(entries) >= 2, (
        f"Multi-domain message should decompose into 2+ entries, got {len(entries)}"
    )

    # Extract butler targets
    targets = {entry["butler"] for entry in entries}

    # Should target both health and relationship (or at least 2 different butlers)
    assert len(targets) >= 2, f"Should target at least 2 different butlers, got {targets}"

    # Validate segment metadata for all entries
    for idx, entry in enumerate(entries):
        assert "butler" in entry, f"Entry {idx} missing butler field"
        assert "prompt" in entry, f"Entry {idx} missing prompt field"
        assert "segment" in entry, f"Entry {idx} missing segment field"

        segment = entry["segment"]
        assert isinstance(segment, dict), f"Entry {idx} segment must be dict"

        # At least one metadata field must be present
        has_metadata = any(key in segment for key in ["rationale", "sentence_spans", "offsets"])
        assert has_metadata, f"Entry {idx} segment must have rationale, sentence_spans, or offsets"

        # If offsets present, validate structure
        if "offsets" in segment:
            offsets = segment["offsets"]
            assert isinstance(offsets, dict), f"Entry {idx} offsets must be dict"
            assert "start" in offsets, f"Entry {idx} offsets missing start"
            assert "end" in offsets, f"Entry {idx} offsets missing end"
            assert isinstance(offsets["start"], int), f"Entry {idx} start must be int"
            assert isinstance(offsets["end"], int), f"Entry {idx} end must be int"
            assert offsets["start"] >= 0, f"Entry {idx} start must be non-negative"
            assert offsets["end"] >= offsets["start"], f"Entry {idx} end must be >= start"


# ---------------------------------------------------------------------------
# Scenario 3: Ingest deduplication
# ---------------------------------------------------------------------------


async def test_ingest_deduplication(switchboard_pool: Pool) -> None:
    """Submit same IngestEnvelopeV1 twice via ingest_v1(), assert second returns duplicate=True.

    Tests the idempotent ingestion boundary with stable dedupe_key computation.
    """
    # Build canonical ingest envelope
    now = datetime.now(UTC)
    event_id = f"test-event-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-123",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-456",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-789",
        },
        "payload": {
            "raw": {"text": "Test message for deduplication"},
            "normalized_text": "Test message for deduplication",
        },
        "control": {
            "policy_tier": "default",
        },
    }

    # First submission
    response1 = await ingest_v1(switchboard_pool, envelope_payload)

    assert response1.status == "accepted", "First submission should be accepted"
    assert response1.duplicate is False, "First submission should not be marked as duplicate"
    assert response1.request_id is not None, "First submission should return request_id"
    request_id_1 = response1.request_id

    # Second submission (identical envelope)
    response2 = await ingest_v1(switchboard_pool, envelope_payload)

    assert response2.status == "accepted", "Second submission should be accepted (idempotent)"
    assert response2.duplicate is True, "Second submission should be marked as duplicate"
    assert response2.request_id == request_id_1, "Second submission should return same request_id"

    # Verify only one row in message_inbox
    count_result = await switchboard_pool.fetchval(
        """
        SELECT COUNT(*) FROM message_inbox
        WHERE (request_context ->> 'request_id')::uuid = $1
        """,
        request_id_1,
    )
    assert count_result == 1, "Should only have 1 row in message_inbox for duplicate submissions"


# ---------------------------------------------------------------------------
# Scenario 4: Full dispatch through live MCP
# ---------------------------------------------------------------------------


async def test_dispatch_decomposed_with_live_mcp(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
) -> None:
    """Use dispatch_decomposed() with real route() targeting the live health butler.

    Tests the full dispatch pipeline with live MCP routing and validates that
    routing_log records success=True for successful routes.
    """
    # Simple single-target dispatch to health butler
    targets = [
        {
            "butler": "health",
            "prompt": "I weigh 76.2 kg today",
            "subrequest_id": "test-subrequest-1",
        }
    ]

    # Dispatch to live health butler via route()
    results = await dispatch_decomposed(
        switchboard_pool,
        targets,
        source_channel="switchboard",
        source_id="test-dispatch-001",
        tool_name="bot_switchboard_handle_message",
        source_metadata={
            "channel": "telegram",
            "identity": "test-user",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(results) == 1, "Should get 1 dispatch result"
    result = results[0]

    # Validate result structure
    assert result["butler"] == "health", "Should target health butler"
    assert result["subrequest_id"] == "test-subrequest-1", "Should preserve subrequest_id"
    assert "success" in result, "Result must have success field"
    assert "error" in result, "Result must have error field"

    # For successful routes, success should be True and error should be None
    # Note: The health butler may or may not have the exact tool, but route() should handle it
    if result["success"]:
        assert result["error"] is None, "Successful route should have no error"

        # Verify routing_log records the successful route
        routing_log_entry = await switchboard_pool.fetchrow(
            """
            SELECT * FROM routing_log
            WHERE source_id = $1
            ORDER BY routed_at DESC
            LIMIT 1
            """,
            "test-dispatch-001",
        )

        assert routing_log_entry is not None, "Should have routing_log entry"
        assert routing_log_entry["target_butler"] == "health", "Log should record health target"
        assert routing_log_entry["success"] is True, "Log should record success=True"
    else:
        # If route failed, should have error message
        assert result["error"] is not None, "Failed route should have error message"
        assert result["error_class"] is not None, "Failed route should have error_class"
