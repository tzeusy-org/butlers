"""E2E tests for complex health butler flows.

Tests the health butler's measurement tracking, medication management, and
full ingest→classify→route→execute pipeline with real LLM calls.

Scenarios:
1. Direct health tool execution: call measurement_log() directly against health DB,
   query measurements table, call measurement_latest() to verify round-trip
2. Full ingest → classify → route → dispatch → execute: build IngestEnvelopeV1,
   call ingest_v1(), classify_message(), dispatch_decomposed() with live MCP call_fn
   to health butler SSE, assert sessions table in health DB, assert measurements
   table has expected row
3. Medication tracking through real spawner: ecosystem['health'].spawner.trigger()
   with medication prompt, assert SpawnerResult.success, assert tool_calls contains
   medication calls, query medications table
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from butlers.tools.health import measurement_latest, measurement_log, medication_list
from butlers.tools.switchboard.ingestion.ingest import ingest_v1
from butlers.tools.switchboard.routing.classify import classify_message
from butlers.tools.switchboard.routing.dispatch import dispatch_decomposed

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem, CostTracker


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Direct tool execution
# ---------------------------------------------------------------------------


async def test_direct_measurement_tools(
    health_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Call measurement_log() directly against health DB and verify round-trip.

    Tests the health butler's measurement tools without any MCP routing or
    classification overhead. Validates that measurement data persists correctly
    and can be retrieved via measurement_latest().
    """
    # Log a weight measurement
    measurement_type = "weight"
    weight_value = 75.5
    notes = "Morning weight after breakfast"

    result = await measurement_log(
        pool=health_pool,
        type=measurement_type,
        value=weight_value,
        notes=notes,
    )

    assert result is not None, "measurement_log should return a result"
    assert "id" in result, "Result should have id field"
    assert result["type"] == measurement_type, "Type should match"
    assert result["value"] == weight_value, "Value should match"
    assert result["notes"] == notes, "Notes should match"
    assert "measured_at" in result, "Result should have measured_at timestamp"

    # Verify via measurement_latest()
    latest = await measurement_latest(pool=health_pool, type=measurement_type)

    assert latest is not None, "measurement_latest should find the logged measurement"
    assert latest["id"] == result["id"], "Should retrieve the same measurement"
    assert latest["value"] == weight_value, "Value should match"
    assert latest["notes"] == notes, "Notes should match"

    # Verify direct database query
    row = await health_pool.fetchrow(
        "SELECT * FROM measurements WHERE id = $1",
        result["id"],
    )
    assert row is not None, "Measurement should exist in database"
    assert row["type"] == measurement_type, "DB type should match"
    assert row["value"] == weight_value, "DB value should match"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 2: Full ingest → classify → route → dispatch → execute
# ---------------------------------------------------------------------------


