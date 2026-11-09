"""A CancelledError nobody asked for is a failed job, not a shutdown (bu-dhloi1.59).

A scheduled job can raise ``CancelledError`` with no ``task.cancel()`` behind it: a cancelled
child task it awaited, or the asyncpg race where a statement raises it after an earlier
timeout. ``except Exception`` misses it, so it used to leave ``tick()`` with the occurrence
claimed and no result recorded. ``tick()`` then ended as a cancelled task, which the scheduler
loop could not tell from its own shutdown, so the butler's scheduler stopped until a restart.

``Task.cancelling()`` tells them apart. A real cancel of the running task must still propagate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from unittest.mock import AsyncMock

import pytest

from butlers import background
from butlers.core.scheduler.dispatch import tick

_db_backed = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# tick(): one job's stray cancel is that job's failure
# ---------------------------------------------------------------------------


@pytest.fixture
async def tick_pool(db):
    return await db("core", schemas={"core": "general"}, schema="general")


async def _due_job(pool, name: str, *, minutes_ago: int) -> None:
    await pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, job_name, next_run_at, enabled)
        VALUES ($1, '0 0 1 1 *', 'job', 'some_job',
                now() - make_interval(mins => $2), true)
        """,
        name,
        minutes_ago,
    )


async def _due_deadline(pool, name: str, *, minutes_ago: int) -> None:
    await pool.execute(
        """
        INSERT INTO scheduled_tasks
            (name, cron, prompt, dispatch_mode, task_type, target_date, lead_time_days,
             alert_thresholds, deadline_status, fired_thresholds, next_run_at, enabled)
        VALUES ($1, '* * * * *', 'Review it', 'prompt', 'deadline', current_date + 30, 30,
                '[{"days_before": 30, "severity": "info"}]', 'pending', '[]',
                now() - make_interval(mins => $2), true)
        """,
        name,
        minutes_ago,
    )


async def _last_result(pool, name: str):
    row = await pool.fetchrow(
        "SELECT last_run_at, last_result FROM scheduled_tasks WHERE name = $1", name
    )
    result = row["last_result"]
    return row["last_run_at"], json.loads(result) if isinstance(result, str) else result


