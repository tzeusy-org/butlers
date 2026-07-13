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
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    result = await registered_tools["set_context"](signal_type="dnd", value="focus time", hours=2)
    after = datetime.now(UTC)

    assert result == {"status": "set", "signal_type": "dnd", "value": "focus time"}
    mock_set.assert_awaited_once()
    kwargs = mock_set.await_args.kwargs
    assert kwargs["butler_name"] == "general"
    assert kwargs["signal_type"] == "dnd"
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

    result = await registered_tools["clear_context"](signal_type="dnd")

    assert result == {"status": "cleared", "signal_type": "dnd"}
    mock_clear.assert_awaited_once()
    kwargs = mock_clear.await_args.kwargs
    assert kwargs.get("butler_name") == "general"
    assert kwargs.get("signal_type") == "dnd"
