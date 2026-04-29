"""Tests for butlers.core.scheduler — cron-driven task scheduler with TOML sync.

Covers:
- sync_schedules: insert, update (cron/prompt/mode), disable removed, re-enable restored
- tick: dispatch due prompt/job tasks, skip disabled, continue on failure, update timestamps
- schedule_create / update / delete: CRUD, validation, complexity, calendar fields
- schedule_list: field presence contract
- until_at: auto-disable when exceeded
- deadline task type: create with required fields
- cron staggering: determinism, cap, cadence preservation
- notify() validation: _check_notify_reference, sync_schedules warning behavior
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with scheduler table cleared between tests."""
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute("TRUNCATE TABLE scheduled_tasks CASCADE")
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


async def test_tick_dispatch_prompt_and_job(pool):
    """tick() dispatches prompt tasks with trigger_source; job tasks via job_name/job_args without prompt/complexity."""
    from butlers.core.scheduler import schedule_create, tick

    # Prompt task
    t1 = await schedule_create(pool, "due-task", "*/1 * * * *", "run this")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t1, _past())
    dispatch = _Dispatch()
    count = await tick(pool, dispatch)
    assert count == 1
    assert dispatch.calls[0]["prompt"] == "run this"
    assert dispatch.calls[0]["trigger_source"] == "schedule:due-task"

    # Job task
    t2 = await schedule_create(
        pool,
        "due-job",
        "*/1 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 25},
    )
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t2, _past())
    dispatch2 = _Dispatch()
    await tick(pool, dispatch2)
    call = dispatch2.calls[0]
    assert call["job_name"] == "eligibility_sweep" and call["job_args"] == {"batch_size": 25}
    assert "prompt" not in call and "complexity" not in call


async def test_tick_skips_disabled_continues_on_failure_and_timestamps(pool):
    """tick() skips disabled tasks; continues when dispatch raises; sets last_run_at; advances next_run_at; disables when until_at exceeded."""
    from butlers.core.scheduler import schedule_create, tick

    # Disabled task — skipped
    t1 = await schedule_create(pool, "disabled-task2", "*/1 * * * *", "skip me")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2, enabled = false WHERE id = $1", t1, _past()
    )
    # Task that fails — attempted but not counted
    t2 = await schedule_create(pool, "fail-task2", "*/1 * * * *", "I will fail")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t2, _past())
    dispatch = _Dispatch(fail_on={"I will fail"})
    count = await tick(pool, dispatch)
    assert count == 0
    assert len(dispatch.calls) == 1  # only the fail-task was attempted

    # Timestamps advance after success
    t3 = await schedule_create(pool, "advance-task2", "*/5 * * * *", "advance")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t3, _past(10))
    await tick(pool, _Dispatch())
    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1", t3
    )
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)
    assert row["last_run_at"] is not None

    # until_at exceeded → task disabled
    past_until = datetime.now(UTC) - timedelta(hours=1)
    t4 = await schedule_create(pool, "until-task2", "*/1 * * * *", "expiring", until_at=past_until)
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t4, _past())
    await tick(pool, _Dispatch())
    row2 = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE id = $1", t4)
    assert row2["enabled"] is False


# ---------------------------------------------------------------------------
# schedule_create / update / delete
# ---------------------------------------------------------------------------


async def test_schedule_create_and_list(pool):
    """schedule_create persists prompt and job tasks; schedule_list returns fields; invalid cron and dup name raise."""
    from butlers.core.scheduler import schedule_create, schedule_list

    # Prompt task with optional fields
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
    assert task is not None and task["complexity"] == "high"
    assert task["timezone"] == "America/New_York" and task["dispatch_mode"] == "prompt"
    assert task["last_result"] is None

    # Job task
    await schedule_create(
        pool,
        "job-list-task",
        "*/10 * * * *",
        dispatch_mode="job",
        job_name="my_job",
        job_args={"dry_run": True},
    )
    tasks2 = await schedule_list(pool)
    jt = next(t for t in tasks2 if t["name"] == "job-list-task")
    assert jt["dispatch_mode"] == "job" and jt["job_name"] == "my_job" and jt["prompt"] is None

    # Invalid cron raises
    with pytest.raises((ValueError, Exception)):
        await schedule_create(pool, "bad-cron", "not-a-cron", "test")

    # Duplicate name raises
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