class TestTick:
    pytestmark = _db_backed

    async def test_a_stray_cancel_records_a_failure_and_the_next_job_runs(self, tick_pool):
        await _due_job(tick_pool, "stray", minutes_ago=2)
        await _due_job(tick_pool, "next", minutes_ago=1)
        ran = []

        async def dispatch(*, trigger_source, **_kwargs):
            ran.append(trigger_source)
            if trigger_source == "schedule:stray":
                raise asyncio.CancelledError
            return {"ok": True}

        dispatched = await asyncio.create_task(tick(tick_pool, dispatch))

        assert ran == ["schedule:stray", "schedule:next"]
        assert dispatched == 1
        last_run_at, result = await _last_result(tick_pool, "stray")
        assert last_run_at is not None
        assert result == {"error": "CancelledError()"}
        assert (await _last_result(tick_pool, "next"))[1] == {"ok": True}

    async def test_a_stopping_tick_claims_no_further_rows(self, tick_pool):
        """bu-dhloi1.138: shutdown mid-tick lets the claimed row dispatch, and claims no more."""
        await _due_job(tick_pool, "first", minutes_ago=2)
        await _due_job(tick_pool, "second", minutes_ago=1)
        before = await tick_pool.fetchval(
            "SELECT next_run_at FROM scheduled_tasks WHERE name = 'second'"
        )
        stopping = asyncio.Event()
        ran = []

        async def dispatch(*, trigger_source, **_kwargs):
            ran.append(trigger_source)
            stopping.set()  # shutdown arrives while the first row is dispatching
            return {"ok": True}

        dispatched = await tick(tick_pool, dispatch, stopping=stopping)

        assert ran == ["schedule:first"]
        assert dispatched == 1
        assert (await _last_result(tick_pool, "first"))[1] == {"ok": True}
        row = await tick_pool.fetchrow(
            "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE name = 'second'"
        )
        assert row["next_run_at"] == before
        assert row["last_run_at"] is None

    async def test_a_real_cancel_of_a_running_tick_still_propagates(self, tick_pool):
        await _due_job(tick_pool, "running", minutes_ago=2)
        await _due_job(tick_pool, "never", minutes_ago=1)
        started = asyncio.Event()
        ran = []

        async def dispatch(*, trigger_source, **_kwargs):
            ran.append(trigger_source)
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(tick(tick_pool, dispatch))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled()
        assert ran == ["schedule:running"]
        assert (await _last_result(tick_pool, "running")) == (None, None)

    async def test_a_stray_cancel_from_a_deadline_prompt_leaves_cron_work_running(self, tick_pool):
        """The deadline pass runs first, so its stray cancel must not starve every later pass."""
        await _due_deadline(tick_pool, "deadline", minutes_ago=2)
        await _due_job(tick_pool, "cron", minutes_ago=1)
        ran = []

        async def dispatch(*, trigger_source, **_kwargs):
            ran.append(trigger_source)
            if trigger_source.startswith("deadline:"):
                raise asyncio.CancelledError

        await asyncio.create_task(tick(tick_pool, dispatch))

        assert ran == ["deadline:deadline", "schedule:cron"]
        # Not recorded as fired, so the threshold retries on the next tick.
        fired = await tick_pool.fetchval(
            "SELECT fired_thresholds FROM scheduled_tasks WHERE name = 'deadline'"
        )
        assert (json.loads(fired) if isinstance(fired, str) else fired) == []

    async def test_a_stray_cancel_from_a_deadline_job_leaves_cron_work_running(self, tick_pool):
        await tick_pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, dispatch_mode, job_name, task_type, target_date, lead_time_days,
                 alert_thresholds, deadline_status, fired_thresholds, next_run_at, enabled)
            VALUES ('deadline-job', '* * * * *', 'job', 'some_job', 'deadline',
                    current_date + 30, 30, '[{"days_before": 30, "severity": "info"}]',
                    'pending', '[]', now() - interval '2 minutes', true)
            """
        )
        await _due_job(tick_pool, "cron", minutes_ago=1)
        ran = []

        async def dispatch(*, trigger_source, **_kwargs):
            ran.append(trigger_source)
            if trigger_source.startswith("deadline:"):
                raise asyncio.CancelledError

        await asyncio.create_task(tick(tick_pool, dispatch))

        assert ran == ["deadline:deadline-job", "schedule:cron"]


# ---------------------------------------------------------------------------
# scheduler_loop(): only its own cancel ends it
# ---------------------------------------------------------------------------


def _start_loop(monkeypatch, tick_fn, *, sweep=None) -> asyncio.Task:
    monkeypatch.setattr(
        background,
        "reconcile_reaction_lifecycle",
        sweep or AsyncMock(return_value={"examined": 0, "running": 0, "unreported": 0}),
    )
    return asyncio.create_task(
        background.scheduler_loop(
            pool=AsyncMock(),
            dispatch_fn=AsyncMock(),
            interval=0,
            butler_name="finance",
            tick_fn=tick_fn,
            get_switchboard_client=lambda: None,
            get_db=lambda: None,
        )
    )


@pytest.mark.unit
async def test_a_stray_cancel_from_a_tick_does_not_end_the_scheduler_loop(monkeypatch, caplog):
    second_tick = asyncio.Event()
    ticks = 0

    async def tick_fn(*_args, **_kwargs):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            raise asyncio.CancelledError
        second_tick.set()
        return 0

    with caplog.at_level(logging.ERROR, logger="butlers.background"):
        task = _start_loop(monkeypatch, tick_fn)
        try:
            await asyncio.wait_for(second_tick.wait(), timeout=5)
        finally:
            task.cancel()
            await task

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("finance" in message and "cancel" in message for message in errors), errors


@pytest.mark.unit
async def test_a_stray_cancel_after_a_tick_does_not_end_the_scheduler_loop(monkeypatch):
    """The asyncpg race surfaces on whichever statement comes next, not only inside a job."""
    second_tick = asyncio.Event()
    ticks = 0

    async def tick_fn(*_args, **_kwargs):
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            second_tick.set()
        return 0

    sweep = AsyncMock(side_effect=[asyncio.CancelledError(), {}, {}, {}, {}, {}, {}, {}])
    task = _start_loop(monkeypatch, tick_fn, sweep=sweep)
    try:
        await asyncio.wait_for(second_tick.wait(), timeout=5)
    finally:
        task.cancel()
        await task


@pytest.mark.unit
async def test_the_loops_own_cancel_lets_the_tick_finish_then_ends_it(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    ticks, finished = 0, 0

    async def tick_fn(*_args, **_kwargs):
        nonlocal ticks, finished
        ticks += 1
        started.set()
        await release.wait()
        finished += 1
        return 0

    task = _start_loop(monkeypatch, tick_fn)
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()

    await asyncio.wait_for(task, timeout=5)

    assert (ticks, finished) == (1, 1)


@pytest.mark.unit
async def test_a_stray_cancel_during_shutdown_does_not_keep_the_loop_alive(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    ticks = 0

    async def tick_fn(*_args, **_kwargs):
        nonlocal ticks
        ticks += 1
        started.set()
        await release.wait()
        raise asyncio.CancelledError

    task = _start_loop(monkeypatch, tick_fn)
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    await asyncio.wait_for(task, timeout=5)

    assert ticks == 1


@pytest.mark.unit
async def test_the_scheduler_loop_tells_a_running_tick_to_claim_nothing_more(monkeypatch):
    """The loop sets ``stopping`` when cancelled mid-tick, then waits for that tick."""
    started, release = asyncio.Event(), asyncio.Event()
    seen: list[bool] = []

    async def tick_fn(*_args, stopping=None, **_kwargs):
        assert stopping is not None and not stopping.is_set()
        started.set()
        await release.wait()
        seen.append(stopping.is_set())
        return 0

    task = _start_loop(monkeypatch, tick_fn)
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert seen == [True]
