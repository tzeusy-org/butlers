"""E2E tests for complex health butler flows.

Tests the health butler's measurement tracking, medication management, and
spawner-driven tool execution with real LLM calls.

Scenarios:
1. Direct health tool execution: call measurement_log() directly against health DB,
   query measurements table, call measurement_latest() to verify round-trip
2. Medication tracking through real spawner: ecosystem['health'].spawner.trigger()
   with medication prompt, assert SpawnerResult.success, assert tool_calls contains
   medication calls, query medications table
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from butlers.tools.health import measurement_latest, measurement_log, medication_list

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem, CostTracker


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


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
# Scenario 2: Medication tracking through spawner
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

    Uses live LLM CLI spawner with real LLM calls.
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
