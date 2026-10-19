"""The occurrence claim still stops a double dispatch across two daemon processes (bu-jlrqju).

Within one daemon, ``tick()`` is now serialized by the tick guard, so the concurrent-tick
tests in ``test_scheduler_dispatch_idempotency.py`` (which share one pool object) run one
after the other and no longer exercise the claim. Two processes sharing a database have
two distinct pools and no shared guard. This test simulates that: two distinct pool
objects over one shared task table, with a barrier so both ticks read the due row before
either claims it. Exactly one of them may dispatch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _SharedTable:
    """One ``scheduled_tasks`` row visible to every pool, with an atomic claim."""

    def __init__(self, task: dict) -> None:
        self.task = task
        self.next_run_at: datetime | None = task["next_run_at"]
        self.claims = 0
        self.reads = 0
        self.both_read = asyncio.Event()

    async def read_due(self) -> list[dict]:
        self.reads += 1
        if self.reads >= 2:
            self.both_read.set()
        # Neither tick may claim before both have read the same due row.
        await asyncio.wait_for(self.both_read.wait(), timeout=2)
        return [dict(self.task)]

    def claim(self, new_next_run_at: Any, expected: Any) -> bool:
        if self.next_run_at != expected:
            return False
        self.next_run_at = new_next_run_at
        self.claims += 1
        return True


class _ProcessPool:
    """The pool of one daemon process; the table behind it is shared."""

    def __init__(self, table: _SharedTable) -> None:
        self._table = table

    async def fetchval(self, query: str, *args: Any) -> object:
        if "information_schema.columns" in query:
            col = args[1] if len(args) > 1 else ""
            if isinstance(col, (list, tuple, set)):
                return [{"column_name": c} for c in col if c == "task_type"]
            return col in {"task_type", "until_at"}
        return False

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        if "information_schema.columns" in query:
            requested = set(args[1]) if len(args) > 1 else set()
            return [{"column_name": c} for c in requested if c == "task_type"]
        if "FROM scheduled_tasks" in query and "next_run_at <= $1" in query:
            return await self._table.read_due()
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        if "UPDATE scheduled_tasks" in query and "IS NOT DISTINCT FROM" in query:
            new_next = args[1] if len(args) >= 3 else None
            if self._table.claim(new_next, args[-1]):
                return {"id": args[0]}
        return None

    async def execute(self, query: str, *args: Any) -> str:
        return "OK"


def _due_task() -> dict:
    return {
        "id": "task-1",
        "name": "calendar-event-test",
        "cron": "15 1 * * *",
        "dispatch_mode": "prompt",
        "prompt": "Scheduled event: Pay bills.",
        "job_name": None,
        "job_args": None,
        "complexity": None,
        "timezone": "UTC",
        "next_run_at": datetime.now(UTC),
        "until_at": None,
        "max_token_budget": None,
    }


async def test_two_processes_ticking_one_due_occurrence_dispatch_it_once() -> None:
    from butlers.core.scheduler import tick

    table = _SharedTable(_due_task())
    process_a, process_b = _ProcessPool(table), _ProcessPool(table)
    assert process_a is not process_b  # distinct pools: the tick guard does not link them
    dispatched: list[str] = []

    async def dispatch(**kwargs: Any) -> dict:
        dispatched.append(kwargs["trigger_source"])
        return {"status": "ok"}

    counts = await asyncio.wait_for(
        asyncio.gather(tick(process_a, dispatch), tick(process_b, dispatch)), timeout=5
    )

    assert table.reads == 2, "both ticks must have read the due row before either claimed it"
    assert table.claims == 1
    assert dispatched == ["schedule:calendar-event-test"]
    assert sorted(counts) == [0, 1]
