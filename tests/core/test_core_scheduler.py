"""Tests for butlers.core.scheduler — cron-driven task scheduler with TOML sync.

Covers:
- sync_schedules: insert, update (cron/prompt/mode), disable removed, re-enable restored
- tick: dispatch due prompt/job tasks, skip disabled, continue on failure, update timestamps
- schedule_create / update / delete: CRUD, validation, complexity, calendar fields
- schedule_list: field presence contract
- until_at: auto-disable when exceeded
- deadline task type: create with required fields
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


_SCHEDULED_TASKS_DDL = """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT UNIQUE NOT NULL,
        cron TEXT NOT NULL,
        prompt TEXT,
        dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
        job_name TEXT,
        job_args JSONB,
        complexity TEXT DEFAULT 'medium',
        timezone TEXT NOT NULL DEFAULT 'UTC',
        start_at TIMESTAMPTZ,
        end_at TIMESTAMPTZ,
        until_at TIMESTAMPTZ,
        display_title TEXT,
        calendar_event_id TEXT,
        source TEXT NOT NULL DEFAULT 'db',
        enabled BOOLEAN NOT NULL DEFAULT true,
        next_run_at TIMESTAMPTZ,
        last_run_at TIMESTAMPTZ,
        last_result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        task_type TEXT DEFAULT 'cron',
        target_date DATE,
        lead_time_days INTEGER,
        alert_thresholds JSONB,
        deadline_status TEXT,
        fired_thresholds JSONB,
        depends_on JSONB,
        CONSTRAINT scheduled_tasks_dispatch_mode_check
            CHECK (dispatch_mode IN ('prompt', 'job')),
        CONSTRAINT scheduled_tasks_dispatch_payload_check
            CHECK (
                (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
            ),
        CONSTRAINT scheduled_tasks_window_bounds_check
            CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at),
        CONSTRAINT scheduled_tasks_until_bounds_check
            CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)
    )
"""

_CALENDAR_INDEX_DDL = """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_calendar_event_id
    ON scheduled_tasks (calendar_event_id)
    WHERE calendar_event_id IS NOT NULL
"""


async def _make_pool(postgres_container) -> asyncpg.Pool:
    db_name = _unique_db_name()
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    p = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    await p.execute(_SCHEDULED_TASKS_DDL)
    await p.execute(_CALENDAR_INDEX_DDL)
    return p


@pytest.fixture
async def pool(postgres_container):
    p = await _make_pool(postgres_container)
    yield p
    await p.close()


class _Dispatch:
    def __init__(self, *, fail_on: set[str] | None = None, result=None):
        self.calls: list[dict] = []
        self._fail_on = fail_on or set()
        self._result = result

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        target = kwargs.get("prompt") or kwargs.get("job_name")
        if target in self._fail_on:
            raise RuntimeError(f"Simulated failure for: {target}")
        return self._result


def _past(minutes: int = 5) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# sync_schedules
# ---------------------------------------------------------------------------


async def test_sync_inserts_and_sets_next_run_at(pool):
    """sync_schedules inserts toml tasks with source=toml and non-null next_run_at."""
    from butlers.core.scheduler import sync_schedules

    schedules = [
        {"name": "daily-report", "cron": "0 9 * * *", "prompt": "Generate daily report"},
        {"name": "weekly-digest", "cron": "0 8 * * 1", "prompt": "Send weekly digest"},
    ]
    await sync_schedules(pool, schedules)

    rows = await pool.fetch(
        "SELECT name, source, enabled, next_run_at FROM scheduled_tasks ORDER BY name"
    )
    assert len(rows) == 2
    assert all(r["source"] == "toml" for r in rows)
    assert all(r["enabled"] for r in rows)
    assert all(r["next_run_at"] is not None for r in rows)


async def test_sync_updates_changed_fields_and_disables_removed(pool):
    """sync_schedules updates changed cron/prompt, disables removed tasks, re-enables restored."""
    from butlers.core.scheduler import sync_schedules

    base = [
        {"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"},
        {"name": "change-me", "cron": "0 9 * * *", "prompt": "old prompt"},
        {"name": "drop-me", "cron": "0 10 * * *", "prompt": "drop"},
    ]
    await sync_schedules(pool, base)

    # Update change-me, drop drop-me
    updated = [
        {"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"},
        {"name": "change-me", "cron": "0 10 * * *", "prompt": "new prompt"},
    ]
    await sync_schedules(pool, updated)

    all_rows = await pool.fetch("SELECT name, cron, prompt, enabled FROM scheduled_tasks")
    rows = {r["name"]: r for r in all_rows}
    assert rows["change-me"]["cron"] == "0 10 * * *"
    assert rows["change-me"]["prompt"] == "new prompt"
    assert rows["drop-me"]["enabled"] is False  # soft-disabled, not deleted

    # Re-sync with drop-me → re-enables
    await sync_schedules(pool, base)
    all_rows2 = await pool.fetch("SELECT name, enabled FROM scheduled_tasks")
    rows2 = {r["name"]: r for r in all_rows2}
    assert rows2["drop-me"]["enabled"] is True


# ---------------------------------------------------------------------------
# tick — prompt and job dispatch
# ---------------------------------------------------------------------------


async def test_tick_dispatches_due_prompt_task(pool):
    """tick() dispatches overdue prompt tasks with correct trigger_source."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "due-task", "*/1 * * * *", "run this")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", task_id, _past()
    )

    dispatch = _Dispatch()
    count = await tick(pool, dispatch)

    assert count == 1
    assert dispatch.calls[0]["prompt"] == "run this"
    assert dispatch.calls[0]["trigger_source"] == "schedule:due-task"


