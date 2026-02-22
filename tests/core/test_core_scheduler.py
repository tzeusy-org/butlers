"""Tests for butlers.core.scheduler — cron-driven task scheduler with TOML sync."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so the pool (created in the session
    # fixture loop via asyncio_default_fixture_loop_scope=session) is usable.
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


# Use the session-scoped postgres_container from root conftest (not a local override)
# so the event loop is shared across the whole session, avoiding asyncpg loop mismatch.


@pytest.fixture
async def pool(postgres_container):
    """Create a fresh database with the scheduled_tasks table and return a pool."""
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

    await p.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT UNIQUE NOT NULL,
            cron TEXT NOT NULL,
            prompt TEXT,
            dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
            job_name TEXT,
            job_args JSONB,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            until_at TIMESTAMPTZ,
            display_title TEXT,
            calendar_event_id UUID,
            source TEXT NOT NULL DEFAULT 'db',
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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
    """)
    await p.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_calendar_event_id
        ON scheduled_tasks (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )

    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Dispatch:
    """Captures dispatch calls for assertions."""

    def __init__(self, *, fail_on: set[str] | None = None, result=None):
        self.calls: list[dict] = []
        self._fail_on = fail_on or set()
        self._result = result

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        dispatch_target = kwargs.get("prompt") or kwargs.get("job_name")
        if dispatch_target in self._fail_on:
            raise RuntimeError(f"Simulated failure for: {dispatch_target}")
        return self._result


def _decode_json_field(value):
    """Decode JSONB fields that asyncpg may return as JSON strings."""
    if isinstance(value, str):
        return json.loads(value)
    return value


# ---------------------------------------------------------------------------
# sync_schedules — first run inserts all
# ---------------------------------------------------------------------------


async def test_sync_inserts_new_tasks(pool):
    """First sync run inserts all TOML schedules as new tasks."""
    from butlers.core.scheduler import sync_schedules

    schedules = [
        {"name": "daily-report", "cron": "0 9 * * *", "prompt": "Generate daily report"},
        {"name": "weekly-digest", "cron": "0 8 * * 1", "prompt": "Send weekly digest"},
    ]
    await sync_schedules(pool, schedules)

    rows = await pool.fetch(
        "SELECT name, cron, prompt, source, enabled FROM scheduled_tasks ORDER BY name"
    )
    assert len(rows) == 2
    assert rows[0]["name"] == "daily-report"
    assert rows[0]["source"] == "toml"
    assert rows[0]["enabled"] is True
    assert rows[1]["name"] == "weekly-digest"
    assert rows[1]["cron"] == "0 8 * * 1"


async def test_sync_sets_next_run_at(pool):
    """Synced tasks have a non-null next_run_at computed via croniter."""
    from butlers.core.scheduler import sync_schedules

    schedules = [
        {"name": "check-nra", "cron": "*/5 * * * *", "prompt": "check"},
    ]
    await sync_schedules(pool, schedules)

    row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE name = 'check-nra'")
    assert row is not None
    assert row["next_run_at"] is not None
    # next_run_at should be in the future
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)


# ---------------------------------------------------------------------------
# sync_schedules — update run updates changed
# ---------------------------------------------------------------------------


async def test_sync_updates_changed_cron(pool):
    """Second sync with changed cron expression updates the existing task."""
    from butlers.core.scheduler import sync_schedules

    original = [{"name": "updatable", "cron": "0 9 * * *", "prompt": "hello"}]
    await sync_schedules(pool, original)

    updated = [{"name": "updatable", "cron": "0 10 * * *", "prompt": "hello"}]
    await sync_schedules(pool, updated)

    row = await pool.fetchrow(
        "SELECT cron FROM scheduled_tasks WHERE name = 'updatable' AND source = 'toml'"
    )
    assert row["cron"] == "0 10 * * *"


