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

import pytest

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _future_date(days: int = 30):
    from datetime import date as _date

    return _date.today() + timedelta(days=days)


def _threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


def _basic_thresholds():
    return [_threshold(30, "info"), _threshold(14, "warning"), _threshold(3, "critical")]


# ---------------------------------------------------------------------------
# deadline_create
# ---------------------------------------------------------------------------


async def test_deadline_create(db_pool):
    """create returns UUID, row has correct task_type/status/defaults; duplicates raise;
    validation errors raise."""
    from butlers.core.temporal.deadlines_db import deadline_create, get_deadline_by_id

    # Creates a row with correct task_type, deadline_status, and stores depends_on
    dep_id = str(uuid.uuid4())
    task_id = await deadline_create(
        db_pool,
        name="test-deadline-create",
        prompt="Alert about upcoming task.",
        target_date=_future_date(60),
        lead_time_days=42,
        alert_thresholds=_basic_thresholds(),
        depends_on=[dep_id],
    )
    assert isinstance(task_id, uuid.UUID)
    row = await get_deadline_by_id(db_pool, task_id)
    assert (
        row is not None and row["task_type"] == "deadline" and row["deadline_status"] == "pending"
    )
    assert dep_id in row["depends_on"]

    # Duplicate name raises
    with pytest.raises(ValueError, match="already exists"):
        await deadline_create(
            db_pool,
            name="test-deadline-create",
            prompt="P.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        )

    # Validation: empty name, empty prompt
    for kwargs in [
        dict(
            name="",
            prompt="P.",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        ),
        dict(
            name="test-empty-prompt",
            prompt="",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_threshold(14)],
        ),
    ]:
        with pytest.raises(ValueError):
            await deadline_create(db_pool, **kwargs)


# ---------------------------------------------------------------------------
# deadline_list
# ---------------------------------------------------------------------------


async def test_deadline_list(db_pool):
    """Lists only deadline tasks; status filter works; invalid status raises."""
    from butlers.core.temporal.deadlines_db import (
        deadline_create,
        deadline_list,
        deadline_update,
    )

    # Insert a cron task — should NOT appear in list
    await db_pool.execute(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
        "VALUES ($1, '0 * * * *', 'cron prompt', 'prompt', 'db', 'cron')",
        "plain-cron-task",
    )
    tid = await deadline_create(
        db_pool,
        name="list-test-deadline",
        prompt="Deadline prompt.",
        target_date=_future_date(30),
        lead_time_days=14,
        alert_thresholds=[_threshold(14)],
    )

    rows = await deadline_list(db_pool)
    names = {r["name"] for r in rows}
    assert "list-test-deadline" in names and "plain-cron-task" not in names

    # Status filter
    alerted = await deadline_list(db_pool, status="alerted")
    assert not any(r["id"] == str(tid) for r in alerted)
    await deadline_update(db_pool, tid, deadline_status="alerted")
    alerted2 = await deadline_list(db_pool, status="alerted")
    assert any(r["id"] == str(tid) for r in alerted2)

    # Invalid status raises
    with pytest.raises(ValueError, match="Invalid status"):
        await deadline_list(db_pool, status="unknown-status")


# ---------------------------------------------------------------------------
# deadline_update
# ---------------------------------------------------------------------------


