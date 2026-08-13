"""Tests for the General butler's situational context-bus MCP tools (RFC 0009).

Covers ``check_context`` / ``set_context`` / ``clear_context`` as registered by
``roster/general/modules/tools.py::register_tools`` — the explicit,
user-initiated counterpart to the deterministic context-bus producers
(bu-hmdqz.15). These wrap ``butlers.context_bus`` and were added without
direct test coverage; this file closes that gap using the same
fake-``mcp.tool()`` registration harness used elsewhere in the test suite
(see ``tests/modules/test_home_maintenance_tools.py``).

Covers:
- Tool registration: check_context/set_context/clear_context registered.
- check_context: serializes ContextEntry rows to plain dicts.
- set_context: hours -> expires_at conversion (including hours=None passthrough);
  always writes as butler_name="general" at confidence 1.0; propagates
  context_bus validation errors (invalid signal type).
- clear_context: always clears as butler_name="general".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_general import GeneralModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


@pytest.fixture
async def registered_tools() -> dict[str, Any]:
    """A GeneralModule wired to a mock pool, with tools registered on a fake mcp."""
    module = GeneralModule()
    db = MagicMock()
    db.pool = MagicMock()
    mcp = _make_mcp()
    await module.register_tools(mcp, config={}, db=db, butler_name="general")
    return mcp._registered_tools


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


async def test_context_tools_registered(registered_tools: dict[str, Any]) -> None:
    for name in ["check_context", "set_context", "clear_context"]:
        assert name in registered_tools


async def test_dnd_request_context_is_injected_and_correlation_is_not_a_tool_argument() -> None:
    """FastMCP injects context while exposing only the opaque replay UUID."""
    from butlers.modules._roster_general.tools import register_tools

    mcp = FastMCP("general-dnd-context-test")
    register_tools(mcp, SimpleNamespace(_get_pool=lambda: MagicMock()))

    set_tool = await mcp.get_tool("set_context")
    clear_tool = await mcp.get_tool("clear_context")

    assert set_tool is not None
    assert clear_tool is not None
    assert "ctx" not in set_tool.parameters["properties"]
    assert "ctx" not in clear_tool.parameters["properties"]
    assert "requested_expires_at" in set_tool.parameters["properties"]
    assert "mutation_id" in set_tool.parameters["properties"]
    assert "correlation_id" not in set_tool.parameters["properties"]
    assert "mutation_id" in clear_tool.parameters["properties"]
    assert "correlation_id" not in clear_tool.parameters["properties"]
    assert "uuid" in str(set_tool.parameters["properties"]["mutation_id"]).lower()
    assert "uuid" in str(clear_tool.parameters["properties"]["mutation_id"]).lower()


# ---------------------------------------------------------------------------
# check_context
# ---------------------------------------------------------------------------


async def test_check_context_serializes_active_signals(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    entry = ctx.ContextEntry(
        signal_type="traveling",
        value="Tokyo",
        set_by_butler="travel",
        set_at=now,
        expires_at=now,
        confidence=1.0,
        metadata={"source": "travel_trip"},
    )
    monkeypatch.setattr(ctx, "get_active_context", AsyncMock(return_value=[entry]))

    result = await registered_tools["check_context"]()

    assert result == [
        {
            "signal_type": "traveling",
            "value": "Tokyo",
            "set_by_butler": "travel",
            "set_at": now.isoformat(),
            "expires_at": now.isoformat(),
            "confidence": 1.0,
        }
    ]


async def test_check_context_empty_when_no_active_signals(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    monkeypatch.setattr(ctx, "get_active_context", AsyncMock(return_value=[]))

    assert await registered_tools["check_context"]() == []


# ---------------------------------------------------------------------------
# set_context
# ---------------------------------------------------------------------------


async def test_set_context_converts_hours_to_expires_at(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)

    before = datetime.now(UTC)
    result = await registered_tools["set_context"](
        signal_type="focused", value="focus time", hours=2
    )
    after = datetime.now(UTC)

    assert result == {"status": "set", "signal_type": "focused", "value": "focus time"}
    mock_set.assert_awaited_once()
    kwargs = mock_set.await_args.kwargs
    assert kwargs["butler_name"] == "general"
    assert kwargs["signal_type"] == "focused"
    assert kwargs["value"] == "focus time"
    assert kwargs["confidence"] == 1.0
    expires_at = kwargs["expires_at"]
    assert before + timedelta(hours=2) <= expires_at <= after + timedelta(hours=2)


async def test_set_context_no_hours_passes_none_expiry(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    """No hours override -> expires_at=None, so context_bus applies the signal's default TTL."""
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)

    await registered_tools["set_context"](signal_type="sick")

    kwargs = mock_set.await_args.kwargs
    assert kwargs["expires_at"] is None
    assert kwargs["butler_name"] == "general"
    assert kwargs["signal_type"] == "sick"


async def test_dnd_set_derives_opaque_action_identity_without_forwarding_request_text(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    """Raw request/DND text cannot be forwarded into the durable receipt path."""
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)

    class RequestContext:
        origin_request_id = "raw user DND text: silence everything"

    await registered_tools["set_context"](
        signal_type="dnd",
        value="do not store this raw DND text",
        ctx=RequestContext(),
    )

    kwargs = mock_set.await_args.kwargs
    assert kwargs["mutation_id"] == UUID("d3a666f5-e23b-5079-892e-5b55b3ef4e32")
    assert "correlation_id" not in kwargs
    assert "raw user DND text" not in str(kwargs)