async def test_sync_updates_changed_prompt(pool):
    """Second sync with changed prompt updates the existing task."""
    from butlers.core.scheduler import sync_schedules

    original = [{"name": "prompt-change", "cron": "0 9 * * *", "prompt": "old prompt"}]
    await sync_schedules(pool, original)

    updated = [{"name": "prompt-change", "cron": "0 9 * * *", "prompt": "new prompt"}]
    await sync_schedules(pool, updated)

    row = await pool.fetchrow(
        "SELECT prompt FROM scheduled_tasks WHERE name = 'prompt-change' AND source = 'toml'"
    )
    assert row["prompt"] == "new prompt"


async def test_sync_updates_changed_dispatch_payload(pool):
    """Second sync can migrate an existing TOML task between prompt and job modes."""
    from butlers.core.scheduler import sync_schedules

    original = [
        {
            "name": "dispatch-mode-change",
            "cron": "0 9 * * *",
            "prompt": "old prompt mode task",
        }
    ]
    await sync_schedules(pool, original)

    updated = [
        {
            "name": "dispatch-mode-change",
            "cron": "*/5 * * * *",
            "dispatch_mode": "job",
            "job_name": "eligibility_sweep",
            "job_args": {"dry_run": True},
        }
    ]
    await sync_schedules(pool, updated)

    row = await pool.fetchrow(
        """
        SELECT cron, dispatch_mode, prompt, job_name, job_args
        FROM scheduled_tasks
        WHERE name = 'dispatch-mode-change' AND source = 'toml'
        """
    )
    assert row["cron"] == "*/5 * * * *"
    assert row["dispatch_mode"] == "job"
    assert row["prompt"] is None
    assert row["job_name"] == "eligibility_sweep"
    assert _decode_json_field(row["job_args"]) == {"dry_run": True}


# ---------------------------------------------------------------------------
# sync_schedules — removal disables
# ---------------------------------------------------------------------------


async def test_sync_disables_removed_tasks(pool):
    """Tasks removed from TOML are disabled, not deleted."""
    from butlers.core.scheduler import sync_schedules

    schedules = [
        {"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"},
        {"name": "drop-me", "cron": "0 10 * * *", "prompt": "drop"},
    ]
    await sync_schedules(pool, schedules)

    # Re-sync without "drop-me"
    await sync_schedules(pool, [{"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"}])

    row = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE name = 'drop-me'")
    assert row is not None  # Not deleted
    assert row["enabled"] is False


async def test_sync_re_enables_restored_task(pool):
    """A previously disabled TOML task is re-enabled when it reappears in config."""
    from butlers.core.scheduler import sync_schedules

    schedules = [{"name": "toggle-me", "cron": "0 9 * * *", "prompt": "toggle"}]
    await sync_schedules(pool, schedules)

    # Remove it
    await sync_schedules(pool, [])
    row = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE name = 'toggle-me'")
    assert row["enabled"] is False

    # Restore it
    await sync_schedules(pool, schedules)
    row = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE name = 'toggle-me'")
    assert row["enabled"] is True


# ---------------------------------------------------------------------------
# tick — dispatches due tasks
# ---------------------------------------------------------------------------


async def test_tick_dispatches_due_tasks(pool):
    """tick() dispatches tasks whose next_run_at is in the past."""
    from butlers.core.scheduler import schedule_create, tick

    # Create a task with next_run_at in the past by setting it directly
    task_id = await schedule_create(pool, "due-task", "*/1 * * * *", "run this")
    # Force next_run_at to the past
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    dispatch = _Dispatch()
    count = await tick(pool, dispatch)

    assert count == 1
    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["prompt"] == "run this"
    assert dispatch.calls[0]["trigger_source"] == "schedule:due-task"


