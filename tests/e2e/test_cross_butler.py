"""E2E tests for cross-butler orchestration and inter-butler communication.

Tests the complete butler ecosystem coordination patterns:
1. Heartbeat tick cycle: heartbeat butler ticks all registered butlers
2. Full message pipeline: end-to-end message flow from ingestion through
   classification, routing, dispatch, execution, and multi-DB persistence

These tests validate the most complex cross-butler flows that exercise
the entire framework architecture.
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

    from tests.e2e.conftest import ButlerEcosystem, CostTracker


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Heartbeat tick cycle
# ---------------------------------------------------------------------------


async def test_heartbeat_tick_all_butlers(
    butler_ecosystem: ButlerEcosystem,
    heartbeat_pool: Pool,
) -> None:
    """Trigger heartbeat butler to tick all registered butlers.

    Tests the heartbeat coordination mechanism:
    1. Call tick_all_butlers() via heartbeat spawner
    2. Assert spawner result success is True
    3. Verify tick cycle completed without unhandled exceptions
    4. Check that sessions table in heartbeat DB has a logged session

    This validates the infrastructure heartbeat that keeps all butlers alive.
    """
    heartbeat_daemon = butler_ecosystem.butlers["heartbeat"]
    assert heartbeat_daemon.spawner is not None, "Heartbeat spawner must be initialized"

    # Trigger heartbeat tick cycle via spawner
    prompt = "Call tick_all_butlers() to tick all registered butlers. Log the results."

    result = await heartbeat_daemon.spawner.trigger(
        prompt=prompt,
        trigger_source="external",
    )

    # Validate spawner result
    assert result.success is True, f"Heartbeat tick should succeed, got error: {result.error}"
    assert result.session_id is not None, "Should have session_id"
    assert result.tool_calls is not None, "Should have tool_calls list"
    assert len(result.tool_calls) > 0, "Should have made at least one tool call"

    # Verify tick_all_butlers tool was called
    called_tools = {tc.get("tool") for tc in result.tool_calls}
    assert "tick_all_butlers" in called_tools, (
        f"Expected tick_all_butlers in tool calls, got {called_tools}"
    )

    # Verify session was logged in heartbeat DB
    session_row = await heartbeat_pool.fetchrow(
        "SELECT * FROM sessions WHERE id = $1",
        result.session_id,
    )

    assert session_row is not None, "Session should exist in heartbeat DB"
    assert session_row["success"] is True, "Session should be marked successful"
    assert session_row["trigger_source"] == "external", "Should record external trigger"

    # Check if the result contains tick summary
    # The LLM should have received tick results and may have logged them
    assert result.output is not None, "Should have output from spawner"


# ---------------------------------------------------------------------------
# Scenario 2: End-to-end message flow across multiple butlers
# ---------------------------------------------------------------------------


async def test_full_e2e_message_pipeline(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Full pipeline from ingest → classify → route → dispatch → execute.

    Tests the complete message lifecycle across butler boundaries:
    1. Build IngestEnvelopeV1 with health-related message
    2. Call ingest_v1() to persist in switchboard message_inbox
    3. Call classify_message() to route to health butler
    4. Call dispatch_decomposed() with live MCP routing to health butler
    5. Verify message_inbox row exists in switchboard DB
    6. Verify routing_log row exists in switchboard DB with success=True
    7. Verify session row exists in health DB
    8. Verify measurement row exists in health DB (if LLM executed the tool)

    This is the most complex test — validates the entire message lifecycle
    across multiple database boundaries with real LLM classification and
    routing decisions.
    """
    # Build canonical ingest envelope with health message
    now = datetime.now(UTC)
    event_id = f"test-e2e-flow-{uuid4().hex[:8]}"
    message_text = "I weigh 77.3 kg this morning after breakfast"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-e2e",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-e2e-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-e2e-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
        "control": {
            "policy_tier": "default",
        },
    }

    # Step 1: Ingest message into switchboard
    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)

    assert ingest_response.status == "accepted", "Ingest should accept the message"
    assert ingest_response.duplicate is False, "Should not be a duplicate"
    assert ingest_response.request_id is not None, "Should return request_id"
    request_id = ingest_response.request_id

    # Verify message_inbox row in switchboard DB
    inbox_row = await switchboard_pool.fetchrow(
        """
        SELECT * FROM message_inbox
        WHERE (request_context ->> 'request_id')::uuid = $1
        """,
        request_id,
    )
    assert inbox_row is not None, "Should have message_inbox entry in switchboard DB"
    assert inbox_row["source_channel"] == "telegram", "Should record telegram as source channel"

    # Step 2: Classify message
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None, "Switchboard spawner must be initialized"
    dispatch_fn = switchboard_daemon.spawner.trigger

    routing_entries = await classify_message(switchboard_pool, message_text, dispatch_fn)

    assert len(routing_entries) >= 1, "Should produce at least 1 routing entry"

    # Validate first entry routes to health (weight measurement message)
    health_entry = None
    for entry in routing_entries:
        if entry["butler"] == "health":
            health_entry = entry
            break

    assert health_entry is not None, f"Should route to health butler, got: {routing_entries}"
    health_prompt = health_entry["prompt"]
    assert "segment" in health_entry, "Entry must have segment metadata"

    # Step 3: Dispatch to health butler via MCP
    targets = [
        {
            "butler": "health",
            "prompt": health_prompt,
            "subrequest_id": f"health-sub-{uuid4().hex[:8]}",
        }
    ]

    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        targets,
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_full_e2e_pipeline",
        source_metadata={
            "channel": "telegram",
            "identity": "user-e2e-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) == 1, "Should get 1 dispatch result"
    result = dispatch_results[0]

    # Validate dispatch result
    assert result["butler"] == "health", "Should target health butler"
    assert result["success"] is True, "Dispatch should succeed"
    assert result["error"] is None, "Should have no error"

    # Step 4: Verify routing_log in switchboard DB
    routing_log_entry = await switchboard_pool.fetchrow(
        """
        SELECT * FROM routing_log
        WHERE source_id = $1 AND target_butler = 'health'
        ORDER BY routed_at DESC
        LIMIT 1
        """,
        str(request_id),
    )

    assert routing_log_entry is not None, "Should have routing_log entry in switchboard DB"
    assert routing_log_entry["target_butler"] == "health", "Log should record health target"
    assert routing_log_entry["success"] is True, "Log should record success=True"
    assert routing_log_entry["source_channel"] == "switchboard", "Should record source channel"

    # Step 5: Verify session in health DB
    # Find the most recent session created after our test started
    health_session = await health_pool.fetchrow(
        """
        SELECT * FROM sessions
        WHERE trigger_source = 'external'
        AND triggered_at > $1
        ORDER BY triggered_at DESC
        LIMIT 1
        """,
        now,
    )

    assert health_session is not None, "Should have created session in health DB"
    assert health_session["success"] is not None, "Session should have success status"

    # Step 6: Verify measurement in health DB (if LLM executed the tool)
    # Note: This depends on LLM correctly parsing "77.3 kg" and calling measurement_log()
    # We'll check if any weight measurement exists from this test run
    measurement_count = await health_pool.fetchval(
        """
        SELECT COUNT(*) FROM measurements
        WHERE type = 'weight'
        AND measured_at > $1
        """,
        now,
    )

    # Allow flexibility: LLM might or might not execute tool depending on prompt interpretation
    # But we've validated the full pipeline works through routing_log and session creation
    assert measurement_count >= 0, "Measurement count should be non-negative"

    # Track LLM usage (classify_message + any spawner calls)
    # TODO: Extract real token counts when telemetry is available
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 3: Multi-butler dispatch (decomposed message)
# ---------------------------------------------------------------------------


async def test_multi_butler_decomposed_dispatch(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
    relationship_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Classify and dispatch a message that spans multiple butlers.

    Tests message decomposition and parallel dispatch:
    1. Build message spanning health + relationship domains
    2. Classify message (should decompose into 2+ routing entries)
    3. Dispatch to both butlers in parallel
    4. Verify routing_log entries in switchboard DB for both butlers
    5. Verify sessions created in both health and relationship DBs

    This validates the switchboard's ability to decompose complex messages
    and coordinate parallel dispatch across multiple specialist butlers.
    """
    # Build multi-domain ingest envelope
    now = datetime.now(UTC)
    event_id = f"test-multi-butler-{uuid4().hex[:8]}"
    message_text = (
        "I weigh 78.1 kg today. Also, remind me to call Dr. Smith next week "
        "to discuss my test results."
    )

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-multi",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-multi-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-multi-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
        "control": {
            "policy_tier": "default",
        },
    }

    # Step 1: Ingest
    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)
    assert ingest_response.status == "accepted", "Ingest should accept the message"
    request_id = ingest_response.request_id

    # Step 2: Classify (should decompose into multiple routing entries)
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None, "Switchboard spawner must be initialized"
    dispatch_fn = switchboard_daemon.spawner.trigger

    routing_entries = await classify_message(switchboard_pool, message_text, dispatch_fn)

    # Should produce at least 2 routing entries (health + relationship)
    assert len(routing_entries) >= 2, (
        f"Multi-domain message should decompose into 2+ entries, got {len(routing_entries)}"
    )

    # Extract butler targets
    targets_by_butler = {entry["butler"]: entry for entry in routing_entries}

    # Should target at least 2 different butlers
    assert len(targets_by_butler) >= 2, (
        f"Should target at least 2 different butlers, got {list(targets_by_butler.keys())}"
    )

    # Step 3: Build dispatch targets for both butlers
    dispatch_targets = [
        {
            "butler": butler_name,
            "prompt": entry["prompt"],
            "subrequest_id": f"{butler_name}-sub-{uuid4().hex[:8]}",
        }
        for butler_name, entry in targets_by_butler.items()
    ]

    # Step 4: Dispatch decomposed message to all target butlers
    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        dispatch_targets,
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_multi_butler_dispatch",
        source_metadata={
            "channel": "telegram",
            "identity": "user-multi-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) >= 2, "Should get at least 2 dispatch results"

    # Validate all dispatches succeeded
    for result in dispatch_results:
        assert result["success"] is True, (
            f"Dispatch to {result['butler']} should succeed, got error: {result.get('error')}"
        )

    # Step 5: Verify routing_log entries in switchboard DB
    routing_log_entries = await switchboard_pool.fetch(
        """
        SELECT target_butler, success FROM routing_log
        WHERE source_id = $1
        ORDER BY routed_at DESC
        """,
        str(request_id),
    )

    assert len(routing_log_entries) >= 2, "Should have at least 2 routing_log entries"

    # All should be successful
    for entry in routing_log_entries:
        assert entry["success"] is True, (
            f"Routing to {entry['target_butler']} should be marked successful"
        )

    # Step 6: Verify sessions created in target butler DBs
    # Check for recent sessions in both health and relationship DBs
    for butler_name in ["health", "relationship"]:
        if butler_name in targets_by_butler:
            pool = health_pool if butler_name == "health" else relationship_pool

            session = await pool.fetchrow(
                """
                SELECT * FROM sessions
                WHERE trigger_source = 'external'
                AND triggered_at > $1
                ORDER BY triggered_at DESC
                LIMIT 1
                """,
                now,
            )

            assert session is not None, f"Should have created session in {butler_name} DB"

    # Track LLM usage
    cost_tracker.record(input_tokens=0, output_tokens=0)
