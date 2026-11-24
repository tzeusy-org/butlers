"""Chronicler's service-only grant read, the target of ``location_retention.grants_ready``.

It returns only what ``ready_grant_payloads`` offers (grant ids, cutoffs, raw ids, keys and
digests of ready, unsettled grants) and takes no arguments, so the relay's caller chooses
nothing. It is declared ``presentable=False``.
"""

from __future__ import annotations

import inspect
from typing import Any

from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
from butlers.roster_imports import roster_module

_TOOL = "location_retention_grants_ready_service"


class _CapturingMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


_CONN = object()


class _Pool:
    def acquire(self):
        class _Ctx:
            async def __aenter__(self):
                return _CONN

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class _Db:
    pool = _Pool()


async def _registered() -> dict[str, Any]:
    chronicler = roster_module("chronicler", "modules")
    module = chronicler.ChroniclerModule()
    mcp = _CapturingMcp()
    await module.register_tools(mcp, None, _Db(), "chronicler")
    return mcp.tools


async def test_the_tool_returns_only_the_offered_payloads(monkeypatch) -> None:
    offered = [{"grant_id": "g", "cutoff": "c", "items": []}]
    seen: list[Any] = []

    async def fake_ready(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.append((conn, kwargs))
        return offered

    monkeypatch.setattr(
        "butlers.chronicler.location_retention_handoff.ready_grant_payloads", fake_ready
    )
    tools = await _registered()
    assert await tools[_TOOL]() == {"grants": offered}
    assert seen == [(_CONN, {})]


async def test_the_tool_takes_no_caller_choice() -> None:
    tools = await _registered()
    assert set(inspect.signature(tools[_TOOL]).parameters) <= {"trace_context"}


def test_the_tool_is_a_service_only_read() -> None:
    presentation = TOOL_PRESENTATION_BY_NAME[_TOOL]
    assert not presentation.llm_presentable
    assert presentation.effect_class == "read"
    assert presentation.write_stores == ()
