"""E2E scheduling and cron lifecycle tests.

Validates timer-driven flows, cron lifecycle, and tick behavior per
docs/tests/e2e/scheduling.md:

1. TOML schedule sync (schedules synced to scheduled_tasks table)
2. Schedule CRUD via MCP tools
3. Timer + external trigger interleaving

Note: Tests for tick idempotency, TOML sync idempotency, schedule create
idempotency, disabled schedule skipping, and session metadata have been
removed — these call core scheduler functions directly (tick, sync_schedules,
schedule_create) and are already covered by tests/core/test_core_scheduler.py.
Only tests requiring the full butler ecosystem or MCP interface are retained.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client as MCPClient

from butlers.core.scheduler import tick as _tick

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# TOML Schedule Sync Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toml_schedule_sync(butler_ecosystem: ButlerEcosystem) -> None:
    """TOML schedules should be synced to scheduled_tasks table on startup.

    Validates that [[butler.schedule]] entries from butler.toml are present
    in the scheduled_tasks table with source='toml'.
    """
    # Check each butler for TOML schedules
    for butler_name, daemon in butler_ecosystem.butlers.items():
        pool = butler_ecosystem.pools[butler_name]

        # Get TOML schedules from config
        toml_schedules = daemon.config.schedules
        if not toml_schedules:
            continue

        # Query DB for TOML-sourced schedules
        async with pool.acquire() as conn:
            db_schedules = await conn.fetch(
                """
                SELECT name, cron, prompt, source, enabled
                FROM scheduled_tasks
                WHERE source = 'toml'
                ORDER BY name
                """
            )

        toml_names = {s["name"] for s in toml_schedules}
        db_names = {row["name"] for row in db_schedules}

        # All TOML schedules should be present in DB
        assert toml_names.issubset(db_names), (
            f"Butler {butler_name}: TOML schedules not synced. Missing: {toml_names - db_names}"
        )

        # Verify each TOML schedule matches DB row
        db_by_name = {row["name"]: row for row in db_schedules}
        for toml_sched in toml_schedules:
            name = toml_sched["name"]
            db_row = db_by_name.get(name)
            assert db_row is not None, f"Butler {butler_name}: schedule {name} not in DB"
            assert db_row["cron"] == toml_sched["cron"]
            assert db_row["prompt"] == toml_sched["prompt"]
            assert db_row["enabled"] is True


# ---------------------------------------------------------------------------
# Schedule CRUD via MCP Tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_crud_via_mcp(butler_ecosystem: ButlerEcosystem, health_pool: Pool) -> None:
    """Schedule management tools should work end-to-end.

    Validates schedule_create, schedule_list, schedule_update, schedule_delete
    via MCP client calls.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    port = health_daemon.config.port
    url = f"http://localhost:{port}/sse"

    test_schedule_name = f"e2e-test-schedule-{uuid.uuid4()}"

    async with MCPClient(url) as client:
        # CREATE: Create a new scheduled task
        create_result = await client.call_tool(
            "schedule_create",
            {
                "name": test_schedule_name,
                "cron": "0 */6 * * *",
                "prompt": "Run E2E test task every 6 hours",
            },
        )
        assert create_result is not None
        task_id = create_result.get("task_id")
        assert task_id is not None, "schedule_create should return task_id"

        # LIST: Verify new schedule appears in list
        list_result = await client.call_tool("schedule_list", {})
        assert list_result is not None
        assert "schedules" in list_result
        schedules = list_result["schedules"]
        assert any(s["name"] == test_schedule_name for s in schedules), (
            "New schedule should appear in schedule_list"
        )

        # UPDATE: Disable the schedule
        update_result = await client.call_tool(
            "schedule_update",
            {
                "task_id": task_id,
                "enabled": False,
            },
        )
        assert update_result is not None
        assert update_result.get("status") == "ok"

        # Verify schedule is disabled in DB
        async with health_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM scheduled_tasks WHERE id = $1::uuid", task_id
            )
            assert row is not None
            assert row["enabled"] is False, "Schedule should be disabled after update"

        # DELETE: Remove the schedule
        delete_result = await client.call_tool(
            "schedule_delete",
            {
                "task_id": task_id,
            },
        )
        assert delete_result is not None
        assert delete_result.get("status") == "ok"

        # Verify schedule is deleted from DB
        async with health_pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM scheduled_tasks WHERE id = $1::uuid)",
                task_id,
            )
            assert not exists, "Schedule should be deleted from DB"


# ---------------------------------------------------------------------------
# Timer + External Trigger Interleaving
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timer_external_interleaving(
    butler_ecosystem: ButlerEcosystem, health_pool: Pool
) -> None:
    """External and scheduled triggers should serialize, not deadlock.

    Validates that firing both an external trigger and a scheduled tick
    concurrently completes successfully (serial dispatch via spawner lock).
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner

    # Get initial session count
    initial_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")

    # Create a due scheduled task
    now = datetime.now(UTC)
    past = now - timedelta(minutes=5)
    task_name = f"test-interleaving-{uuid.uuid4()}"

    async with health_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, '*/5 * * * *', 'Scheduled task for interleaving test', 'db', true, $2)
            """,
            task_name,
            past,
        )

    # Fire external trigger and tick concurrently
    external_task = asyncio.create_task(
        spawner.trigger("Test external trigger", trigger_source="external")
    )
    tick_task = asyncio.create_task(_tick(health_pool, spawner.trigger))

    # Wait for both to complete (should serialize, not deadlock)
    external_result, tick_count = await asyncio.gather(external_task, tick_task)

    # Verify external trigger succeeded
    assert external_result.success, f"External trigger failed: {external_result.error}"

    # Verify tick dispatched at least one task
    assert tick_count >= 1, "Tick should dispatch at least one scheduled task"

    # Give spawner time to complete sessions
    await asyncio.sleep(2)

    # Verify at least 2 new sessions exist (one from external, one from tick)
    final_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")
    assert final_count >= initial_count + 2, (
        f"Expected at least 2 new sessions (external + scheduled), got {final_count - initial_count}"  # noqa: E501
    )

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE name = $1", task_name)
