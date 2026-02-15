"""E2E scheduling and cron lifecycle tests.

Validates timer-driven flows, cron lifecycle, and tick behavior per
docs/tests/e2e/scheduling.md:

1. Heartbeat tick triggers all butlers
2. Tick idempotency (double-tick does not duplicate sessions)
3. TOML schedule sync (schedules synced to scheduled_tasks table)
4. Schedule CRUD via MCP tools
5. Timer + external trigger interleaving
6. Disabled schedule skipped
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
# Heartbeat Tick Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_tick_triggers_all_butlers(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    heartbeat_pool: Pool,
) -> None:
    """Heartbeat tick should create sessions in all registered butlers.

    Validates that calling tick() on the heartbeat butler triggers every
    registered butler (except heartbeat itself) and creates a new session
    with trigger_source="heartbeat" in each butler's DB.
    """
    # Get list of registered butlers from switchboard registry
    async with switchboard_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name FROM butler_registry
            WHERE eligibility_state = 'active'
            ORDER BY name
            """
        )
        registered_butlers = {row["name"] for row in rows}

    # Record session counts before tick (exclude heartbeat since it ticks others)
    session_counts_before: dict[str, int] = {}
    for butler_name in registered_butlers:
        if butler_name == "heartbeat":
            continue
        if butler_name in butler_ecosystem.pools:
            pool = butler_ecosystem.pools[butler_name]
            count = await pool.fetchval("SELECT COUNT(*) FROM sessions")
            session_counts_before[butler_name] = count

    # Fire heartbeat tick via MCP tool
    heartbeat_daemon = butler_ecosystem.butlers["heartbeat"]
    port = heartbeat_daemon.config.butler.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        result = await client.call_tool("tick_all_butlers", {})

    # Verify tick completed successfully
    assert result is not None
    assert "total" in result
    assert "successful" in result
    assert "failed" in result

    # Give sessions time to complete (async spawner invocations)
    await asyncio.sleep(2)

    # Verify each butler (except heartbeat) got a new session
    for butler_name in registered_butlers:
        if butler_name == "heartbeat":
            continue
        if butler_name not in butler_ecosystem.pools:
            continue

        pool = butler_ecosystem.pools[butler_name]
        count_after = await pool.fetchval("SELECT COUNT(*) FROM sessions")
        count_before = session_counts_before.get(butler_name, 0)

        assert count_after > count_before, (
            f"Butler {butler_name} did not receive heartbeat trigger "
            f"(before={count_before}, after={count_after})"
        )

        # Verify trigger_source is heartbeat-related
        latest_session = await pool.fetchrow(
            """
            SELECT trigger_source FROM sessions
            ORDER BY created_at DESC LIMIT 1
            """
        )
        assert latest_session is not None
        # Trigger source should be either "heartbeat" or "schedule:heartbeat-cycle"
        assert (
            "heartbeat" in latest_session["trigger_source"]
            or "schedule:" in latest_session["trigger_source"]
        ), f"Unexpected trigger_source: {latest_session['trigger_source']}"


