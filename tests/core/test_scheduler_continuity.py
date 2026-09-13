"""bu-2jtfw.13: the task-continuity ledger at the real scheduler dispatch seam.

Covers:
- AC6: run N+1 of an opted-in recurring task carries run N's carry_forward
  block with its age.
- AC7: a gap (task ran under continuity but never called carry_forward) is
  named honestly, never silently treated as if nothing changed.
- AC8: two ticks racing the same due occurrence dispatch at most once
  (the existing atomic occurrence claim), so at most one carry_forward call
  can ever happen per occurrence -- exactly one live continuity row results.
- AC10: a task NOT opted into continuity (chronicler's day-close default) is
  byte-identical whether or not this whole feature exists.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, build_day_close_prompt_hooks
from butlers.core.scheduler import _prepare_scheduled_prompt, _prepend_seasonal_context
from butlers.core.task_continuity import record_carry_forward
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE scheduled_tasks CASCADE")
    # public.task_continuity has RLS FORCEd with no DELETE policy (matching
    # public.expected_signals' precedent), and this pool's login is an
    # ordinary non-superuser role, so a cleanup DELETE here would silently
    # no-op. Tests use unique task names instead of relying on row cleanup.
    yield p
    await p.close()


class _Dispatch:
    """Records dispatch calls and lets the caller simulate a carry_forward write."""

    def __init__(self, *, butler_name: str, on_dispatch=None) -> None:
        self.calls: list[dict] = []
        self._butler_name = butler_name
        self._on_dispatch = on_dispatch

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._on_dispatch is not None:
            await self._on_dispatch(kwargs)
        return {"status": "ok"}


def _past(minutes: int = 5) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


async def _create_continuity_task(pool: asyncpg.Pool, name: str, prompt: str) -> uuid.UUID:
    from butlers.core.scheduler import schedule_create

    task_id = await schedule_create(pool, name, "*/1 * * * *", prompt)
    await pool.execute(
        "UPDATE scheduled_tasks SET continuity = true, next_run_at = $2 WHERE id = $1",
        task_id,
        _past(),
    )
    return task_id


@_asyncio_session
async def test_second_run_carries_first_runs_carry_forward_with_age(pool) -> None:
    task_name = "continuity-task"
    await _create_continuity_task(pool, task_name, "Do the thing")

    dispatch1 = _Dispatch(butler_name="health")
    from butlers.core.scheduler import tick

    count = await tick(pool, dispatch1, butler_name="health")
    assert count == 1
    # Run 1: no live row yet -> honest gap wording (AC7), not silence.
    assert "no carry-forward" in dispatch1.calls[0]["prompt"]

    # Simulate run 1's session calling carry_forward.
    session_1 = uuid.uuid4()
    await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=session_1,
        content="Reviewed 3 measurements; flagged none as anomalous.",
    )

    # Advance the task to due again for run 2.
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE name = $1", task_name, _past()
    )
    dispatch2 = _Dispatch(butler_name="health")
    count2 = await tick(pool, dispatch2, butler_name="health")
    assert count2 == 1

    dispatched_prompt = dispatch2.calls[0]["prompt"]
    assert "Reviewed 3 measurements; flagged none as anomalous." in dispatched_prompt
    assert str(session_1) in dispatched_prompt
    assert "s ago" in dispatched_prompt  # age is named, not just the raw content


@_asyncio_session
async def test_gap_is_named_when_a_run_never_called_carry_forward(pool) -> None:
    """AC7: run N records nothing -> run N+1 names the gap, not silence."""
    task_name = "continuity-gap-task"
    await _create_continuity_task(pool, task_name, "Check something")

    from butlers.core.scheduler import tick

    dispatch = _Dispatch(butler_name="health")
    count = await tick(pool, dispatch, butler_name="health")
    assert count == 1
    assert f"## Task Continuity — {task_name}" in dispatch.calls[0]["prompt"]
    assert "The last run recorded no carry-forward." in dispatch.calls[0]["prompt"]


@_asyncio_session
async def test_two_concurrent_ticks_dispatch_at_most_once_and_carry_forward_stays_single_row(
    pool,
) -> None:
    """AC8: the atomic occurrence claim means only one tick's session can ever
    call carry_forward for a given occurrence, so exactly one live row results."""
    task_name = "continuity-race-task"
    await _create_continuity_task(pool, task_name, "Race me")

    from butlers.core.scheduler import tick

    async def dispatch_and_record(kwargs: dict[str, Any]) -> None:
        await record_carry_forward(
            pool,
            butler_name="health",
            task_name=task_name,
            session_id=uuid.uuid4(),
            content="Run concluded.",
        )

    dispatch_a = _Dispatch(butler_name="health", on_dispatch=dispatch_and_record)
    dispatch_b = _Dispatch(butler_name="health", on_dispatch=dispatch_and_record)

    counts = await asyncio.gather(
        tick(pool, dispatch_a, butler_name="health"),
        tick(pool, dispatch_b, butler_name="health"),
    )

    assert sum(counts) == 1
    assert len(dispatch_a.calls) + len(dispatch_b.calls) == 1

    rows = await pool.fetch(
        "SELECT id FROM public.task_continuity WHERE butler_name = $1 AND task_name = $2 "
        "AND is_live",
        "health",
        task_name,
    )
    assert len(rows) == 1


@_asyncio_session
async def test_chronicler_day_close_prompt_unaffected_when_not_opted_in(pool) -> None:
    """AC10: chronicler's day-close prompt is byte-identical without continuity=true.

    Retiring the bespoke day-close hook onto the general layer is explicitly a
    non-goal of this bead -- this proves the new dispatch-seam code is a
    strict no-op for a task that has not opted in, which is what makes that
    future migration safe to attempt later.
    """
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, DAY_CLOSE_TASK_NAME, "5 1 * * *", "Chronicle the day")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", task_id, _past()
    )
    # continuity defaults to false — deliberately not set here.

    prompt_hooks = build_day_close_prompt_hooks(timezone="UTC")
    dispatch = _Dispatch(butler_name="chronicler")
    count = await tick(pool, dispatch, butler_name="chronicler", prompt_hooks=prompt_hooks)
    assert count == 1

    dispatched_prompt = dispatch.calls[0]["prompt"]
    now = datetime.now(UTC)
    expected_hook_only = _prepare_scheduled_prompt(
        prompt_hooks,
        task_name=DAY_CLOSE_TASK_NAME,
        prompt=_prepend_seasonal_context("Chronicle the day", None),
        run_at=now,
        timezone="UTC",
    )
    # The scheduler-owned date binding text is present and unaltered; no
    # continuity block was appended (continuity=false by default) -- the new
    # dispatch-seam code is a strict no-op for a non-opted-in task.
    assert "Trusted scheduled target" in dispatched_prompt
    assert "Task Continuity" not in dispatched_prompt
    assert dispatched_prompt == expected_hook_only