async def test_dnd_tool_rejects_caller_supplied_correlation_but_preserves_uuid_replay(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    """A caller can replay with an opaque UUID, never free-form correlation text."""
    from butlers import context_bus as ctx

    with pytest.raises(TypeError, match="unexpected keyword argument 'correlation_id'"):
        await registered_tools["set_context"](
            signal_type="dnd",
            correlation_id="raw DND/user text",
        )
    mock_clear = AsyncMock()
    monkeypatch.setattr(ctx, "clear_context", mock_clear)
    mutation_id = UUID("33333333-3333-3333-3333-333333333333")
    await registered_tools["clear_context"](signal_type="dnd", mutation_id=mutation_id)
    assert mock_clear.await_args.kwargs["mutation_id"] == mutation_id


async def test_dnd_set_requires_stable_absolute_expiry_for_custom_ttl(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    """A retry must not regenerate its DND expiry from a relative duration."""
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)

    with pytest.raises(ValueError, match="requested_expires_at"):
        await registered_tools["set_context"](
            signal_type="dnd",
            hours=2,
        )

    mock_set.assert_not_awaited()


async def test_dnd_set_forwards_stable_requested_expiry(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)
    requested_expires_at = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)

    class RequestContext:
        origin_request_id = "mcp-request-absolute-expiry"

    await registered_tools["set_context"](
        signal_type="dnd",
        requested_expires_at=requested_expires_at,
        ctx=RequestContext(),
    )

    assert mock_set.await_args.kwargs["expires_at"] == requested_expires_at


async def test_dnd_set_derives_identity_from_the_mcp_request_not_dnd_content(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mock_set = AsyncMock()
    monkeypatch.setattr(ctx, "set_context", mock_set)

    class RequestContext:
        origin_request_id = "mcp-request-987"

    await registered_tools["set_context"](
        signal_type="dnd",
        value="the same raw payload must not choose the ID",
        ctx=RequestContext(),
    )

    kwargs = mock_set.await_args.kwargs
    assert kwargs["mutation_id"] == UUID("503275c1-fb50-5390-a862-d9f40efac976")
    assert "correlation_id" not in kwargs


async def test_dnd_clear_derives_stable_action_identity(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mock_clear = AsyncMock()
    monkeypatch.setattr(ctx, "clear_context", mock_clear)

    class RequestContext:
        origin_request_id = "mcp-clear-987"

    await registered_tools["clear_context"](
        signal_type="dnd",
        ctx=RequestContext(),
    )

    kwargs = mock_clear.await_args.kwargs
    assert kwargs["mutation_id"] == UUID("2e6988c8-9955-5fe8-87be-0b7ad0c6b5e0")
    assert "correlation_id" not in kwargs


async def test_dnd_tool_returns_content_minimizing_durable_receipt(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mutation_id = UUID("55555555-5555-5555-5555-555555555555")
    committed_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        ctx,
        "set_context",
        AsyncMock(
            return_value=ctx.DndMutationReceipt(
                mutation_id=mutation_id,
                generation=9,
                writer="general",
                operation="set",
                correlation_id="dnd-action:55555555-5555-5555-5555-555555555555",
                requested_expires_at=None,
                effective_expires_at=committed_at + timedelta(hours=2),
                committed_at=committed_at,
            )
        ),
    )

    class RequestContext:
        origin_request_id = "receipt-request"

    result = await registered_tools["set_context"](
        signal_type="dnd",
        value="do not echo this DND payload",
        ctx=RequestContext(),
    )

    assert result["mutation"] == {
        "mutation_id": str(mutation_id),
        "generation": 9,
        "writer": "general",
        "operation": "set",
        "correlation_id": "dnd-action:55555555-5555-5555-5555-555555555555",
        "requested_expires_at": None,
        "effective_expires_at": (committed_at + timedelta(hours=2)).isoformat(),
        "committed_at": committed_at.isoformat(),
    }
    assert "value" not in result["mutation"]
    assert "metadata" not in result["mutation"]
    assert "value" not in result


async def test_set_context_propagates_invalid_signal_type(
    registered_tools: dict[str, Any],
) -> None:
    """Vocabulary/permission validation is real (not mocked) — invalid type raises."""
    with pytest.raises(ValueError):
        await registered_tools["set_context"](signal_type="partying")


# ---------------------------------------------------------------------------
# clear_context
# ---------------------------------------------------------------------------


async def test_clear_context_scopes_to_general_butler(
    monkeypatch: pytest.MonkeyPatch, registered_tools: dict[str, Any]
) -> None:
    from butlers import context_bus as ctx

    mock_clear = AsyncMock()
    monkeypatch.setattr(ctx, "clear_context", mock_clear)

    result = await registered_tools["clear_context"](signal_type="focused")

    assert result == {"status": "cleared", "signal_type": "focused"}
    mock_clear.assert_awaited_once()
    kwargs = mock_clear.await_args.kwargs
    assert kwargs.get("butler_name") == "general"
    assert kwargs.get("signal_type") == "focused"