async def test_deadline_update(db_pool):
    """Updating target_date resets fired_thresholds/status; status=completed disables;
    non-deadline raises; invalid UUID raises."""
    from butlers.core.temporal.deadlines_db import (
        deadline_create,
        deadline_update,
        get_deadline_by_id,
    )

    tid = await deadline_create(
        db_pool,
        name="update-test",
        prompt="Alert.",
        target_date=_future_date(30),
        lead_time_days=14,
        alert_thresholds=[_threshold(14)],
    )

    # Update prompt
    await deadline_update(db_pool, tid, prompt="Updated prompt.")
    row = await get_deadline_by_id(db_pool, tid)
    assert row["prompt"] == "Updated prompt."

    # Update target_date resets fired_thresholds and status
    await db_pool.execute(
        "UPDATE scheduled_tasks SET deadline_status='alerted',"
        " fired_thresholds='[{\"days_before\": 14}]'::jsonb WHERE id = $1",
        tid,
    )
    await deadline_update(db_pool, tid, target_date=_future_date(60))
    row2 = await get_deadline_by_id(db_pool, tid)
    assert row2["deadline_status"] == "pending"
    assert row2["fired_thresholds"] in ([], None, "[]")

    # Setting completed disables task
    tid2 = await deadline_create(
        db_pool,
        name="complete-test",
        prompt="Alert.",
        target_date=_future_date(30),
        lead_time_days=14,
        alert_thresholds=[_threshold(14)],
    )
    await deadline_update(db_pool, tid2, deadline_status="completed")
    r = await get_deadline_by_id(db_pool, tid2)
    assert r["deadline_status"] == "completed"

    # Non-existent task raises
    with pytest.raises(ValueError, match="not found"):
        await deadline_update(db_pool, uuid.uuid4(), prompt="New prompt.")

    # Non-deadline task raises
    cron_id = await db_pool.fetchval(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
        "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
        "update-cron-test",
    )
    with pytest.raises(ValueError, match="not a deadline"):
        await deadline_update(db_pool, cron_id, prompt="New prompt.")

    # Invalid UUID raises
    with pytest.raises(ValueError, match="Invalid task_id|not found|not a deadline"):
        await deadline_update(db_pool, "not-a-uuid", deadline_status="completed")


# ---------------------------------------------------------------------------
# deadline_delete
# ---------------------------------------------------------------------------


async def test_deadline_delete(db_pool):
    """Delete removes row; nonexistent raises; cron task raises; TOML-sourced raises."""
    from butlers.core.temporal.deadlines_db import (
        deadline_create,
        deadline_delete,
        get_deadline_by_id,
    )

    # Delete removes row
    tid = await deadline_create(
        db_pool,
        name="delete-test",
        prompt="Prompt.",
        target_date=_future_date(30),
        lead_time_days=14,
        alert_thresholds=[_threshold(14)],
    )
    await deadline_delete(db_pool, tid)
    assert await get_deadline_by_id(db_pool, tid) is None

    # Non-existent raises
    with pytest.raises(ValueError, match="not found"):
        await deadline_delete(db_pool, uuid.uuid4())

    # Cron task raises
    cron_id = await db_pool.fetchval(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
        "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
        "delete-cron-test",
    )
    with pytest.raises(ValueError, match="not a deadline"):
        await deadline_delete(db_pool, cron_id)

    # TOML-sourced deadline raises
    toml_id = await db_pool.fetchval(
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
        await deadline_delete(db_pool, toml_id)


# ---------------------------------------------------------------------------
# get_deadline_by_id
# ---------------------------------------------------------------------------


async def test_get_deadline_by_id(db_pool):
    """Returns None for unknown ID and cron tasks; returns dict with string ID for deadlines."""
    from butlers.core.temporal.deadlines_db import deadline_create, get_deadline_by_id

    assert await get_deadline_by_id(db_pool, uuid.uuid4()) is None

    cron_id = await db_pool.fetchval(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
        "VALUES ($1, '0 * * * *', 'prompt', 'prompt', 'db', 'cron') RETURNING id",
        "get-by-id-cron",
    )
    assert await get_deadline_by_id(db_pool, cron_id) is None

    tid = await deadline_create(
        db_pool,
        name="get-by-id-test",
        prompt="Prompt.",
        target_date=_future_date(30),
        lead_time_days=14,
        alert_thresholds=[_threshold(14)],
    )
    row = await get_deadline_by_id(db_pool, tid)
    assert row is not None and isinstance(row["id"], str) and row["id"] == str(tid)