async def test_tick_dispatches_job_mode_task(pool):
    """tick() dispatches job-mode tasks via job_name/job_args without complexity."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(
        pool,
        "due-job",
        "*/1 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 25},
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", task_id, _past()
    )

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    assert dispatch.calls[0]["job_name"] == "eligibility_sweep"
    assert dispatch.calls[0]["job_args"] == {"batch_size": 25}
    assert "prompt" not in dispatch.calls[0]
    assert "complexity" not in dispatch.calls[0]


async def test_tick_skips_disabled_and_continues_on_failure(pool):
    """tick() skips disabled tasks and continues when dispatch raises."""
    from butlers.core.scheduler import schedule_create, tick

    # Disabled task
    t1 = await schedule_create(pool, "disabled-task", "*/1 * * * *", "skip me")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2, enabled = false WHERE id = $1", t1, _past()
    )
    # Task that fails
    t2 = await schedule_create(pool, "fail-task", "*/1 * * * *", "I will fail")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t2, _past())

    dispatch = _Dispatch(fail_on={"I will fail"})
    count = await tick(pool, dispatch)

    # disabled task not dispatched, failing task attempted but doesn't count as success
    assert count == 0
    assert len(dispatch.calls) == 1  # only the fail-task was attempted


async def test_tick_updates_timestamps_and_advances_next_run_at(pool):
    """tick() sets last_run_at and advances next_run_at into the future."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "advance-task", "*/5 * * * *", "advance")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", task_id, _past(10)
    )

    await tick(pool, _Dispatch())

    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)
    assert row["last_run_at"] is not None


async def test_tick_auto_disables_when_until_at_exceeded(pool):
    """tick() disables a task when next_run_at would exceed until_at."""
    from butlers.core.scheduler import schedule_create, tick

    past_until = datetime.now(UTC) - timedelta(hours=1)
    task_id = await schedule_create(
        pool,
        "until-task",
        "*/1 * * * *",
        "expiring",
        until_at=past_until,
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", task_id, _past()
    )

    await tick(pool, _Dispatch())

    row = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["enabled"] is False


# ---------------------------------------------------------------------------
# schedule_create / update / delete
# ---------------------------------------------------------------------------


async def test_schedule_create_and_list(pool):
    """schedule_create persists task; schedule_list returns it with required fields."""
    from butlers.core.scheduler import schedule_create, schedule_list

    task_id = await schedule_create(
        pool,
        "list-task",
        "0 9 * * *",
        "list me",
        complexity="high",
        timezone="America/New_York",
        display_title="List Task",
    )
    assert task_id is not None

    tasks = await schedule_list(pool)
    task = next((t for t in tasks if t["name"] == "list-task"), None)
    assert task is not None
    assert task["complexity"] == "high"
    assert task["timezone"] == "America/New_York"
    assert task["dispatch_mode"] == "prompt"
    assert task["last_result"] is None


