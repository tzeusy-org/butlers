"""A runtime trigger is refused when the process-wide session cap is full (bu-kcgfqb C4b).

``Spawner.trigger`` takes the global semaphore before the per-butler one. In production the
global cap (5) binds before a butler's own (3), so a butler with a free slot can still have
its dispatch wait behind other butlers' sessions. The refusal must cover that case, or the
dispatch would hold the tick guard while waiting and stall the scheduler loop behind it.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerType
from butlers.core import spawner as spawner_module
from butlers.core.tick_guard import tick_in_progress
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.core_tools._base import ToolContext
from butlers.core_tools._scheduling import register_scheduling_tools

pytestmark = pytest.mark.unit

_TASK_ID = str(uuid.uuid4())


def _taken(slots: int, taken: int) -> asyncio.Semaphore:
    semaphore = asyncio.Semaphore(slots)
    for _ in range(taken):
        semaphore._value -= 1  # take a slot without awaiting, as running sessions do
    return semaphore


def _schedule_trigger_tool(spawner: Any):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": uuid.UUID(_TASK_ID),
            "name": "insight-urgent",
            "dispatch_mode": "prompt",
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
        spawner=spawner,
        butler_name="switchboard",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    registered: dict[str, Any] = {}

    def core_tool(_group: str, **kwargs: Any):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    register_scheduling_tools(ctx, SimpleNamespace(), core_tool)
    return pool, daemon, registered["schedule_trigger"]


async def _trigger_as_runtime_session(trigger: Any) -> dict:
    token = set_current_runtime_session_id("runtime-session")
    try:
        return await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)
    finally:
        reset_current_runtime_session_id(token)


async def test_a_full_global_cap_refuses_even_with_a_free_butler_slot() -> None:
    butler_spawner = SimpleNamespace(_session_semaphore=_taken(slots=3, taken=1))
    pool, daemon, trigger = _schedule_trigger_tool(butler_spawner)
    guard_seen: list[bool] = []
    original = pool.fetchrow

    async def spy(query: str, *args: Any) -> Any:
        guard_seen.append(tick_in_progress(pool))
        return await original(query, *args)

    pool.fetchrow = spy

    with patch.object(spawner_module, "_global_semaphore", _taken(slots=5, taken=5)):
        reply = await _trigger_as_runtime_session(trigger)

    assert reply["status"] == "error"
    assert reply["code"] == "SPAWNER_SLOTS_FULL"
    assert "fleet-wide" in reply["error"]
    daemon._dispatch_scheduled_task.assert_not_awaited()
    assert guard_seen and not any(guard_seen)
    assert not tick_in_progress(pool)


async def test_a_global_cap_with_room_does_not_refuse() -> None:
    butler_spawner = SimpleNamespace(_session_semaphore=_taken(slots=3, taken=1))
    _pool, daemon, trigger = _schedule_trigger_tool(butler_spawner)

    with patch.object(spawner_module, "_global_semaphore", _taken(slots=5, taken=4)):
        reply = await _trigger_as_runtime_session(trigger)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_an_unset_global_semaphore_is_not_full() -> None:
    butler_spawner = SimpleNamespace(_session_semaphore=_taken(slots=3, taken=1))
    _pool, daemon, trigger = _schedule_trigger_tool(butler_spawner)

    with patch.object(spawner_module, "_global_semaphore", None):
        reply = await _trigger_as_runtime_session(trigger)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_a_dashboard_trigger_with_a_full_global_cap_still_dispatches() -> None:
    _pool, daemon, trigger = _schedule_trigger_tool(MagicMock())

    with patch.object(spawner_module, "_global_semaphore", _taken(slots=1, taken=1)):
        reply = await asyncio.wait_for(trigger(id=_TASK_ID), timeout=2)

    assert reply["status"] == "triggered"
    daemon._dispatch_scheduled_task.assert_awaited_once()
