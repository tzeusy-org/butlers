"""drain() returns even when a finished task's discard callback has not run yet (bu-dhloi1.227).

On Python 3.12, ``asyncio.gather`` of futures that are all done completes
synchronously, so awaiting it does not yield to the event loop. A task that
finished in the same loop iteration drain() started in is still in
``_tasks``, because its ``discard`` done-callback only runs on a later
iteration. drain() then re-gathered it forever without yielding, and the
callback never got the chance to run (CI run 42600378964, a 300 s hang).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.goal_runner.runner import GoalRunner

pytestmark = pytest.mark.unit


def _runner() -> GoalRunner:
    host = MagicMock()
    host.terminate = AsyncMock()  # finishes in one step
    return GoalRunner(MagicMock(), host, catalog_pool=MagicMock(), sink=MagicMock())


async def test_drain_returns_when_a_done_task_still_awaits_its_discard(monkeypatch) -> None:
    runner = _runner()
    real_gather = asyncio.gather
    calls = 0

    def bounded_gather(*aws, **kwargs):
        # A spinning drain never yields, so no timeout could fire; fail instead.
        nonlocal calls
        calls += 1
        if calls > 50:
            raise AssertionError("drain() re-gathered done tasks without yielding")
        return real_gather(*aws, **kwargs)

    monkeypatch.setattr(asyncio, "gather", bounded_gather)

    runner._terminate_later(uuid4())  # a tracked task that finishes in one step
    await asyncio.sleep(0)  # it runs and finishes; its discard is queued behind us
    (task,) = runner._tasks
    assert task.done()  # finished, but its done-callback has not run yet

    await runner.drain()

    assert runner._tasks == set()
    assert calls <= 2


async def test_drain_waits_for_tasks_spawned_while_draining() -> None:
    runner = _runner()
    release = asyncio.Event()
    seen: list[str] = []

    async def second() -> None:
        seen.append("second")

    async def first() -> None:
        await release.wait()
        task = asyncio.create_task(second())
        runner._tasks.add(task)
        task.add_done_callback(runner._tasks.discard)
        seen.append("first")

    task = asyncio.create_task(first())
    runner._tasks.add(task)
    task.add_done_callback(runner._tasks.discard)
    asyncio.get_running_loop().call_soon(release.set)

    await runner.drain()

    assert seen == ["first", "second"]
    assert runner._tasks == set()