async def test_tick_dispatches_job_mode_tasks(pool):
    """tick() dispatches job-mode tasks through the deterministic job call path."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(
        pool,
        "due-job-task",
        "*/1 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 25},
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    dispatch = _Dispatch()
    count = await tick(pool, dispatch)

    assert count == 1
    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["job_name"] == "eligibility_sweep"
    assert dispatch.calls[0]["job_args"] == {"batch_size": 25}
    assert dispatch.calls[0]["trigger_source"] == "schedule:due-job-task"
    assert "prompt" not in dispatch.calls[0]


async def test_tick_noop_when_nothing_due(pool):
    """tick() returns 0 when no tasks are due."""
    from butlers.core.scheduler import schedule_create, tick

    # Create a task with next_run_at far in the future (default from _next_run)
    await schedule_create(pool, "future-task", "0 0 1 1 *", "future prompt")

    dispatch = _Dispatch()
    count = await tick(pool, dispatch)

    assert count == 0
    assert len(dispatch.calls) == 0


async def test_tick_skips_disabled_tasks(pool):
    """tick() does not dispatch disabled tasks even if they are due."""
    from butlers.core.scheduler import schedule_create, schedule_update, tick

    task_id = await schedule_create(pool, "disabled-task", "*/1 * * * *", "skip me")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )
    await schedule_update(pool, task_id, enabled=False)

    dispatch = _Dispatch()
    count = await tick(pool, dispatch)

    assert count == 0
    assert len(dispatch.calls) == 0


async def test_tick_continues_on_dispatch_failure(pool):
    """tick() continues to next task if dispatch_fn raises for one task."""
    from butlers.core.scheduler import schedule_create, tick

    # Create two due tasks
    id1 = await schedule_create(pool, "fail-task", "*/1 * * * *", "I will fail")
    id2 = await schedule_create(pool, "ok-task", "*/1 * * * *", "I will succeed")
    for tid in (id1, id2):
        await pool.execute(
            "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
            tid,
            datetime.now(UTC) - timedelta(minutes=5),
        )

    dispatch = _Dispatch(fail_on={"I will fail"})
    count = await tick(pool, dispatch)

    # Only the successful one counts
    assert count == 1
    # Both were attempted
    assert len(dispatch.calls) == 2


async def test_tick_updates_next_run_at_and_last_run_at(pool):
    """After tick(), next_run_at is advanced and last_run_at is set."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "advance-task", "*/5 * * * *", "advance")
    past = datetime.now(UTC) - timedelta(minutes=10)
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        past,
    )

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    # next_run_at should now be in the future
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)
    # last_run_at should be set
    assert row["last_run_at"] is not None


# ---------------------------------------------------------------------------
# tick — last_result
# ---------------------------------------------------------------------------


