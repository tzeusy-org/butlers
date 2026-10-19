"""The ``schedule_trigger`` tool tags a missing schedule with a machine-readable code (bu-ukf1u0).

The dashboard route maps the code to 404 instead of matching the error prose.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._scheduling import register_scheduling_tools

pytestmark = pytest.mark.unit


async def test_a_missing_schedule_replies_with_the_not_found_code_and_dispatches_nothing() -> None:
    registered: dict[str, object] = {}

    def core_tool(_group: str, **kwargs):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="switchboard"),
        _build_scheduler_runtime_context=AsyncMock(),
        _dispatch_scheduled_task=AsyncMock(),
    )
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=MagicMock(),
        butler_name="switchboard",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_scheduling_tools(ctx, SimpleNamespace(), core_tool)

    reply = await registered["schedule_trigger"](id="7d6f2ac2-6a86-4a0c-8f7c-1f1c9d3c3a11")

    assert reply["status"] == "error"
    assert reply["code"] == "SCHEDULE_NOT_FOUND"
    assert reply["error"] == "Schedule not found"
    daemon._dispatch_scheduled_task.assert_not_awaited()
