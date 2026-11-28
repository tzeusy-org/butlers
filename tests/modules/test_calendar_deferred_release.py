"""The calendar module's side of deferred plan steps (bu-5ii4gj.11.1): wiring only."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = pytest.mark.unit


def _module():
    module = CalendarModule()
    module._db = SimpleNamespace(pool=object())
    return module


async def test_the_sweep_does_nothing_until_the_executor_is_wired():
    module = _module()
    with patch("butlers.modules.calendar.resolution_move.release_deferred") as release:
        assert await module._sweep_deferred_plan_steps() == 0
    release.assert_not_called()


async def test_the_sweep_hands_the_wired_executor_to_the_release():
    module = _module()
    execute = AsyncMock(return_value=True)
    module.set_approved_card_executor(execute)
    with patch(
        "butlers.modules.calendar.resolution_move.release_deferred",
        new_callable=AsyncMock,
        return_value=2,
    ) as release:
        assert await module._sweep_deferred_plan_steps(after="card") == 2
    (pool, run), kwargs = release.await_args
    assert pool is module._db.pool and kwargs == {"after": "card"}
    await run("card-id")  # the module re-runs only the tools it owns
    execute.assert_awaited_once_with("card-id", tool_names=frozenset({"calendar_update_event"}))


async def test_a_write_schedules_a_release_of_its_own_dependents_only():
    module = _module()
    module.set_approved_card_executor(AsyncMock())
    card = uuid.uuid4()
    swept: list[str | None] = []

    async def sweep(*, after=None):
        swept.append(after)
        return 1

    module._sweep_deferred_plan_steps = sweep  # type: ignore[method-assign]
    with patch("butlers.modules.calendar.asyncio.sleep", new=AsyncMock()):
        module.schedule_dependent_release(card)
        await asyncio.gather(*module._release_tasks)

    assert swept == [str(card)]


async def test_no_release_is_scheduled_without_the_executor():
    module = _module()
    module.schedule_dependent_release(uuid.uuid4())
    assert not module._release_tasks


async def test_shutdown_cancels_a_pending_release():
    module = _module()
    module.set_approved_card_executor(AsyncMock())
    with patch(
        "butlers.modules.calendar.asyncio.sleep",
        new=AsyncMock(side_effect=lambda _: asyncio.sleep(3600)),
    ):
        module.schedule_dependent_release(uuid.uuid4())
        (task,) = module._release_tasks
        await asyncio.sleep(0)
        await module.on_shutdown()
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled() and not module._release_tasks
