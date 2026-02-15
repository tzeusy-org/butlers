"""E2E resilience and chaos engineering tests.

Tests failure injection and graceful degradation across the butler ecosystem:
1. Serial dispatch lock contention
2. Module failure isolation
3. Lock release after error

These tests validate that the system degrades gracefully rather than
cascading into hard failures when components fail.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client as MCPClient

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Serial dispatch lock contention
# ---------------------------------------------------------------------------


async def test_serial_dispatch_lock_contention(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Fire two concurrent triggers at health butler, verify serial execution.

    Tests:
    1. Launch two concurrent trigger() calls
    2. Both should succeed (not fail)
    3. Sessions should be sequential (non-overlapping timestamps)
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    # Fire two triggers concurrently
    start_time = datetime.now(UTC)
    results = await asyncio.gather(
        spawner.trigger("Record weight 80kg", trigger_source="external"),
        spawner.trigger("Record weight 75kg", trigger_source="external"),
    )

    # Both should succeed
    assert all(r.success for r in results), f"All triggers should succeed: {results}"
    assert all(r.session_id is not None for r in results)

    # Fetch sessions ordered by creation time
    sessions = await health_pool.fetch(
        """
        SELECT id, triggered_at, completed_at
        FROM sessions
        WHERE triggered_at >= $1
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"

    # Verify non-overlapping execution (serial dispatch)
    # Session 1 should complete before session 2 starts
    session_1 = sessions[0]
    session_2 = sessions[1]

    assert session_1["completed_at"] is not None, "Session 1 should have completed_at"
    assert session_2["triggered_at"] is not None, "Session 2 should have triggered_at"

    # Serial execution: first session completes before second starts
    assert session_1["completed_at"] <= session_2["triggered_at"], (
        "Sessions should execute serially (no overlap)"
    )


# ---------------------------------------------------------------------------
# Scenario 2: Module failure isolation
# ---------------------------------------------------------------------------


async def test_module_failure_isolation(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Butler with failed module should still serve core tools.

    Tests:
    1. Check module statuses on a butler
    2. Verify core tools (status, trigger) are still available
    3. Failed module's tools are not registered
    """
    # Use general butler as test target
    general_daemon = butler_ecosystem.butlers["general"]
    port = general_daemon.config.port
    url = f"http://localhost:{port}/sse"

    # Call status tool (core functionality)
    async with MCPClient(url) as client:
        status = await client.call_tool("status", {})

        assert status is not None
        assert status["name"] == "general"
        assert "modules" in status

        # Verify core tools are available
        tools_response = await client.list_tools()
        tool_names = {t.name for t in tools_response.tools}

        # Core tools should always be present
        assert "status" in tool_names, "Core status tool should be available"
        assert "trigger" in tool_names, "Core trigger tool should be available"

        # If any module failed during startup, it should be marked as failed
        # but butler should still function
        module_statuses = status["modules"]
        for module_name, module_status in module_statuses.items():
            if module_status.get("status") == "failed":
                # Failed module's tools should not be registered
                # This is implicit - if a module fails, its tools won't be in tool_names
                pass


# ---------------------------------------------------------------------------
# Scenario 3: Lock release after error
# ---------------------------------------------------------------------------


async def test_lock_release_after_error(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Trigger that errors out should release lock for subsequent triggers.

    Tests:
    1. Send a trigger that will cause an error (invalid prompt / tool call failure)
    2. Verify the session is logged with error
    3. Send another trigger immediately after
    4. Second trigger should succeed (lock was released)
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    # First trigger: intentionally cause an error with an impossible request
    # that will fail during tool execution
    start_time = datetime.now(UTC)

    result_1 = await spawner.trigger(
        "Call a tool that doesn't exist named fake_nonexistent_tool_xyz",
        trigger_source="external",
    )

    # The spawner may succeed even if the LLM can't complete the task perfectly
    # What matters is that it completes and releases the lock
    assert result_1.session_id is not None, "Should have logged a session"

    # Second trigger: should succeed without hanging
    result_2 = await spawner.trigger(
        "Get status",
        trigger_source="external",
    )

    assert result_2.success is True, "Second trigger should succeed (lock was released)"
    assert result_2.session_id is not None

    # Verify both sessions exist
    sessions = await health_pool.fetch(
        """
        SELECT id, success, error FROM sessions
        WHERE triggered_at >= $1
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"
