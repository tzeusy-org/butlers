"""The merged tool-call list still carries what the Switchboard lanes branch on (bu-4mfhdq).

Persisted tool-call input is reduced to the capture allowlist, and that reduced list is also
``SpawnerResult.tool_calls``, which the pipeline reads. ``answer_question``'s ``scope`` and
``target`` must therefore be on the allowlist, or a system-scope decline or cancel is no longer
recognised. These tests drive the real ``_ToolCallLoggingMCP`` wrapper through a real fastmcp
Client, the real merge, and the real pipeline readers.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP

from butlers.core.spawner_tool_calls import _merge_tool_call_records
from butlers.core.tool_call_capture import (
    _runtime_session_id_var,
    consume_runtime_session_tool_calls,
    set_current_runtime_session_id,
)
from butlers.core_tools._dashboard_system_answer import extract_system_answer
from butlers.mcp_wrappers import _ToolCallLoggingMCP
from butlers.modules.pipeline import _extract_answer_question_calls, _extract_cannot_answer_calls

pytestmark = pytest.mark.unit

_SYSTEM = {"scope": "system", "question": "What did I spend last week?"}
_SPELLED = {"target": None, "system_tool": None, "system_args": None}
_DECLINE = {"status": "ok", "answered": False, "dead_letter_id": "dl-1"}
_CANCELLED = {"status": "cancelled", "answered": False, "cancelled": True}
_DOMAIN = {"status": "accepted", "butler": "health"}


async def _executed(kwargs: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    mcp = FastMCP("lanes")
    wrapped = _ToolCallLoggingMCP(mcp, "switchboard", module_name="core")

    @wrapped.tool()
    async def answer_question(
        scope: str,
        question: str,
        target: str | None = None,
        system_tool: str | None = None,
        system_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return result

    token = set_current_runtime_session_id("lanes-session")
    try:
        async with Client(mcp) as client:
            await client.call_tool("answer_question", kwargs)
    finally:
        _runtime_session_id_var.reset(token)
    return consume_runtime_session_tool_calls("lanes-session")


def _parser(kwargs: dict[str, Any], name: str = "mcp__switchboard__answer_question"):
    return [{"id": "toolu_1", "name": name, "input": dict(kwargs)}]


async def _merged(kwargs: dict[str, Any], result: dict[str, Any], *, parser_kwargs=None):
    executed = await _executed(kwargs, result)
    return _merge_tool_call_records(
        _parser(kwargs if parser_kwargs is None else parser_kwargs),
        executed,
        butler_name="switchboard",
    )


@pytest.mark.parametrize("extra", [{}, _SPELLED], ids=["defaults-omitted", "all-args-spelled"])
async def test_a_system_decline_is_still_the_terminal_decline_lane(extra) -> None:
    calls = await _merged({**_SYSTEM, **extra}, _DECLINE)

    assert len(calls) == 1
    assert _extract_cannot_answer_calls(calls) == (True, True, "dl-1")


@pytest.mark.parametrize("extra", [{}, _SPELLED], ids=["defaults-omitted", "all-args-spelled"])
async def test_a_system_cancel_is_still_reported_as_cancelled(extra) -> None:
    calls = await _merged({**_SYSTEM, **extra}, _CANCELLED)

    assert len(calls) == 1
    assert extract_system_answer(calls) == {"status": "cancelled"}


async def test_a_domain_answer_is_one_routed_row() -> None:
    kwargs = {"scope": "domain", "question": "Sleep last night?", "target": "health"}
    calls = await _merged(kwargs, _DOMAIN)

    assert len(calls) == 1
    assert _extract_answer_question_calls(calls) == (["health"], ["health"], [])


async def test_only_scope_and_target_are_persisted_not_the_question() -> None:
    calls = await _merged({**_SYSTEM, "target": "finance"}, _DECLINE)

    assert calls[0]["input"] == {"scope": "system", "target": "finance"}
    assert "What did I spend" not in repr(calls)


async def test_the_executed_capture_carries_scope_without_the_parser() -> None:
    """A daemon-only record (no parser counterpart) is as readable as a paired one."""
    executed = await _executed(_SYSTEM, _CANCELLED)

    assert executed[0]["input"] == {"scope": "system", "target": None}
    assert extract_system_answer(executed) == {"status": "cancelled"}
