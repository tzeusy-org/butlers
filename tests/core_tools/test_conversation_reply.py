"""Tests for the ``conversation_reply`` core MCP tool (bu-p6ey8.1).

Covers:
- Invalid conversation_id / missing pool degrade to an actionable error dict
  (never raise — the model must be able to see and correct its own mistake).
- Successful reply persists via conversation_reply_create() and returns
  status=ok with the created message id.
- A stale/hallucinated conversation_id (conversation_reply_create returns
  None) and an unexpected persistence failure both surface as status=error.
- _best_effort_request_id() degrades to None when routing context is
  missing or malformed, without raising.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.core_tools._base import ToolContext

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_and_grab(pool=None, butler_name="finance"):
    """Register conversation_reply on a minimal ctx and return the tool function."""
    import butlers.core_tools._conversation_reply as mod

    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    # tool_span wraps the function in a real OTel span; bypass it in tests
    # (established pattern — see tests/core_tools/test_infra_status.py).
    original_tool_span = mod.tool_span
    mod.tool_span = lambda *_a, **_kw: lambda fn: fn
    try:
        ctx = ToolContext(
            daemon=SimpleNamespace(),
            pool=pool,
            spawner=None,
            butler_name=butler_name,
            butler_type=None,
            is_switchboard=False,
            is_messenger=False,
            route_metrics=None,
        )
        mcp = SimpleNamespace()
        mod.register_conversation_reply_tool(ctx, mcp, _core_tool)
    finally:
        mod.tool_span = original_tool_span

    return registered["conversation_reply"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_conversation_reply_rejects_invalid_uuid():
    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(conversation_id="not-a-uuid", message="hi")

    assert result["status"] == "error"
    assert "not a valid UUID" in result["error"]


async def test_conversation_reply_errors_when_pool_unavailable():
    tool = _register_and_grab(pool=None)

    result = await tool(conversation_id=str(uuid4()), message="hi")

    assert result["status"] == "error"
    assert "Database pool" in result["error"]


# ---------------------------------------------------------------------------
# Success / not-found / persistence-failure paths
# ---------------------------------------------------------------------------


async def test_conversation_reply_persists_and_returns_ok(monkeypatch):
    conv_id = uuid4()
    message_id = uuid4()
    fake_create = AsyncMock(return_value={"id": message_id, "role": "assistant"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_create)

    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(conversation_id=str(conv_id), message="Recorded — correct?")

    assert result == {
        "status": "ok",
        "message_id": str(message_id),
        "conversation_id": str(conv_id),
    }
    fake_create.assert_awaited_once()
    assert fake_create.await_args.args[1] == conv_id
    assert fake_create.await_args.kwargs["message"] == "Recorded — correct?"


async def test_conversation_reply_errors_when_conversation_missing(monkeypatch):
    fake_create = AsyncMock(return_value=None)
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_create)

    tool = _register_and_grab(pool=AsyncMock())
    conv_id = uuid4()

    result = await tool(conversation_id=str(conv_id), message="hi")

    assert result["status"] == "error"
    assert str(conv_id) in result["error"]


async def test_conversation_reply_errors_when_persistence_raises(monkeypatch):
    fake_create = AsyncMock(side_effect=RuntimeError("connection reset"))
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_create)

    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(conversation_id=str(uuid4()), message="hi")

    assert result["status"] == "error"
    assert "connection reset" in result["error"]


# ---------------------------------------------------------------------------
# sources — answer-lane citation requirement (bu-0ynlk.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sources", [[], [""], ["   "]])
async def test_empty_or_blank_sources_are_rejected_with_guidance(sources):
    """An answer-lane reply claiming sources but providing none must error —
    an unsourced 'answer' is indistinguishable from a fabricated one."""
    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(conversation_id=str(uuid4()), message="The answer is 42", sources=sources)

    assert result["status"] == "error"
    assert "sources must contain only non-empty names" in result["error"]
    assert "decline" in result["error"]


async def test_omitted_sources_is_unaffected(monkeypatch):
    """Confirm-loop/action-proposal replies never pass sources — the default
    None must not trigger the answer-lane citation requirement."""
    fake_create = AsyncMock(return_value={"id": uuid4(), "role": "assistant"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_create)

    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(conversation_id=str(uuid4()), message="Recorded — correct?")

    assert result["status"] == "ok"
    assert fake_create.await_args.kwargs["sources"] is None


async def test_non_empty_sources_persists(monkeypatch):
    conv_id = uuid4()
    message_id = uuid4()
    fake_create = AsyncMock(return_value={"id": message_id, "role": "assistant"})
    monkeypatch.setattr("butlers.api.conversations.conversation_reply_create", fake_create)

    tool = _register_and_grab(pool=AsyncMock())

    result = await tool(
        conversation_id=str(conv_id),
        message="You spent $312 on groceries this month.",
        sources=["finance.get_budget", "transaction#a1b2c3"],
    )

    assert result == {
        "status": "ok",
        "message_id": str(message_id),
        "conversation_id": str(conv_id),
    }
    fake_create.assert_awaited_once()
    assert fake_create.await_args.kwargs["sources"] == ["finance.get_budget", "transaction#a1b2c3"]


# ---------------------------------------------------------------------------
# _best_effort_request_id — ambient routing-context recovery, best-effort only
# ---------------------------------------------------------------------------


async def test_best_effort_request_id_returns_none_without_context(monkeypatch):
    from butlers.core_tools._conversation_reply import _best_effort_request_id

    monkeypatch.setattr(
        "butlers.core_tools._conversation_reply.get_current_runtime_session_routing_context",
        lambda: None,
    )

    assert _best_effort_request_id() is None


async def test_best_effort_request_id_recovers_valid_uuid(monkeypatch):
    from butlers.core_tools._conversation_reply import _best_effort_request_id

    request_id = uuid4()
    monkeypatch.setattr(
        "butlers.core_tools._conversation_reply.get_current_runtime_session_routing_context",
        lambda: {"request_id": str(request_id)},
    )

    assert _best_effort_request_id() == request_id


async def test_best_effort_request_id_degrades_on_malformed_value(monkeypatch):
    from butlers.core_tools._conversation_reply import _best_effort_request_id

    monkeypatch.setattr(
        "butlers.core_tools._conversation_reply.get_current_runtime_session_routing_context",
        lambda: {"request_id": "not-a-uuid"},
    )

    assert _best_effort_request_id() is None
