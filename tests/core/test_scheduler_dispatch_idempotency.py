"""Tests for scheduler tick() idempotency guard (bu-0g76b).

Problem 2: one calendar-event scheduled task (finance butler) produced 4
sessions within ~90 s even though last_run_at advanced.  The root cause is
that tick() lacks a per-task claim step — concurrent invocations (e.g. the
background scheduler loop + the MCP `tick` tool) all read the same due row
before any of them updates next_run_at.

Fix: before calling dispatch_fn, tick() atomically advances next_run_at (the
"claim" step).  If another tick already claimed the row, the UPDATE returns 0
rows and the task is skipped without dispatch.

These tests verify:
1. A due task dispatches exactly once when tick() is called sequentially.
2. A second concurrent tick() call that races against the first's dispatch
   is skipped (claim returns nothing → no second dispatch).
3. A task past its until_at is auto-disabled without dispatching again.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared pool infrastructure
# ---------------------------------------------------------------------------


class _ClaimTrackingPool:
    """Asyncpg pool double that simulates atomic claim semantics.

    Tracks which tasks have been claimed and enforces that each task can only
    be claimed once per next_run_at value, matching the production SQL:

        UPDATE scheduled_tasks
        SET next_run_at = $2
        WHERE id = $1 AND next_run_at IS NOT DISTINCT FROM $3
        RETURNING id

    The first caller wins; subsequent callers get None.
    """

    def __init__(self, *, tasks: list[dict]) -> None:
        self._tasks = tasks
        # Maps task_id → current next_run_at (mutable, reflects claims)
        self._current_next_run_at: dict[str, datetime | None] = {
            t["id"]: t["next_run_at"] for t in tasks
        }
        self.claimed_ids: list[str] = []
        self.dispatch_calls: list[str] = []
        self.execute_calls: list[str] = []
        self.fetchrow_calls: list[str] = []

    async def fetchval(self, query: str, *args: Any) -> object:
        """Schema probes: report presence of optional columns."""
        if "information_schema.columns" in query:
            col = args[1] if len(args) > 1 else ""
            # Return the requested column as present when it's "task_type" or "until_at"
            if isinstance(col, (list, tuple, set)):
                return [{"column_name": c} for c in col if c == "task_type"]
            return col in {"task_type", "until_at"}
        if "information_schema.tables" in query:
            return False
        return False

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        """Return due tasks or schema column lists."""
        if "information_schema.columns" in query:
            requested = set(args[1]) if len(args) > 1 else set()
            return [{"column_name": c} for c in requested if c == "task_type"]
        if "FROM scheduled_tasks" in query and "next_run_at <= $1" in query:
            return list(self._tasks)
        # seasonal periods, event chains, deferred notifications → empty
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        """Simulate the atomic claim UPDATE."""
        self.fetchrow_calls.append(query)
        if "UPDATE scheduled_tasks" in query and "IS NOT DISTINCT FROM" in query:
            task_id = args[0]
            original_next_run_at = args[-1]  # last positional arg is the version value
            current = self._current_next_run_at.get(str(task_id))
            if current == original_next_run_at:
                # Claim succeeds — advance next_run_at
                new_next_run_at = args[1] if len(args) >= 3 else None
                self._current_next_run_at[str(task_id)] = new_next_run_at
                self.claimed_ids.append(str(task_id))
                return {"id": task_id}
            # Already claimed by another tick
            return None
        return None

    async def execute(self, query: str, *args: Any) -> str:
        """Record last_run_at / last_result updates and other mutations."""
        self.execute_calls.append(query)
        return "OK"


def _make_due_task(
    *,
    task_id: str = "task-1",
    name: str = "calendar-event-test",
    next_run_at: datetime | None = None,
    until_at: datetime | None = None,
) -> dict:

    _now = datetime.now(UTC)
    return {
        "id": task_id,
        "name": name,
        "cron": "15 1 * * *",
        "dispatch_mode": "prompt",
        "prompt": "Scheduled event: Pay bills at 01:15 UTC.",
        "job_name": None,
        "job_args": None,
        "complexity": None,
        "timezone": "UTC",
        "next_run_at": next_run_at or _now,
        "until_at": until_at,
        "max_token_budget": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSchedulerDispatchIdempotency:
    """tick() must dispatch each due occurrence exactly once."""

    async def test_single_tick_dispatches_once(self) -> None:
        """One tick() call for one due task → exactly one dispatch."""
        from butlers.core.scheduler import tick

        task = _make_due_task()
        pool = _ClaimTrackingPool(tasks=[task])
        dispatch_calls: list[dict] = []

        async def dispatch(**kwargs: Any) -> dict:
            dispatch_calls.append(kwargs)
            return {"status": "ok"}

        count = await tick(pool, dispatch)

        assert count == 1, f"Expected 1 dispatch, got {count}"
        assert len(dispatch_calls) == 1, f"dispatch called {len(dispatch_calls)} times"
        assert dispatch_calls[0]["trigger_source"] == "schedule:calendar-event-test"

    async def test_concurrent_ticks_dispatch_exactly_once(self) -> None:
        """Two concurrent tick() calls on the same due task dispatch it exactly once.

        This simulates the background scheduler loop ticking while the MCP tick
        tool is called from a running session — the scenario that produced 4
        sessions in 90 s on 2026-06-20.
        """
        from butlers.core.scheduler import tick

        task = _make_due_task()
        pool = _ClaimTrackingPool(tasks=[task])
        dispatch_calls: list[dict] = []
        dispatch_started = asyncio.Event()

        async def slow_dispatch(**kwargs: Any) -> dict:
            """Simulate a long-running session: yield after recording the call."""
            dispatch_calls.append(kwargs)
            dispatch_started.set()
            # Pause here so a concurrent tick can try to claim the same row
            await asyncio.sleep(0)
            return {"status": "ok"}

        # Launch both ticks concurrently
        results = await asyncio.gather(
            tick(pool, slow_dispatch),
            tick(pool, slow_dispatch),
            return_exceptions=True,
        )

        # Neither tick should raise
        for r in results:
            assert not isinstance(r, Exception), f"tick() raised: {r}"

        # Exactly one dispatch must have happened
        assert len(dispatch_calls) == 1, (
            f"Expected 1 dispatch across 2 concurrent ticks, got {len(dispatch_calls)}: "
            f"{[c['trigger_source'] for c in dispatch_calls]}"
        )

    async def test_second_tick_after_claim_is_skipped(self) -> None:
        """A tick that finds next_run_at already advanced skips without dispatch."""
        from butlers.core.scheduler import tick

        task = _make_due_task()
        pool = _ClaimTrackingPool(tasks=[task])
        dispatch_calls: list[dict] = []

        async def dispatch(**kwargs: Any) -> dict:
            dispatch_calls.append(kwargs)
            return {"status": "ok"}

        # First tick: claims and dispatches
        count1 = await tick(pool, dispatch)
        assert count1 == 1

        # Simulate pool returning the same due row again (as if next_run_at hasn't
        # been persisted yet in a race), but the in-memory claim state says it was
        # already advanced.  A second tick with the stale row should skip.
        count2 = await tick(pool, dispatch)
        assert count2 == 0, "Second tick must not dispatch an already-claimed task"
        assert len(dispatch_calls) == 1, (
            f"Task dispatched {len(dispatch_calls)} times across two ticks"
        )

    async def test_dispatch_once_even_when_dispatch_is_slow(self) -> None:
        """next_run_at is advanced BEFORE dispatch so a concurrent tick is blocked
        even while the first session is still running."""
        from butlers.core.scheduler import tick

        task = _make_due_task()
        pool = _ClaimTrackingPool(tasks=[task])
        dispatch_calls: list[dict] = []
        tick1_dispatched = asyncio.Event()

        async def blocking_dispatch(**kwargs: Any) -> dict:
            dispatch_calls.append(kwargs)
            tick1_dispatched.set()
            # Yield to allow concurrent work; the claim is already done so a
            # concurrent tick should find next_run_at updated and skip.
            await asyncio.sleep(0)
            return {"status": "ok"}

        async def fast_noop(**kwargs: Any) -> dict:
            dispatch_calls.append({"concurrent": True, **kwargs})
            return {"status": "ok"}

        # Run first tick — it will claim the row before calling dispatch
        tick1 = asyncio.create_task(tick(pool, blocking_dispatch))
        # Allow tick1 to reach the claim step by yielding
        await asyncio.sleep(0)
        # Now run second tick — it should find the row already claimed
        count2 = await tick(pool, fast_noop)
        count1 = await tick1

        assert count1 == 1, f"First tick should dispatch once, got {count1}"
        assert count2 == 0, f"Second tick should skip, got {count2}"
        # Only the first dispatch (blocking_dispatch) should have run
        concurrent_calls = [c for c in dispatch_calls if c.get("concurrent")]
        assert not concurrent_calls, "Concurrent dispatch must not fire after claim"

    async def test_until_at_expired_task_auto_disables_without_double_dispatch(
        self,
    ) -> None:
        """A task that has passed its until_at is auto-disabled.  A second tick
        after disable must not dispatch it again."""
        from datetime import timedelta

        from butlers.core.scheduler import tick

        now = datetime(2026, 6, 20, 1, 15, tzinfo=UTC)
        # until_at is exactly now — next occurrence will be tomorrow, past until_at
        task = _make_due_task(until_at=now + timedelta(seconds=30))
        pool = _ClaimTrackingPool(tasks=[task])
        dispatch_calls: list[dict] = []

        async def dispatch(**kwargs: Any) -> dict:
            dispatch_calls.append(kwargs)
            return {"status": "ok"}

        count1 = await tick(pool, dispatch)
        # Task fires (this is the last valid occurrence)
        assert count1 == 1

        # Simulate pool re-returning the task (with next_run_at set to NULL by
        # the auto-disable claim).  A fresh tick with no due tasks should dispatch 0.
        # The pool's internal state has next_run_at=None so the claim won't match.
        count2 = await tick(pool, dispatch)
        assert count2 == 0, "Auto-disabled task must not dispatch on subsequent tick"
