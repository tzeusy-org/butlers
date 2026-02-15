"""E2E performance and load tests — throughput, latency, concurrency, and cost.

Tests cover:
1. Serial dispatch under load: fire 5 concurrent triggers -> all complete, sessions are
   sequential
2. Lock released on error: trigger that errors out releases lock (subsequent trigger
   succeeds)
3. Connection pool queuing: fire 20 concurrent state_set calls -> all succeed (pool
   queues, not rejects)
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
# Scenario 1: Serial dispatch under load
# ---------------------------------------------------------------------------


async def test_serial_dispatch_under_load(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Multiple concurrent triggers should serialize, not deadlock.

    Tests:
    1. Fire 5 concurrent triggers at health butler
    2. All should succeed (not fail)
    3. Sessions should be sequential (non-overlapping timestamps)

    This validates the spawner's serial dispatch lock correctly queues
    concurrent triggers and executes them one at a time.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    n = 5
    start_time = datetime.now(UTC)

    # Fire N triggers concurrently
    tasks = [
        spawner.trigger(
            prompt=f"Record weight {70 + i}kg",
            trigger_source="external",
        )
        for i in range(n)
    ]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert sum(1 for r in results if r.success) == n, f"All triggers should succeed: {results}"
    assert all(r.session_id is not None for r in results)

    # Fetch sessions ordered by creation time
    sessions = await health_pool.fetch(
        """
        SELECT id, triggered_at, completed_at
        FROM sessions
        WHERE triggered_at >= $1
        AND trigger_source = 'external'
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= n, f"Should have at least {n} sessions, got {len(sessions)}"

    # Verify non-overlapping execution (serial dispatch)
    # Session i should complete before session i+1 starts
    for i in range(1, min(len(sessions), n)):
        prev_session = sessions[i - 1]
        curr_session = sessions[i]

        assert prev_session["completed_at"] is not None, f"Session {i - 1} should have completed_at"
        assert curr_session["triggered_at"] is not None, f"Session {i} should have triggered_at"

        # Serial execution: previous session completes before next starts
        assert prev_session["completed_at"] <= curr_session["triggered_at"], (
            f"Sessions {i - 1} and {i} overlap — serial dispatch lock violated"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Lock released on error
# ---------------------------------------------------------------------------


async def test_lock_release_after_error(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Trigger that errors out should release lock for subsequent triggers.

    Tests:
    1. Send a trigger that will cause an error
    2. Verify the session is logged
    3. Send another trigger immediately after
    4. Second trigger should succeed (lock was released)

    This validates that the spawner lock is properly released in finally
    blocks even when sessions fail, preventing deadlocks.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    start_time = datetime.now(UTC)

    # First trigger: intentionally cause an error with an impossible request
    result_1 = await spawner.trigger(
        "Call a tool that doesn't exist named fake_nonexistent_tool_xyz_12345",
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
        AND trigger_source = 'external'
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"


# ---------------------------------------------------------------------------
# Scenario 3: Connection pool queuing
# ---------------------------------------------------------------------------


async def test_pool_exhaustion_queues_gracefully(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Tool calls should queue on pool, not crash, when pool is saturated.

    Tests:
    1. Fire 20 concurrent tool calls (state_set) at one butler
    2. All should succeed (queued, not rejected)
    3. No errors even if pool size is smaller than request count

    This validates that asyncpg's connection pool correctly queues
    requests when all connections are busy, rather than rejecting them.
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        # Fire many tool calls concurrently
        n = 20
        tasks = [
            client.call_tool("state_set", {"key": f"load-pool-{i}", "value": i}) for i in range(n)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (queued, not rejected)
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 0, f"Pool saturation caused errors: {errors}"

        # Verify all writes succeeded
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "ok")
        assert success_count == n, f"Expected {n} successes, got {success_count}"
