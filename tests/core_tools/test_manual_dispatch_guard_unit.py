"""Manual dispatch paths do not run while a scheduler tick is running (bu-kcgfqb).

``schedule_trigger`` (behind ``POST /api/butlers/{name}/schedules/{id}/trigger``) and the
Chronicler day-close refresh both dispatch scheduled work outside ``tick()``. Without the
tick guard they could overlap a tick and send the same thing twice. With the pool's guard
held by a running tick they reply busy and dispatch nothing; with it free they dispatch as
before. A busy reply rather than a wait avoids queueing behind a tick that may itself be
waiting on the one-slot spawner semaphore.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.day_close_writer import DayCloseCacheWriteOutcome
from butlers.config import ButlerType
from butlers.core.tick_guard import BUSY_MESSAGE, exclusive_tick, run_forced_tick, tick_in_progress
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.core_tools._base import ToolContext
from butlers.core_tools._chronicler import register_chronicler_tools
from butlers.core_tools._scheduling import register_scheduling_tools

pytestmark = pytest.mark.unit

_TASK_ID = str(uuid.uuid4())


class _HeldTick:
    """Holds the pool's tick guard with a running guarded tick until released."""

    def __init__(self, pool: object) -> None:
        self._pool = pool
        self._started = asyncio.Event()
        self._release = asyncio.Event()
        self._task: asyncio.Task[int] | None = None

    async def __aenter__(self) -> _HeldTick:
        async def blocking_tick(pool: object) -> int:
            self._started.set()
            await self._release.wait()
            return 0

        self._task = asyncio.create_task(exclusive_tick(blocking_tick)(self._pool))
        await asyncio.wait_for(self._started.wait(), timeout=2)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self._release.set()
        assert self._task is not None
        await self._task
        assert not tick_in_progress(self._pool)


def _tool_registry() -> tuple[dict[str, Any], Any]:
    registered: dict[str, Any] = {}

    def core_tool(_group: str, **kwargs: Any):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    return registered, core_tool


def _schedule_trigger_tool(*, spawner: Any = None, dispatch_mode: str = "prompt"):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": uuid.UUID(_TASK_ID),
            "name": "insight-urgent",
            "dispatch_mode": dispatch_mode,
            "prompt": "send the urgent insight",
            "job_name": None,
            "job_args": None,
            "complexity": None,
            "timezone": "UTC",
        }
    )
    runtime_context = SimpleNamespace(prompt_hooks={}, completion_hooks={}, default_timezone="UTC")
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="switchboard"),
        _build_scheduler_runtime_context=AsyncMock(return_value=runtime_context),
        _dispatch_scheduled_task=AsyncMock(return_value={"status": "ok"}),
    )
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=spawner if spawner is not None else MagicMock(),
        butler_name="switchboard",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    registered, core_tool = _tool_registry()
    register_scheduling_tools(ctx, SimpleNamespace(), core_tool)
    return pool, daemon, registered["schedule_trigger"]


def _chronicler_refresh_tool():
    pool = AsyncMock()
    daemon = MagicMock()
    daemon._dispatch_scheduled_task = AsyncMock(return_value=MagicMock())
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=MagicMock(),
        butler_name="chronicler",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=MagicMock(),
    )
    registered, core_tool = _tool_registry()
    register_chronicler_tools(ctx, SimpleNamespace(), core_tool)
    return pool, daemon, registered["chronicler_day_close_refresh"]


# ---------------------------------------------------------------------------
# schedule_trigger
# ---------------------------------------------------------------------------


async def test_schedule_trigger_reports_busy_and_dispatches_nothing_during_a_tick() -> None:
    pool, daemon, trigger = _schedule_trigger_tool()

    async with _HeldTick(pool):
        reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)

    assert reply["status"] == "busy"
    assert reply["id"] == _TASK_ID
    daemon._dispatch_scheduled_task.assert_not_awaited()
    pool.execute.assert_not_awaited()  # no last_run_at / last_result written either


async def test_schedule_trigger_dispatches_as_before_when_no_tick_is_running() -> None:
    pool, daemon, trigger = _schedule_trigger_tool()

    reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)

    assert reply == {"id": _TASK_ID, "status": "triggered", "name": "insight-urgent"}
    daemon._dispatch_scheduled_task.assert_awaited_once()
    assert not tick_in_progress(pool)


async def test_schedule_trigger_holds_the_guard_while_it_dispatches() -> None:
    pool, daemon, trigger = _schedule_trigger_tool()
    seen: list[bool] = []

    async def dispatch(**kwargs: Any) -> dict:
        seen.append(tick_in_progress(pool))
        return {"status": "ok"}

    daemon._dispatch_scheduled_task = AsyncMock(side_effect=dispatch)

    await trigger(id=_TASK_ID)

    assert seen == [True]  # a tick arriving mid-dispatch would wait, not overlap


# ---------------------------------------------------------------------------
# Chronicler day-close refresh
# ---------------------------------------------------------------------------


