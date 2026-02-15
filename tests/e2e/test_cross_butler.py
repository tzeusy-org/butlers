"""E2E tests for cross-butler orchestration and inter-butler communication.

Tests the butler ecosystem coordination patterns:
1. Heartbeat tick cycle: heartbeat butler ticks all registered butlers
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


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
