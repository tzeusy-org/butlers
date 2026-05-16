"""Regression tests for the infra ``trigger`` MCP tool.

Prior regression: the dashboard ``/api/butlers/{name}/trigger`` endpoint
forwards a ``complexity`` kwarg to the butler's MCP ``trigger`` tool, but
the tool signature did not accept ``complexity`` and pydantic rejected
the call. These tests pin the accepted kwargs and the forwarding behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from butlers.core.model_routing import Complexity
from butlers.core_tools._base import ToolContext
from butlers.core_tools._infra import register_infra_tools


class _FakeSpawner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def trigger(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output="ok", success=True, error=None, duration_ms=1)


def _register_and_grab_trigger():
    registered: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    spawner = _FakeSpawner()
    ctx = ToolContext(
        daemon=SimpleNamespace(
            _started_at=0.0,
            _check_health=lambda: None,
            _modules=[],
            _module_statuses={},
            config=SimpleNamespace(name="test", description="t", port=0),
        ),
        pool=None,
        spawner=spawner,
        butler_name="test",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_infra_tools(ctx, mcp, _core_tool)
    return registered["trigger"], spawner


async def test_trigger_accepts_complexity_kwarg():
    trigger, spawner = _register_and_grab_trigger()
    result = await trigger(prompt="hello", complexity="workhorse")
    assert result["success"] is True
    assert len(spawner.calls) == 1
    assert spawner.calls[0]["complexity"] is Complexity.WORKHORSE
    assert spawner.calls[0]["prompt"] == "hello"
    assert spawner.calls[0]["trigger_source"] == "trigger"


async def test_trigger_omits_complexity_when_not_supplied():
    trigger, spawner = _register_and_grab_trigger()
    await trigger(prompt="hello")
    assert "complexity" not in spawner.calls[0]


async def test_trigger_rejects_unknown_complexity():
    trigger, _spawner = _register_and_grab_trigger()
    with pytest.raises(ValueError):
        await trigger(prompt="hello", complexity="galactic")