async def test_tick_writes_last_result_after_dispatch(pool):
    """tick() stores the dispatch_fn return value in last_result after dispatch."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "result-task", "*/1 * * * *", "get result")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    # dispatch_fn returns a dict-like result
    dispatch = _Dispatch(result={"result": "All good", "tool_calls": [], "duration_ms": 42})
    await tick(pool, dispatch)

    row = await pool.fetchrow(
        "SELECT last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["last_result"] is not None
    result_data = json.loads(row["last_result"])
    assert result_data["result"] == "All good"
    assert result_data["duration_ms"] == 42


async def test_tick_writes_last_result_with_dataclass(pool):
    """tick() stores a SpawnerResult-like dataclass in last_result."""
    from dataclasses import dataclass, field

    from butlers.core.scheduler import schedule_create, tick

    @dataclass
    class FakeSpawnerResult:
        result: str | None = None
        tool_calls: list = field(default_factory=list)
        error: str | None = None
        duration_ms: int = 0

    task_id = await schedule_create(pool, "dataclass-task", "*/1 * * * *", "dataclass test")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    dispatch = _Dispatch(result=FakeSpawnerResult(result="done", duration_ms=100))
    await tick(pool, dispatch)

    row = await pool.fetchrow(
        "SELECT last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["last_result"] is not None
    result_data = json.loads(row["last_result"])
    assert result_data["result"] == "done"
    assert result_data["duration_ms"] == 100


async def test_tick_writes_error_to_last_result_on_failure(pool):
    """tick() stores error info in last_result when dispatch fails."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "error-result-task", "*/1 * * * *", "I will fail")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    dispatch = _Dispatch(fail_on={"I will fail"})
    await tick(pool, dispatch)

    row = await pool.fetchrow(
        "SELECT last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["last_result"] is not None
    result_data = json.loads(row["last_result"])
    assert "error" in result_data
    assert "Simulated failure" in result_data["error"]


async def test_tick_writes_error_to_last_result_on_job_failure(pool):
    """tick() stores deterministic job dispatch errors in last_result."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(
        pool,
        "error-job-task",
        "*/1 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    dispatch = _Dispatch(fail_on={"eligibility_sweep"})
    await tick(pool, dispatch)

    row = await pool.fetchrow(
        "SELECT last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["last_result"] is not None
    result_data = json.loads(row["last_result"])
    assert "error" in result_data
    assert "Simulated failure for: eligibility_sweep" in result_data["error"]


async def test_last_result_null_for_new_tasks(pool):
    """Newly created tasks have last_result as NULL."""
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(pool, "new-task-null", "0 9 * * *", "new task")
    row = await pool.fetchrow(
        "SELECT last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["last_result"] is None


# ---------------------------------------------------------------------------
# schedule_list — includes last_result
# ---------------------------------------------------------------------------


async def test_schedule_list_includes_last_result(pool):
    """schedule_list includes last_result in its output."""
    from butlers.core.scheduler import schedule_create, schedule_list, tick

    task_id = await schedule_create(pool, "list-result-task", "*/1 * * * *", "list me")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )

    # Dispatch to populate last_result
    dispatch = _Dispatch(result={"status": "ok"})
    await tick(pool, dispatch)

    tasks = await schedule_list(pool)
    task = next(t for t in tasks if t["name"] == "list-result-task")
    assert "last_result" in task
    result_data = json.loads(task["last_result"])
    assert result_data["status"] == "ok"


async def test_schedule_list_last_result_null_for_unrun_task(pool):
    """schedule_list returns last_result=None for tasks that have never been dispatched."""
    from butlers.core.scheduler import schedule_create, schedule_list

    await schedule_create(pool, "unrun-task", "0 9 * * *", "never ran")

    tasks = await schedule_list(pool)
    task = next(t for t in tasks if t["name"] == "unrun-task")
    assert "last_result" in task
    assert task["last_result"] is None


async def test_schedule_list_includes_dispatch_fields(pool):
    """schedule_list includes dispatch mode and deterministic job metadata."""
    from butlers.core.scheduler import schedule_create, schedule_list

    await schedule_create(pool, "prompt-list-task", "0 9 * * *", "prompt task")
    await schedule_create(
        pool,
        "job-list-task",
        "*/10 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"dry_run": True},
    )

    tasks = await schedule_list(pool)
    prompt_task = next(t for t in tasks if t["name"] == "prompt-list-task")
    job_task = next(t for t in tasks if t["name"] == "job-list-task")

    assert prompt_task["dispatch_mode"] == "prompt"
    assert prompt_task["job_name"] is None
    assert prompt_task["job_args"] is None
    assert job_task["dispatch_mode"] == "job"
    assert job_task["prompt"] is None
    assert job_task["job_name"] == "eligibility_sweep"
    assert job_task["job_args"] == {"dry_run": True}


async def test_schedule_list_includes_projection_linkage_fields(pool):
    """schedule_list returns timezone/window/linkage metadata."""
    from butlers.core.scheduler import schedule_create, schedule_list

    calendar_event_id = uuid.uuid4()
    start_at = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    end_at = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
    until_at = datetime(2026, 4, 1, 14, 0, tzinfo=UTC)
    await schedule_create(
        pool,
        "projection-list-task",
        "0 9 * * *",
        "projection list",
        timezone="America/New_York",
        start_at=start_at,
        end_at=end_at,
        until_at=until_at,
        display_title="Projection List",
        calendar_event_id=calendar_event_id,
    )

    tasks = await schedule_list(pool)
    task = next(t for t in tasks if t["name"] == "projection-list-task")
    assert task["timezone"] == "America/New_York"
    assert task["start_at"] == start_at
    assert task["end_at"] == end_at
    assert task["until_at"] == until_at
    assert task["display_title"] == "Projection List"
    assert task["calendar_event_id"] == calendar_event_id


# ---------------------------------------------------------------------------
# next_run_at computed correctly via croniter
# ---------------------------------------------------------------------------


async def test_next_run_at_is_future(pool):
    """Newly created task's next_run_at is in the future."""
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(pool, "cron-check", "0 12 * * *", "noon task")
    row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)


