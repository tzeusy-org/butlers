"""Integration tests for deadline DB CRUD helpers.

Tests butlers.core.temporal.deadlines_db against a real PostgreSQL DB
(via testcontainers) with the deadline columns present.

Requires Docker to run.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# DB fixture with deadline-aware scheduled_tasks table
# ---------------------------------------------------------------------------

_CREATE_SCHEDULED_TASKS_SQL = """
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
        -- deadline columns (migration core_041)
        task_type TEXT NOT NULL DEFAULT 'cron'
            CHECK (task_type IN ('cron', 'deadline')),
        target_date DATE,
        lead_time_days INTEGER,
        alert_thresholds JSONB,
        deadline_status TEXT
            CHECK (deadline_status IN (
                'pending', 'alerted', 'escalated', 'completed', 'expired'
            )),
        fired_thresholds JSONB,
        depends_on JSONB,
        CONSTRAINT scheduled_tasks_dispatch_mode_check
            CHECK (dispatch_mode IN ('prompt', 'job')),
        CONSTRAINT scheduled_tasks_dispatch_payload_check
            CHECK (
                (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
            )
    )
"""


@pytest.fixture
async def pool(postgres_container):
    """Fresh DB with scheduled_tasks + deadline columns."""
    db_name = f"test_{uuid.uuid4().hex[:12]}"

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

    await p.execute(_CREATE_SCHEDULED_TASKS_SQL)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_date(days: int = 30):
    from datetime import date as _date

    return (_date.today() + timedelta(days=days))


def _threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


def _basic_thresholds():
    return [_threshold(30, "info"), _threshold(14, "warning"), _threshold(3, "critical")]


# ---------------------------------------------------------------------------
# deadline_create tests
# ---------------------------------------------------------------------------


class TestDeadlineCreate:
    async def test_create_returns_uuid(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create

        task_id = await deadline_create(
            pool,
            name="test-deadline-create",
            prompt="Alert about upcoming task.",
            target_date=_future_date(60),
            lead_time_days=42,
            alert_thresholds=_basic_thresholds(),
        )
        assert isinstance(task_id, uuid.UUID)

    async def test_created_row_has_correct_task_type(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create, get_deadline_by_id

        task_id = await deadline_create(
            pool,
            name="test-task-type",
            prompt="Check deadline.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        row = await get_deadline_by_id(pool, task_id)
        assert row is not None
        assert row["task_type"] == "deadline"
        assert row["deadline_status"] == "pending"

    async def test_create_with_dependencies(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create, get_deadline_by_id

        dep_id = str(uuid.uuid4())
        task_id = await deadline_create(
            pool,
            name="test-with-deps",
            prompt="Alert user.",
            target_date=_future_date(60),
            lead_time_days=30,
            alert_thresholds=[_threshold(30)],
            depends_on=[dep_id],
        )
        row = await get_deadline_by_id(pool, task_id)
        assert row is not None
        assert dep_id in row["depends_on"]

    async def test_duplicate_name_raises_value_error(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create

        kwargs: dict[str, Any] = dict(
            name="test-duplicate",
            prompt="Prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        await deadline_create(pool, **kwargs)
        with pytest.raises(ValueError, match="already exists"):
            await deadline_create(pool, **kwargs)

    async def test_empty_name_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create

        with pytest.raises(ValueError):
            await deadline_create(
                pool,
                name="",
                prompt="Prompt.",
                target_date=_future_date(30),
                lead_time_days=14,
                alert_thresholds=[_threshold(14)],
            )

    async def test_empty_prompt_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create

        with pytest.raises(ValueError):
            await deadline_create(
                pool,
                name="test-empty-prompt",
                prompt="",
                target_date=_future_date(30),
                lead_time_days=14,
                alert_thresholds=[_threshold(14)],
            )


# ---------------------------------------------------------------------------
# deadline_list tests
# ---------------------------------------------------------------------------


class TestDeadlineList:
    async def test_lists_only_deadline_tasks(self, pool):
        """deadline_list must not return cron tasks."""
        from butlers.core.temporal.deadlines_db import deadline_create, deadline_list

        # Insert a cron task directly
        await pool.execute(
            "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
            "VALUES ($1, '0 * * * *', 'cron prompt', 'prompt', 'db', 'cron')",
            "plain-cron-task",
        )
        await deadline_create(
            pool,
            name="list-test-deadline",
            prompt="Deadline prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        rows = await deadline_list(pool)
        names = {r["name"] for r in rows}
        assert "list-test-deadline" in names
        assert "plain-cron-task" not in names

    async def test_status_filter(self, pool):
        from butlers.core.temporal.deadlines_db import (
            deadline_create,
            deadline_list,
            deadline_update,
        )

        tid = await deadline_create(
            pool,
            name="filter-test-deadline",
            prompt="Deadline prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        # Default status is 'pending'; filtering by alerted should not include it
        alerted = await deadline_list(pool, status_filter="alerted")
        assert not any(r["id"] == str(tid) for r in alerted)

        # Update to alerted, then it should appear
        await deadline_update(pool, tid, deadline_status="alerted")
        alerted = await deadline_list(pool, status_filter="alerted")
        assert any(r["id"] == str(tid) for r in alerted)

    async def test_empty_list_when_no_deadlines(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_list

        rows = await deadline_list(pool)
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# deadline_update tests
# ---------------------------------------------------------------------------


class TestDeadlineUpdate:
    async def test_update_prompt(self, pool):
        from butlers.core.temporal.deadlines_db import (
            deadline_create,
            deadline_update,
            get_deadline_by_id,
        )

        tid = await deadline_create(
            pool,
            name="update-prompt-test",
            prompt="Original prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        await deadline_update(pool, tid, prompt="Updated prompt.")
        row = await get_deadline_by_id(pool, tid)
        assert row is not None
        assert row["prompt"] == "Updated prompt."

    async def test_update_target_date_resets_fired_thresholds_and_status(self, pool):
        from butlers.core.temporal.deadlines_db import (
            deadline_create,
            deadline_update,
            get_deadline_by_id,
        )

        tid = await deadline_create(
            pool,
            name="reset-test",
            prompt="Alert.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        # Manually set alerted + fired thresholds
        await pool.execute(
            "UPDATE scheduled_tasks SET deadline_status='alerted', "
            "fired_thresholds='[{\"days_before\": 14}]'::jsonb WHERE id = $1",
            tid,
        )
        # Now update target_date — should reset both
        await deadline_update(pool, tid, target_date=_future_date(60))
        row = await get_deadline_by_id(pool, tid)
        assert row is not None
        assert row["deadline_status"] == "pending"
        fired = row["fired_thresholds"]
        assert fired in ([], None, "[]")

    async def test_update_target_date_explicit_status_wins(self, pool):
        """If deadline_status is also provided alongside target_date, the explicit status wins."""
        from butlers.core.temporal.deadlines_db import (
            deadline_create,
            deadline_update,
            get_deadline_by_id,
        )

        tid = await deadline_create(
            pool,
            name="explicit-status-test",
            prompt="Prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        await deadline_update(
            pool,
            tid,
            target_date=_future_date(60),
            deadline_status="completed",
        )
        row = await get_deadline_by_id(pool, tid)
        assert row is not None
        assert row["deadline_status"] == "completed"

    async def test_update_nonexistent_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_update

        with pytest.raises(ValueError, match="not found"):
            await deadline_update(pool, uuid.uuid4(), prompt="New prompt.")

    async def test_update_cron_task_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_update

        # Insert a plain cron task
        cron_id = await pool.fetchval(
            "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
            "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
            "update-cron-test",
        )
        with pytest.raises(ValueError, match="not a deadline"):
            await deadline_update(pool, cron_id, prompt="New prompt.")

    async def test_update_nothing_does_not_fail(self, pool):
        """Calling update with no fields is a no-op."""
        from butlers.core.temporal.deadlines_db import deadline_create, deadline_update

        tid = await deadline_create(
            pool,
            name="noop-update",
            prompt="Prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        # Should not raise
        await deadline_update(pool, tid)


# ---------------------------------------------------------------------------
# deadline_delete tests
# ---------------------------------------------------------------------------


class TestDeadlineDelete:
    async def test_delete_removes_row(self, pool):
        from butlers.core.temporal.deadlines_db import (
            deadline_create,
            deadline_delete,
            get_deadline_by_id,
        )

        tid = await deadline_create(
            pool,
            name="delete-test",
            prompt="Prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        await deadline_delete(pool, tid)
        row = await get_deadline_by_id(pool, tid)
        assert row is None

    async def test_delete_nonexistent_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_delete

        with pytest.raises(ValueError, match="not found"):
            await deadline_delete(pool, uuid.uuid4())

    async def test_delete_cron_task_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_delete

        cron_id = await pool.fetchval(
            "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
            "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
            "delete-cron-test",
        )
        with pytest.raises(ValueError, match="not a deadline"):
            await deadline_delete(pool, cron_id)

    async def test_delete_toml_sourced_raises(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_delete

        toml_id = await pool.fetchval(
            "INSERT INTO scheduled_tasks "
            "(name, cron, prompt, dispatch_mode, source, task_type, "
            " target_date, lead_time_days, alert_thresholds, deadline_status, "
            " fired_thresholds, depends_on) "
            "VALUES ($1, '0 0 * * *', 'Alert.', 'prompt', 'toml', 'deadline', "
            " CURRENT_DATE + 30, 14, '[]'::jsonb, 'pending', '[]'::jsonb, '[]'::jsonb) "
            "RETURNING id",
            "toml-deadline",
        )
        with pytest.raises(ValueError, match="TOML"):
            await deadline_delete(pool, toml_id)


# ---------------------------------------------------------------------------
# get_deadline_by_id tests
# ---------------------------------------------------------------------------


class TestGetDeadlineById:
    async def test_returns_none_for_unknown_id(self, pool):
        from butlers.core.temporal.deadlines_db import get_deadline_by_id

        result = await get_deadline_by_id(pool, uuid.uuid4())
        assert result is None

    async def test_returns_none_for_cron_task(self, pool):
        from butlers.core.temporal.deadlines_db import get_deadline_by_id

        cron_id = await pool.fetchval(
            "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
            "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
            "get-by-id-cron",
        )
        result = await get_deadline_by_id(pool, cron_id)
        assert result is None

    async def test_returns_dict_with_string_id(self, pool):
        from butlers.core.temporal.deadlines_db import deadline_create, get_deadline_by_id

        tid = await deadline_create(
            pool,
            name="get-by-id-test",
            prompt="Prompt.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )
        row = await get_deadline_by_id(pool, tid)
        assert row is not None
        assert isinstance(row["id"], str)
        assert row["id"] == str(tid)