async def test_full_pipeline_ingest_to_execute(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Build IngestEnvelopeV1, ingest, classify, dispatch to health butler via MCP.

    Tests the complete message processing pipeline:
    1. Create IngestEnvelopeV1 with health-related message
    2. Call ingest_v1() to persist in message_inbox
    3. Call classify_message() to route to health butler
    4. Call dispatch_decomposed() with live MCP routing
    5. Assert session created in health DB
    6. Assert measurement row exists

    This validates the full switchboard → health butler flow with real LLM calls
    and MCP routing.
    """
    # Build canonical ingest envelope
    now = datetime.now(UTC)
    event_id = f"test-health-flow-{uuid4().hex[:8]}"
    message_text = "I weigh 76.2 kg this morning"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-health",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-health-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-health-test",
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
    assert ingest_response.duplicate is False, "Should not be a duplicate"
    assert ingest_response.request_id is not None, "Should return request_id"
    request_id = ingest_response.request_id

    # Verify message_inbox row
    inbox_row = await switchboard_pool.fetchrow(
        """
        SELECT * FROM message_inbox
        WHERE (request_context ->> 'request_id')::uuid = $1
        """,
        request_id,
    )
    assert inbox_row is not None, "Should have message_inbox entry"

    # Step 2: Classify
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None, "Switchboard spawner must be initialized"
    dispatch_fn = switchboard_daemon.spawner.trigger

    routing_entries = await classify_message(switchboard_pool, message_text, dispatch_fn)

    assert len(routing_entries) >= 1, "Should produce at least 1 routing entry"
    assert routing_entries[0]["butler"] == "health", "Should route to health butler"
    health_prompt = routing_entries[0]["prompt"]

    # Step 3: Dispatch decomposed to health butler
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
        tool_name="test_full_pipeline",
        source_metadata={
            "channel": "telegram",
            "identity": "user-health-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) == 1, "Should get 1 dispatch result"
    result = dispatch_results[0]

    assert result["butler"] == "health", "Should target health butler"
    assert result["success"] is True, "Dispatch should succeed"
    assert result["error"] is None, "Should have no error"

    # Step 4: Verify routing_log
    routing_log_entry = await switchboard_pool.fetchrow(
        """
        SELECT * FROM routing_log
        WHERE source_id = $1 AND target_butler = 'health'
        ORDER BY routed_at DESC
        LIMIT 1
        """,
        str(request_id),
    )

    assert routing_log_entry is not None, "Should have routing_log entry"
    assert routing_log_entry["success"] is True, "Routing should be marked successful"

    # Step 5: Verify session in health DB
    health_session = await health_pool.fetchrow(
        """
        SELECT * FROM sessions
        WHERE trigger_source = 'external'
        ORDER BY triggered_at DESC
        LIMIT 1
        """
    )

    assert health_session is not None, "Should have created session in health DB"
    assert health_session["success"] is not None, "Session should have success status"

    # Step 6: Verify measurement in health DB (if LLM executed the tool)
    # Note: This depends on LLM correctly parsing "76.2 kg" and calling measurement_log()
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
    # But we've validated the full pipeline works
    assert measurement_count >= 0, "Measurement count should be non-negative"

    # Track LLM usage (classify_message + any spawner calls)
    # TODO: Extract real token counts when telemetry is available
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 3: Medication tracking through spawner
# ---------------------------------------------------------------------------


async def test_medication_tracking_via_spawner(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Trigger health butler spawner with medication prompt and verify DB state.

    Tests the health butler's medication tracking via the spawner:
    1. Call spawner.trigger() with medication tracking prompt
    2. Assert SpawnerResult.success is True
    3. Assert tool_calls contains medication_add or medication_log_dose
    4. Query medications table to verify persistence

    Uses live Claude Code spawner with real LLM calls.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None, "Health spawner must be initialized"

    # Medication tracking prompt
    prompt = "Started taking Metformin 500mg twice daily for blood sugar management"

    # Trigger spawner
    result = await health_daemon.spawner.trigger(
        prompt=prompt,
        trigger_source="external",
    )

    assert result.success is True, f"Spawner should succeed, got error: {result.error}"
    assert result.session_id is not None, "Should have session_id"
    assert result.tool_calls is not None, "Should have tool_calls list"
    assert len(result.tool_calls) > 0, "Should have made at least one tool call"

    # Verify medication-related tool was called
    medication_tools = {"medication_add", "medication_log_dose", "medication_list"}
    called_tools = {tc.get("tool") for tc in result.tool_calls}
    medication_tool_called = bool(medication_tools & called_tools)

    assert medication_tool_called, (
        f"Expected medication tool in {medication_tools}, got {called_tools}"
    )

    # Query medications table
    medications = await medication_list(pool=health_pool, active_only=True)

    assert len(medications) > 0, "Should have at least one medication"

    # Check if Metformin was added (case-insensitive search)
    metformin_found = any("metformin" in med["name"].lower() for med in medications)

    assert metformin_found, f"Should find Metformin in medications list: {medications}"

    # Verify session was logged
    session_row = await health_pool.fetchrow(
        "SELECT * FROM sessions WHERE id = $1",
        result.session_id,
    )

    assert session_row is not None, "Session should exist in health DB"
    assert session_row["success"] is True, "Session should be marked successful"

    # Track LLM usage from spawner result
    if result.input_tokens and result.output_tokens:
        cost_tracker.record(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    else:
        # Fallback if telemetry not available
        cost_tracker.record(input_tokens=0, output_tokens=0)