@pytest.mark.asyncio
async def test_tick_idempotency(health_pool: Pool) -> None:
    """Running tick() twice in quick succession should not duplicate sessions.

    Validates that tick() is idempotent within a scheduling period: if a task
    has already been dispatched, calling tick() again immediately should not
    dispatch it again because next_run_at has been advanced.
    """
    # Create a test scheduled task with next_run_at in the past
    now = datetime.now(UTC)
    past = now - timedelta(hours=1)

    task_name = f"test-idempotency-{uuid.uuid4()}"
    async with health_pool.acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, '0 * * * *', 'Test idempotency tick', 'db', true, $2)
            RETURNING id
            """,
            task_name,
            past,
        )

    # Get initial session count
    initial_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")

    # Get health butler's spawner for tick dispatch
    health_daemon = None
    # Import ecosystem dynamically to access spawner
    import inspect

    from tests.e2e.conftest import butler_ecosystem  # noqa: F401

    frame = inspect.currentframe()
    while frame:
        if "butler_ecosystem" in frame.f_locals:
            ecosystem = frame.f_locals["butler_ecosystem"]
            health_daemon = ecosystem.butlers.get("health")
            break
        frame = frame.f_back

    if health_daemon is None:
        pytest.skip("Could not access health butler daemon for tick test")

    spawner = health_daemon.spawner

    # First tick: should dispatch the due task
    dispatched_1 = await _tick(health_pool, spawner.trigger)
    assert dispatched_1 >= 1, "First tick should dispatch at least one task"

    # Give spawner time to complete
    await asyncio.sleep(1)

    # Second tick immediately after: should NOT dispatch again
    dispatched_2 = await _tick(health_pool, spawner.trigger)
    assert dispatched_2 == 0, "Second tick should not dispatch any tasks (next_run_at advanced)"

    # Verify only one new session was created (from first tick)
    final_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")
    # Allow for exactly one new session (the first tick)
    # Note: there might be other sessions from other tests, so check delta
    assert final_count == initial_count + 1, (
        f"Expected exactly one new session, got {final_count - initial_count}"
    )

    # Cleanup
    async with health_pool.acquire() as conn:
        await conn.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)


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
        toml_schedules = daemon.config.butler.schedule
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


@pytest.mark.asyncio
async def test_toml_sync_idempotency(health_pool: Pool) -> None:
    """Restarting daemon (re-syncing TOML) should not duplicate schedule rows.

    Validates that sync_schedules() is idempotent and updates existing rows
    rather than creating duplicates.
    """
    from butlers.core.scheduler import sync_schedules

    # Create a test schedule as if from TOML
    schedules = [{"name": "test-sync-idempotent", "cron": "0 9 * * *", "prompt": "Daily summary"}]

    # First sync
    await sync_schedules(health_pool, schedules)

    # Count rows
    count_1 = await health_pool.fetchval(
        """
        SELECT COUNT(*) FROM scheduled_tasks
        WHERE name = 'test-sync-idempotent'
        """
    )
    assert count_1 == 1, "First sync should create one row"

    # Second sync (simulates daemon restart)
    await sync_schedules(health_pool, schedules)

    # Count rows again
    count_2 = await health_pool.fetchval(
        """
        SELECT COUNT(*) FROM scheduled_tasks
        WHERE name = 'test-sync-idempotent'
        """
    )
    assert count_2 == 1, "Second sync should not duplicate rows"

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE name = 'test-sync-idempotent'")


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
    port = health_daemon.config.butler.port
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


@pytest.mark.asyncio
async def test_schedule_create_idempotency(health_pool: Pool) -> None:
    """Creating a schedule with duplicate name should fail.

    Validates that scheduled_tasks.name has a unique constraint and duplicate
    creation attempts are rejected.
    """
    from butlers.core.scheduler import schedule_create

    test_name = f"test-create-duplicate-{uuid.uuid4()}"

    # First creation should succeed
    task_id_1 = await schedule_create(health_pool, test_name, "0 * * * *", "First creation")
    assert task_id_1 is not None

    # Second creation with same name should raise ValueError
    with pytest.raises(ValueError, match="already exists"):
        await schedule_create(health_pool, test_name, "0 * * * *", "Second creation")

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id_1)


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

    # Verify both sessions were created
    final_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")
    # Should have at least 2 new sessions (external + scheduled)
    assert final_count >= initial_count + 2, (
        f"Expected at least 2 new sessions, got {final_count - initial_count}"
    )

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE name = $1", task_name)


# ---------------------------------------------------------------------------
# Disabled Schedule Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_schedule_skipped(health_pool: Pool) -> None:
    """Schedule with enabled=false should not be triggered by tick.

    Validates that disabled schedules are skipped during tick evaluation.
    """
    # Create a disabled scheduled task with next_run_at in the past
    now = datetime.now(UTC)
    past = now - timedelta(hours=1)
    task_name = f"test-disabled-{uuid.uuid4()}"

    async with health_pool.acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, '0 * * * *', 'Disabled task should not run', 'db', false, $2)
            RETURNING id
            """,
            task_name,
            past,
        )

    # Get initial session count
    initial_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")

    # Access health daemon spawner
    import inspect

    frame = inspect.currentframe()
    health_daemon = None
    while frame:
        if "butler_ecosystem" in frame.f_locals:
            ecosystem = frame.f_locals["butler_ecosystem"]
            health_daemon = ecosystem.butlers.get("health")
            break
        frame = frame.f_back

    if health_daemon is None:
        pytest.skip("Could not access health butler daemon for disabled schedule test")

    spawner = health_daemon.spawner

    # Run tick
    dispatched = await _tick(health_pool, spawner.trigger)

    # Give spawner time to complete any sessions
    await asyncio.sleep(1)

    # Verify the disabled task was NOT dispatched
    # Check that the task's last_run_at is still NULL
    async with health_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_run_at FROM scheduled_tasks WHERE id = $1", task_id)
        assert row is not None
        assert row["last_run_at"] is None, "Disabled task should not have been dispatched"

    # Verify session count did not increase (or if it did, it was from other tasks)
    final_count = await health_pool.fetchval("SELECT COUNT(*) FROM sessions")
    # If dispatched is 0, no new sessions should exist
    if dispatched == 0:
        assert final_count == initial_count, "No new sessions should be created for disabled tasks"

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)


# ---------------------------------------------------------------------------
# Session Metadata Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_task_session_metadata(health_pool: Pool) -> None:
    """Scheduled task sessions should have correct trigger_source metadata.

    Validates that sessions created by scheduled tasks have trigger_source
    in the format "schedule:<task-name>".
    """
    # Create a scheduled task with next_run_at in the past
    now = datetime.now(UTC)
    past = now - timedelta(minutes=1)
    task_name = f"test-metadata-{uuid.uuid4()}"

    async with health_pool.acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, '* * * * *', 'Test session metadata', 'db', true, $2)
            RETURNING id
            """,
            task_name,
            past,
        )

    # Access health daemon spawner
    import inspect

    frame = inspect.currentframe()
    health_daemon = None
    while frame:
        if "butler_ecosystem" in frame.f_locals:
            ecosystem = frame.f_locals["butler_ecosystem"]
            health_daemon = ecosystem.butlers.get("health")
            break
        frame = frame.f_back

    if health_daemon is None:
        pytest.skip("Could not access health butler daemon for metadata test")

    spawner = health_daemon.spawner

    # Run tick to dispatch the task
    dispatched = await _tick(health_pool, spawner.trigger)
    assert dispatched >= 1, "Task should have been dispatched"

    # Give spawner time to complete
    await asyncio.sleep(2)

    # Query the most recent session
    async with health_pool.acquire() as conn:
        session = await conn.fetchrow(
            """
            SELECT trigger_source, duration_ms, model
            FROM sessions
            ORDER BY created_at DESC LIMIT 1
            """
        )

    assert session is not None, "Session should have been created"
    assert session["trigger_source"] == f"schedule:{task_name}", (
        f"Expected trigger_source='schedule:{task_name}', got '{session['trigger_source']}'"
    )
    assert session["duration_ms"] is not None, "Session should have duration_ms"
    assert session["duration_ms"] >= 0, "Duration should be non-negative"

    # Cleanup
    await health_pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)