async def test_schedule_validation(pool):
    """schedule_update raises for invalid cron and nonexistent ID; complexity enforced on create and update."""
    from butlers.core.scheduler import schedule_create, schedule_update

    # Invalid cron on update
    task_id = await schedule_create(pool, "cron-update-bad", "0 9 * * *", "prompt")
    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, task_id, cron="bad-cron")

    # Nonexistent ID raises
    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, uuid.uuid4(), prompt="does not exist")

    # Complexity: invalid on create
    with pytest.raises(ValueError, match="complexity"):
        await schedule_create(pool, "bad-complexity", "0 9 * * *", "work", complexity="ultra")

    # Complexity: valid accepted
    t2 = await schedule_create(
        pool, "good-complexity", "0 9 * * *", "work", complexity="extra_high"
    )
    row = await pool.fetchrow("SELECT complexity FROM scheduled_tasks WHERE id = $1", t2)
    assert row["complexity"] == "extra_high"

    # Complexity: invalid on update
    with pytest.raises(ValueError, match="complexity"):
        await schedule_update(pool, t2, complexity="super_high")


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


# ---------------------------------------------------------------------------
# Cron staggering (unit tests — no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stagger_determinism_cap_and_cadence() -> None:
    """Same key/cron always same offset; every-minute capped; cadences preserved."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.scheduler import _next_run, _stagger_offset_seconds

    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    # Determinism
    first = _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)
    assert first == _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)

    # Per-minute cron capped within 60s
    offset = _stagger_offset_seconds("* * * * *", stagger_key="switchboard", now=now)
    assert 0 <= offset <= 59

    # No stagger key same as None
    base = _next_run("0 * * * *", now=now)
    assert _next_run("0 * * * *", stagger_key=None, now=now) == base

    # 5-minute cadence preserved
    first_5 = _next_run("*/5 * * * *", stagger_key="general", now=now)
    second_5 = _next_run("*/5 * * * *", stagger_key="general", now=first_5)
    assert second_5 - first_5 == timedelta(minutes=5)

    # 1-minute cadence preserved
    first_1 = _next_run("* * * * *", stagger_key="messenger", now=now)
    second_1 = _next_run("* * * * *", stagger_key="messenger", now=first_1)
    assert second_1 - first_1 == timedelta(minutes=1)


# ---------------------------------------------------------------------------
# notify() validation in _check_notify_reference and sync_schedules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_notify_reference(tmp_path, caplog) -> None:
    """Warns when notify absent; silent when present or in skill; safe on missing dir."""
    import logging

    from butlers.core.scheduler import _check_notify_reference

    # Present: no warning
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="report", prompt="Call notify() to send.", skills_dir=None
        )
    assert "does not reference notify" not in caplog.text

    # Case-insensitive: no warning
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="task", prompt="Call NOTIFY() when done.", skills_dir=None
        )
    assert "does not reference notify" not in caplog.text

    # Absent: warning with task name
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="cleanup-task", prompt="Delete old temp files.", skills_dir=None
        )
    assert "does not reference notify" in caplog.text and "cleanup-task" in caplog.text

    # Skill with notify suppresses warning
    skill1 = tmp_path / "skills" / "daily-digest"
    skill1.mkdir(parents=True)
    (skill1 / "SKILL.md").write_text("# Daily Digest\nCall notify() to send it.", encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="digest", prompt="Run the daily-digest skill.", skills_dir=tmp_path / "skills"
        )
    assert "does not reference notify" not in caplog.text

    # Missing dir: safe
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="task", prompt="Run some-skill.", skills_dir=tmp_path / "nonexistent"
        )
    assert "does not reference notify" in caplog.text


async def test_sync_schedules_notify_validation(pool, caplog) -> None:
    """sync_schedules warns only for prompt tasks missing notify(); job tasks and notify-present tasks silenced."""
    import logging

    from butlers.core.scheduler import sync_schedules

    schedules = [
        {
            "name": "good-prompt",
            "cron": "0 8 * * *",
            "dispatch_mode": "prompt",
            "prompt": "Do something and notify(channel='telegram').",
        },
        {
            "name": "bad-prompt",
            "cron": "0 9 * * *",
            "dispatch_mode": "prompt",
            "prompt": "Do something quietly without alerting anyone.",
        },
        {"name": "job-task", "cron": "0 10 * * *", "dispatch_mode": "job", "job_name": "some-job"},
    ]
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await sync_schedules(pool, schedules)

    warning_records = [r for r in caplog.records if "does not reference notify" in r.message]
    assert len(warning_records) == 1
    assert "bad-prompt" in warning_records[0].message
