"""The ``tick`` core tool goes through the per-daemon tick guard (bu-jlrqju).

Registered the way ``test_infra_trigger.py`` does. With the pool's guard held by a
running guarded tick, the tool must answer busy and must not call the scheduler.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.tick_guard import exclusive_tick, tick_in_progress
from butlers.core_tools._base import ToolContext
from butlers.core_tools._infra import register_infra_tools

pytestmark = pytest.mark.unit


def _register_tick(pool: object):
    registered: dict[str, object] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    runtime_context = SimpleNamespace(
        prompt_hooks=None, completion_hooks=None, default_timezone="UTC"
    )
    daemon = SimpleNamespace(
        _started_at=0.0,
        _check_health=lambda: None,
        _modules=[],
        _module_statuses={},
        config=SimpleNamespace(name="test", description="t", port=0),
        _build_scheduler_runtime_context=AsyncMock(return_value=runtime_context),
        _dispatch_scheduled_task=AsyncMock(),
    )
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=MagicMock(),
        butler_name="test",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_infra_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["tick"]


async def test_the_tick_tool_reports_busy_while_the_guard_is_held_and_runs_no_tick() -> None:
    pool = MagicMock()
    started, release = asyncio.Event(), asyncio.Event()

    async def blocking_tick(pool: object) -> int:
        started.set()
        await release.wait()
        return 0

    holder = asyncio.create_task(exclusive_tick(blocking_tick)(pool))
    await started.wait()
    scheduler_tick = AsyncMock(return_value=3)
    tick_tool = _register_tick(pool)

    with patch("butlers.core_tools._infra._tick", scheduler_tick):
        reply = await asyncio.wait_for(tick_tool(), timeout=2)

    assert reply["status"] == "busy"
    assert reply["dispatched"] == 0
    scheduler_tick.assert_not_awaited()
    release.set()
    await holder
    assert not tick_in_progress(pool)


async def test_the_tick_tool_runs_a_tick_when_idle_and_replies_with_the_bare_count() -> None:
    pool = MagicMock()
    scheduler_tick = AsyncMock(return_value=3)
    tick_tool = _register_tick(pool)

    with patch("butlers.core_tools._infra._tick", scheduler_tick):
        reply = await asyncio.wait_for(tick_tool(), timeout=2)

    assert reply == {"dispatched": 3}
    scheduler_tick.assert_awaited_once()