async def test_schedule_create_job_mode_and_list(pool):
    """schedule_create with job mode; schedule_list returns job fields."""
    from butlers.core.scheduler import schedule_create, schedule_list

    await schedule_create(
        pool,
        "job-list-task",
        "*/10 * * * *",
        dispatch_mode="job",
        job_name="my_job",
        job_args={"dry_run": True},
    )

    tasks = await schedule_list(pool)
    task = next(t for t in tasks if t["name"] == "job-list-task")
    assert task["dispatch_mode"] == "job"
    assert task["job_name"] == "my_job"
    assert task["prompt"] is None


async def test_schedule_create_invalid_cron_raises(pool):
    """schedule_create raises ValueError for invalid cron expressions."""
    from butlers.core.scheduler import schedule_create

    with pytest.raises((ValueError, Exception)):
        await schedule_create(pool, "bad-cron", "not-a-cron", "test")


async def test_schedule_create_duplicate_name_raises(pool):
    """schedule_create raises when task name already exists."""
    from butlers.core.scheduler import schedule_create

    await schedule_create(pool, "dup-name", "0 9 * * *", "first")
    with pytest.raises(Exception):
        await schedule_create(pool, "dup-name", "0 10 * * *", "second")


async def test_schedule_update_and_delete(pool):
    """schedule_update changes fields; schedule_delete removes runtime tasks."""
    from butlers.core.scheduler import schedule_create, schedule_delete, schedule_update

    task_id = await schedule_create(pool, "updatable", "0 9 * * *", "original")

    await schedule_update(pool, task_id, cron="0 10 * * *", prompt="updated")
    row = await pool.fetchrow("SELECT cron, prompt FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["cron"] == "0 10 * * *"
    assert row["prompt"] == "updated"

    await schedule_delete(pool, task_id)
    assert await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE id = $1", task_id) is None


async def test_schedule_update_invalid_cron_raises(pool):
    """schedule_update raises for invalid cron; nonexistent ID raises."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "cron-update-bad", "0 9 * * *", "prompt")
    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, task_id, cron="bad-cron")

    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, uuid.uuid4(), prompt="does not exist")


async def test_schedule_complexity_validation(pool):
    """schedule_create/update enforce valid complexity values."""
    from butlers.core.scheduler import schedule_create, schedule_update

    # Invalid on create
    with pytest.raises(ValueError, match="complexity"):
        await schedule_create(pool, "bad-complexity", "0 9 * * *", "work", complexity="ultra")

    # Valid values accepted
    task_id = await schedule_create(
        pool, "good-complexity", "0 9 * * *", "work", complexity="extra_high"
    )
    row = await pool.fetchrow("SELECT complexity FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["complexity"] == "extra_high"

    # Invalid on update
    with pytest.raises(ValueError, match="complexity"):
        await schedule_update(pool, task_id, complexity="super_high")


# ---------------------------------------------------------------------------
# Deadline task type
# ---------------------------------------------------------------------------


async def test_deadline_task_create(pool):
    """schedule_create with task_type=deadline requires target_date and alert_thresholds."""
    import datetime as _dt

    from butlers.core.scheduler import schedule_create

    future_date = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=60)).date()
    task_id = await schedule_create(
        pool,
        "deadline-task",
        "0 9 * * *",
        "deadline prompt",
        task_type="deadline",
        target_date=future_date,
        lead_time_days=45,
        alert_thresholds=[
            {"days_before": 30, "severity": "info"},
            {"days_before": 14, "severity": "warning"},
            {"days_before": 7, "severity": "warning"},
            {"days_before": 1, "severity": "critical"},
        ],
    )
    assert task_id is not None

    # Missing required fields should raise
    with pytest.raises((ValueError, Exception)):
        await schedule_create(
            pool,
            "bad-deadline",
            "0 9 * * *",
            "missing",
            task_type="deadline",
        )