# ---------------------------------------------------------------------------
# CRUD — schedule_list
# ---------------------------------------------------------------------------


async def test_schedule_list_returns_all(pool):
    """schedule_list returns all tasks ordered by name."""
    from butlers.core.scheduler import schedule_create, schedule_list

    await schedule_create(pool, "zz-last", "0 9 * * *", "last")
    await schedule_create(pool, "aa-first", "0 9 * * *", "first")

    tasks = await schedule_list(pool)
    names = [t["name"] for t in tasks]
    assert "aa-first" in names
    assert "zz-last" in names
    # Check ordering
    assert names.index("aa-first") < names.index("zz-last")


# ---------------------------------------------------------------------------
# CRUD — schedule_create
# ---------------------------------------------------------------------------


async def test_create_returns_uuid(pool):
    """schedule_create returns a valid UUID."""
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(pool, "uuid-task", "*/10 * * * *", "test")
    assert isinstance(task_id, uuid.UUID)


async def test_create_sets_source_db(pool):
    """Runtime-created tasks have source='db'."""
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(pool, "runtime-src", "0 9 * * *", "test")
    row = await pool.fetchrow("SELECT source FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["source"] == "db"


async def test_create_invalid_cron_raises(pool):
    """schedule_create raises ValueError for an invalid cron expression."""
    from butlers.core.scheduler import schedule_create

    with pytest.raises(ValueError, match="Invalid cron"):
        await schedule_create(pool, "bad-cron", "not a cron", "test")


async def test_create_job_mode_persists_dispatch_metadata(pool):
    """schedule_create supports deterministic job-mode payloads."""
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(
        pool,
        "runtime-job",
        "*/10 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 50},
    )

    row = await pool.fetchrow(
        "SELECT dispatch_mode, prompt, job_name, job_args FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["dispatch_mode"] == "job"
    assert row["prompt"] is None
    assert row["job_name"] == "eligibility_sweep"
    assert _decode_json_field(row["job_args"]) == {"batch_size": 50}


async def test_create_job_mode_requires_job_name(pool):
    """Job-mode schedule_create requires job_name."""
    from butlers.core.scheduler import schedule_create

    with pytest.raises(ValueError, match="requires non-empty job_name"):
        await schedule_create(pool, "missing-job-name", "*/10 * * * *", dispatch_mode="job")


async def test_create_persists_calendar_projection_fields(pool):
    """schedule_create stores optional calendar linkage fields."""
    from butlers.core.scheduler import schedule_create

    start_at = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    end_at = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
    until_at = datetime(2026, 4, 1, 14, 0, tzinfo=UTC)
    calendar_event_id = uuid.uuid4()
    task_id = await schedule_create(
        pool,
        "projection-create",
        "0 9 * * *",
        "projection create",
        timezone="America/New_York",
        start_at=start_at,
        end_at=end_at,
        until_at=until_at,
        display_title="Projection Create",
        calendar_event_id=calendar_event_id,
    )

    row = await pool.fetchrow(
        """
        SELECT timezone, start_at, end_at, until_at, display_title, calendar_event_id
        FROM scheduled_tasks
        WHERE id = $1
        """,
        task_id,
    )
    assert row["timezone"] == "America/New_York"
    assert row["start_at"] == start_at
    assert row["end_at"] == end_at
    assert row["until_at"] == until_at
    assert row["display_title"] == "Projection Create"
    assert row["calendar_event_id"] == calendar_event_id


# ---------------------------------------------------------------------------
# CRUD — schedule_update
# ---------------------------------------------------------------------------


async def test_update_name(pool):
    """schedule_update can change the task name."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "old-name", "0 9 * * *", "test")
    await schedule_update(pool, task_id, name="new-name")

    row = await pool.fetchrow("SELECT name FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["name"] == "new-name"


async def test_update_cron_recomputes_next_run(pool):
    """Updating cron recomputes next_run_at."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "recron", "0 0 1 1 *", "rare")
    old_row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task_id)

    await schedule_update(pool, task_id, cron="*/1 * * * *")
    new_row = await pool.fetchrow(
        "SELECT next_run_at, cron FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert new_row["cron"] == "*/1 * * * *"
    # The new next_run_at should differ from the old one (much sooner)
    assert new_row["next_run_at"] != old_row["next_run_at"]


async def test_update_nonexistent_raises(pool):
    """schedule_update raises ValueError for a nonexistent task ID."""
    from butlers.core.scheduler import schedule_update

    with pytest.raises(ValueError, match="not found"):
        await schedule_update(pool, uuid.uuid4(), name="ghost")


async def test_update_invalid_cron_raises(pool):
    """schedule_update raises ValueError for an invalid cron expression."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "cron-upd-fail", "0 9 * * *", "test")
    with pytest.raises(ValueError, match="Invalid cron"):
        await schedule_update(pool, task_id, cron="bad cron")


async def test_update_prompt_to_job_mode(pool):
    """schedule_update supports transitioning a task from prompt mode to job mode."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "mode-flip-task", "0 9 * * *", "old prompt")
    await schedule_update(
        pool,
        task_id,
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"dry_run": True},
    )

    row = await pool.fetchrow(
        "SELECT dispatch_mode, prompt, job_name, job_args FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["dispatch_mode"] == "job"
    assert row["prompt"] is None
    assert row["job_name"] == "eligibility_sweep"
    assert _decode_json_field(row["job_args"]) == {"dry_run": True}


async def test_update_job_to_prompt_mode(pool):
    """schedule_update supports transitioning a task from job mode to prompt mode."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(
        pool,
        "mode-flip-back-task",
        "0 9 * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
    )
    await schedule_update(
        pool,
        task_id,
        dispatch_mode="prompt",
        prompt="new prompt mode payload",
    )

    row = await pool.fetchrow(
        "SELECT dispatch_mode, prompt, job_name, job_args FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert row["dispatch_mode"] == "prompt"
    assert row["prompt"] == "new prompt mode payload"
    assert row["job_name"] is None
    assert row["job_args"] is None


async def test_update_rejects_job_name_for_prompt_mode(pool):
    """schedule_update rejects deterministic job metadata on prompt-mode tasks."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "invalid-update-task", "0 9 * * *", "prompt")
    with pytest.raises(ValueError, match="job_name is only valid"):
        await schedule_update(pool, task_id, job_name="eligibility_sweep")


async def test_update_calendar_projection_fields(pool):
    """schedule_update mutates calendar linkage fields."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "projection-update", "0 9 * * *", "projection update")
    start_at = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    end_at = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    until_at = datetime(2026, 4, 2, 14, 0, tzinfo=UTC)
    calendar_event_id = uuid.uuid4()
    await schedule_update(
        pool,
        task_id,
        timezone="America/Chicago",
        start_at=start_at,
        end_at=end_at,
        until_at=until_at,
        display_title="Projection Update",
        calendar_event_id=calendar_event_id,
    )

    row = await pool.fetchrow(
        """
        SELECT timezone, start_at, end_at, until_at, display_title, calendar_event_id
        FROM scheduled_tasks
        WHERE id = $1
        """,
        task_id,
    )
    assert row["timezone"] == "America/Chicago"
    assert row["start_at"] == start_at
    assert row["end_at"] == end_at
    assert row["until_at"] == until_at
    assert row["display_title"] == "Projection Update"
    assert row["calendar_event_id"] == calendar_event_id


async def test_update_projection_window_checks_existing_values(pool):
    """schedule_update validates projection windows against existing row values."""
    from butlers.core.scheduler import schedule_create, schedule_update

    start_at = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    end_at = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    task_id = await schedule_create(
        pool,
        "projection-window-check",
        "0 9 * * *",
        "projection window",
        timezone="UTC",
        start_at=start_at,
        end_at=end_at,
    )

    with pytest.raises(ValueError, match="schedule_update.end_at must be after start_at"):
        await schedule_update(
            pool,
            task_id,
            start_at=datetime(2026, 3, 2, 16, 0, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# CRUD — schedule_delete
# ---------------------------------------------------------------------------


async def test_delete_runtime_task(pool):
    """schedule_delete removes a runtime task."""
    from butlers.core.scheduler import schedule_create, schedule_delete

    task_id = await schedule_create(pool, "deletable", "0 9 * * *", "test")
    await schedule_delete(pool, task_id)

    row = await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE id = $1", task_id)
    assert row is None


async def test_delete_toml_task_raises(pool):
    """schedule_delete raises ValueError for TOML-sourced tasks."""
    from butlers.core.scheduler import schedule_delete, sync_schedules

    await sync_schedules(pool, [{"name": "toml-nodelete", "cron": "0 9 * * *", "prompt": "keep"}])
    row = await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE name = 'toml-nodelete'")

    with pytest.raises(ValueError, match="TOML"):
        await schedule_delete(pool, row["id"])


async def test_delete_nonexistent_raises(pool):
    """schedule_delete raises ValueError for a nonexistent task ID."""
    from butlers.core.scheduler import schedule_delete

    with pytest.raises(ValueError, match="not found"):
        await schedule_delete(pool, uuid.uuid4())


# ---------------------------------------------------------------------------
# CRUD — schedule_create duplicate name rejection
# ---------------------------------------------------------------------------


async def test_create_duplicate_name_raises(pool):
    """schedule_create raises ValueError when name already exists."""
    from butlers.core.scheduler import schedule_create

    await schedule_create(pool, "duplicate-name", "0 9 * * *", "first task")

    with pytest.raises(ValueError, match="already exists"):
        await schedule_create(pool, "duplicate-name", "0 10 * * *", "second task")


async def test_create_duplicate_name_no_insert(pool):
    """When duplicate name is rejected, no second row is inserted."""
    from butlers.core.scheduler import schedule_create

    await schedule_create(pool, "unique-check", "0 9 * * *", "first")

    try:
        await schedule_create(pool, "unique-check", "0 10 * * *", "second")
    except ValueError:
        pass

    # Verify only one row exists
    count = await pool.fetchval("SELECT COUNT(*) FROM scheduled_tasks WHERE name = 'unique-check'")
    assert count == 1


async def test_unique_constraint_in_migration(pool):
    """Verify the UNIQUE constraint exists on scheduled_tasks.name."""
    # Try to insert duplicate names directly (bypassing schedule_create)
    await pool.execute(
        "INSERT INTO scheduled_tasks (name, cron, prompt, source) VALUES ($1, $2, $3, $4)",
        "constraint-test",
        "0 9 * * *",
        "test",
        "runtime",
    )

    # Second insert with same name should fail
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO scheduled_tasks (name, cron, prompt, source) VALUES ($1, $2, $3, $4)",
            "constraint-test",
            "0 10 * * *",
            "test2",
            "runtime",
        )


# schedule_update — enabled toggle handling [butlers-06j.9]
# ---------------------------------------------------------------------------


async def test_update_enabled_true_recomputes_next_run(pool):
    """Enabling a disabled task recomputes next_run_at from current time."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "enable-me", "*/5 * * * *", "test")
    # Disable and clear next_run_at
    await pool.execute(
        "UPDATE scheduled_tasks SET enabled = false, next_run_at = NULL WHERE id = $1",
        task_id,
    )

    # Re-enable via schedule_update
    await schedule_update(pool, task_id, enabled=True)

    row = await pool.fetchrow(
        "SELECT enabled, next_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert row["enabled"] is True
    # next_run_at should now be computed and in the future
    assert row["next_run_at"] is not None
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)


async def test_update_enabled_false_nullifies_next_run(pool):
    """Disabling a task sets next_run_at to NULL."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "disable-me", "*/5 * * * *", "test")
    # Verify it starts with a next_run_at
    row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["next_run_at"] is not None

    # Disable
    await schedule_update(pool, task_id, enabled=False)

    row = await pool.fetchrow(
        "SELECT enabled, next_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert row["enabled"] is False
    assert row["next_run_at"] is None


async def test_update_cron_still_recomputes_next_run(pool):
    """Changing cron still recomputes next_run_at (existing behavior preserved)."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "cron-change", "0 0 1 1 *", "yearly")
    old_row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task_id)

    await schedule_update(pool, task_id, cron="*/1 * * * *")
    new_row = await pool.fetchrow("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task_id)

    # next_run_at should be recomputed and different
    assert new_row["next_run_at"] != old_row["next_run_at"]
    assert new_row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)


# Atomicity test for schedule_update
# ---------------------------------------------------------------------------


async def test_update_cron_is_atomic(pool):
    """When cron is changed, both cron and next_run_at are updated in a single transaction."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "atomic-test", "0 0 1 1 *", "rare")

    # Get the initial state
    initial = await pool.fetchrow(
        "SELECT cron, next_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert initial["cron"] == "0 0 1 1 *"

    # Update the cron - this should atomically update both cron and next_run_at
    await schedule_update(pool, task_id, cron="*/1 * * * *")

    # Verify both fields were updated
    updated = await pool.fetchrow(
        "SELECT cron, next_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert updated["cron"] == "*/1 * * * *"
    assert updated["next_run_at"] != initial["next_run_at"]
    # The new next_run_at should be much sooner (within the next minute)
    assert updated["next_run_at"] < initial["next_run_at"]


async def test_update_multiple_fields_with_cron_is_atomic(pool):
    """Updating multiple fields including cron happens in one atomic operation."""
    from butlers.core.scheduler import schedule_create, schedule_update

    task_id = await schedule_create(pool, "multi-update", "0 9 * * *", "old prompt")

    # Update multiple fields including cron
    await schedule_update(
        pool, task_id, name="multi-updated", cron="*/5 * * * *", prompt="new prompt", enabled=False
    )

    # Verify all fields were updated atomically
    updated = await pool.fetchrow(
        "SELECT name, cron, prompt, enabled, next_run_at FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert updated["name"] == "multi-updated"
    assert updated["cron"] == "*/5 * * * *"
    assert updated["prompt"] == "new prompt"
    assert updated["enabled"] is False
    # next_run_at should be NULL when task is disabled
    assert updated["next_run_at"] is None


# Telemetry — butler.tick span
# ---------------------------------------------------------------------------


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    from opentelemetry import trace

    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def otel_provider():
    """Set up an in-memory TracerProvider for tick span tests, then tear down."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


async def test_tick_creates_span_with_tasks_due_and_tasks_run(pool, otel_provider):
    """tick() creates butler.tick span with tasks_due and tasks_run attributes."""
    from butlers.core.scheduler import schedule_create, tick

    # Create 3 due tasks
    for i in range(3):
        task_id = await schedule_create(pool, f"due-{i}", "*/1 * * * *", f"prompt {i}")
        await pool.execute(
            "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
            task_id,
            datetime.now(UTC) - timedelta(minutes=5),
        )

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "butler.tick"
    assert span.attributes["tasks_due"] == 3
    assert span.attributes["tasks_run"] == 3


async def test_tick_span_with_no_due_tasks(pool, otel_provider):
    """tick() creates butler.tick span with tasks_due=0 when no tasks are due."""
    from butlers.core.scheduler import schedule_create, tick

    # Create a task far in the future
    await schedule_create(pool, "future", "0 0 1 1 *", "future")

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "butler.tick"
    assert span.attributes["tasks_due"] == 0
    assert span.attributes["tasks_run"] == 0


async def test_tick_span_counts_only_successful_dispatches(pool, otel_provider):
    """tick() span tasks_run reflects only successful dispatches."""
    from butlers.core.scheduler import schedule_create, tick

    # Create two due tasks, one will fail
    id1 = await schedule_create(pool, "fail", "*/1 * * * *", "fail-prompt")
    id2 = await schedule_create(pool, "ok", "*/1 * * * *", "ok-prompt")
    for tid in (id1, id2):
        await pool.execute(
            "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
            tid,
            datetime.now(UTC) - timedelta(minutes=5),
        )

    dispatch = _Dispatch(fail_on={"fail-prompt"})
    await tick(pool, dispatch)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes["tasks_due"] == 2
    assert span.attributes["tasks_run"] == 1  # Only the successful one
