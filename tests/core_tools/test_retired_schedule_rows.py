"""A task retired by a delete (bu-5ii4gj.4) must not run, be edited, or come back.

Real PostgreSQL. A task with a calendar mirror is retired rather than deleted so the mirror can
remove its provider event; until then the row exists but must behave as deleted everywhere else.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.core.scheduler.crud import schedule_create, schedule_delete, schedule_list
from butlers.core.scheduler.dispatch import tick
from butlers.core.scheduler.update import schedule_toggle, schedule_update
from tests.core_tools.test_schedule_trigger_dispatch import (
    _RecordingDaemon,
    _register_and_grab_schedule_trigger,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _retired_task(pool, name: str = "retire-me") -> Any:
    task_id = await schedule_create(pool, name, "0 9 * * *", "do it")
    await pool.execute(
        "UPDATE scheduled_tasks SET calendar_event_id = 'gcal1' WHERE id = $1", task_id
    )
    await schedule_delete(pool, task_id)
    assert await pool.fetchval(
        "SELECT delete_requested_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    return task_id


async def test_a_retired_task_cannot_be_updated_or_reenabled(db_pool):
    task_id = await _retired_task(db_pool)

    with pytest.raises(ValueError, match="not found"):
        await schedule_update(db_pool, task_id, enabled=True)
    result = await schedule_toggle(db_pool, task_id, enabled=True)

    assert result["status"] == "error"
    row = await db_pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["enabled"] is False


async def test_a_retired_task_cannot_be_triggered(db_pool):
    task_id = await _retired_task(db_pool)
    daemon = _RecordingDaemon(result={"ok": True})
    trigger = _register_and_grab_schedule_trigger(db_pool, daemon)

    result = await trigger(task_id=str(task_id))

    assert result["status"] == "error" and daemon.calls == []


async def test_a_retired_task_is_not_listed(db_pool):
    task_id = await _retired_task(db_pool)
    assert task_id not in [task["id"] for task in await schedule_list(db_pool)]


class _RetireAfterDueRead:
    """Delegates to the real pool, but retires a task right after the due read returns it."""

    def __init__(self, pool, task_id) -> None:
        self._pool = pool
        self._task_id = task_id
        self._done = False

    def __getattr__(self, name: str):
        return getattr(self._pool, name)

    async def fetch(self, query: str, *args: Any):
        rows = await self._pool.fetch(query, *args)
        if not self._done and "next_run_at <= $1" in query:
            self._done = True
            await schedule_delete(self._pool, self._task_id)
        return rows


async def test_a_tick_that_read_a_task_before_its_retire_does_not_fire_it(db_pool):
    # The fixture schema predates these optional tick columns (the tick selects them when present).
    await db_pool.execute(
        "ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS max_token_budget INTEGER, "
        "ADD COLUMN IF NOT EXISTS continuity BOOLEAN NOT NULL DEFAULT false"
    )
    task_id = await schedule_create(db_pool, "due-then-retired", "0 9 * * *", "do it")
    await db_pool.execute(
        "UPDATE scheduled_tasks SET calendar_event_id = 'gcal1', next_run_at = $2 WHERE id = $1",
        task_id,
        datetime.now(UTC) - timedelta(minutes=5),
    )
    fired: list[dict] = []

    async def dispatch(**kwargs: Any) -> dict:
        fired.append(kwargs)
        return {"status": "ok"}

    await tick(_RetireAfterDueRead(db_pool, task_id), dispatch)

    assert fired == []
    row = await db_pool.fetchrow(
        "SELECT delete_requested_at, last_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert row["delete_requested_at"] is not None and row["last_run_at"] is None