async def test_chronicler_refresh_reports_busy_and_dispatches_nothing_during_a_tick() -> None:
    pool, daemon, refresh = _chronicler_refresh_tool()

    async with _HeldTick(pool):
        reply = await asyncio.wait_for(
            refresh(date_label="2026-09-03", timezone="Asia/Singapore"), timeout=2
        )

    assert reply["status"] == "error"
    assert reply["code"] == "scheduler_busy"
    daemon._dispatch_scheduled_task.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()


async def test_chronicler_refresh_dispatches_as_before_when_no_tick_is_running() -> None:
    pool, daemon, refresh = _chronicler_refresh_tool()
    built_at = datetime.now(UTC)
    pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            {"cache_built_at": built_at},
            {"covered_at": built_at},
        ]
    )

    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=DayCloseCacheWriteOutcome()),
    ):
        reply = await asyncio.wait_for(
            refresh(date_label="2026-09-03", timezone="Asia/Singapore"), timeout=2
        )

    assert reply["status"] == "success"
    daemon._dispatch_scheduled_task.assert_awaited_once()
    assert not tick_in_progress(pool)


# ---------------------------------------------------------------------------
# Busy wording, the id alias, and a manual dispatch as the guard holder
# ---------------------------------------------------------------------------


def test_the_busy_message_does_not_claim_a_tick_is_the_holder() -> None:
    assert "tick" not in BUSY_MESSAGE
    assert "scheduled work is already running" in BUSY_MESSAGE


async def test_the_busy_reply_echoes_the_task_id_alias() -> None:
    pool, _daemon, trigger = _schedule_trigger_tool()

    async with _HeldTick(pool):
        reply = await asyncio.wait_for(trigger(task_id=_TASK_ID), timeout=2)

    assert reply["status"] == "busy"
    assert reply["id"] == _TASK_ID


async def test_a_manual_dispatch_makes_a_second_trigger_and_a_forced_tick_busy() -> None:
    pool, daemon, trigger = _schedule_trigger_tool()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_dispatch(**kwargs: Any) -> dict:
        started.set()
        await release.wait()
        return {"status": "ok"}

    daemon._dispatch_scheduled_task = AsyncMock(side_effect=slow_dispatch)
    first = asyncio.create_task(trigger(id=_TASK_ID))
    await asyncio.wait_for(started.wait(), timeout=2)

    second = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)
    forced_ran: list[bool] = []

    async def forced_run() -> int:
        forced_ran.append(True)
        return 0

    forced = await asyncio.wait_for(run_forced_tick(pool, forced_run), timeout=2)

    assert second == {"id": _TASK_ID, "status": "busy", "message": BUSY_MESSAGE}
    assert forced == {"status": "busy", "dispatched": 0, "message": BUSY_MESSAGE}
    assert forced_ran == []
    assert daemon._dispatch_scheduled_task.await_count == 1
    release.set()
    assert (await first)["status"] == "triggered"
    assert not tick_in_progress(pool)


# ---------------------------------------------------------------------------
# A runtime session must not hold the guard while waiting for its own slot
# ---------------------------------------------------------------------------


def _full_spawner(slots: int = 1, taken: int = 1) -> Any:
    semaphore = asyncio.Semaphore(slots)
    for _ in range(taken):
        semaphore._value -= 1  # take a slot without awaiting, as running sessions do
    return SimpleNamespace(_session_semaphore=semaphore)


async def test_a_runtime_trigger_with_every_slot_taken_is_refused_before_the_guard() -> None:
    pool, daemon, trigger = _schedule_trigger_tool(spawner=_full_spawner())
    guard_seen: list[bool] = []
    original = pool.fetchrow

    async def spy(query: str, *args: Any) -> Any:
        guard_seen.append(tick_in_progress(pool))
        return await original(query, *args)

    pool.fetchrow = spy
    token = set_current_runtime_session_id("runtime-session")
    try:
        reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)
    finally:
        reset_current_runtime_session_id(token)

    assert reply["status"] == "error"
    assert reply["code"] == "SPAWNER_SLOTS_FULL"
    assert reply["id"] == _TASK_ID
    daemon._dispatch_scheduled_task.assert_not_awaited()
    assert guard_seen and not any(guard_seen)  # every read happened outside the guard
    assert not tick_in_progress(pool)


async def test_a_runtime_trigger_with_a_free_slot_is_not_refused() -> None:
    pool, daemon, trigger = _schedule_trigger_tool(spawner=_full_spawner(slots=2, taken=1))
    token = set_current_runtime_session_id("runtime-session")
    try:
        reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)
    finally:
        reset_current_runtime_session_id(token)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_a_dashboard_trigger_with_every_slot_taken_still_dispatches() -> None:
    # No runtime session: this is the operator path, which queues for a slot as before.
    pool, daemon, trigger = _schedule_trigger_tool(spawner=_full_spawner())

    reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_a_runtime_trigger_of_a_job_mode_schedule_is_not_refused() -> None:
    pool, daemon, trigger = _schedule_trigger_tool(spawner=_full_spawner(), dispatch_mode="job")
    token = set_current_runtime_session_id("runtime-session")
    try:
        reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)
    finally:
        reset_current_runtime_session_id(token)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()
