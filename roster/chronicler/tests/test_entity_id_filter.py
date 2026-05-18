"""Tests for the entity_id filter on chronicler_list_episodes (bu-aqe7n, task 12.5).

Covers:
- MCP tool signature exposes entity_id parameter.
- Calling the tool with entity_id filters at the storage layer.
- entity_id=None (omitted) returns all episodes without filtering.
- Invalid UUID in entity_id propagates a ValueError.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from roster.chronicler.modules import ChroniclerModule

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTITY_ID = uuid4()


def _make_fake_mcp() -> tuple[Any, dict[str, Any]]:
    """Return a (FakeMCP, registered_tools) pair for capturing @mcp.tool decorations."""
    registered_tools: dict[str, Any] = {}

    class FakeMCP:
        def tool(self):
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

    return FakeMCP(), registered_tools


async def _register(module: ChroniclerModule, mcp: Any) -> None:
    await module.register_tools(mcp, config=None, db=module._db, butler_name="chronicler")


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------


def test_chronicler_list_episodes_tool_has_entity_id_param() -> None:
    """The MCP tool function registered in the chronicler module must accept entity_id."""
    mcp, registered_tools = _make_fake_mcp()

    module = ChroniclerModule()
    module._db = MagicMock()
    module._db.pool = MagicMock()

    asyncio.run(_register(module, mcp))

    assert "chronicler_list_episodes" in registered_tools, (
        "chronicler_list_episodes tool must be registered"
    )

    fn = registered_tools["chronicler_list_episodes"]
    sig = inspect.signature(fn)
    assert "entity_id" in sig.parameters, (
        "chronicler_list_episodes must accept entity_id parameter (bu-aqe7n)"
    )

    param = sig.parameters["entity_id"]
    # Default must be None (optional)
    assert param.default is None, "entity_id parameter must default to None (optional filter)"


# ---------------------------------------------------------------------------
# Filtering behaviour tests (via mocked storage)
# ---------------------------------------------------------------------------


async def test_list_episodes_passes_entity_id_uuid_to_storage() -> None:
    """When entity_id is provided, the tool converts it to UUID and passes to list_episodes."""
    mcp, registered_tools = _make_fake_mcp()

    module = ChroniclerModule()
    module._db = MagicMock()
    module._db.pool = MagicMock()

    await _register(module, mcp)

    tool = registered_tools["chronicler_list_episodes"]
    entity_id_str = str(_ENTITY_ID)

    with patch(
        "butlers.chronicler.storage.list_episodes",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = []

        await tool(entity_id=entity_id_str)

        mock_list.assert_awaited_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("entity_id") == UUID(entity_id_str), (
            "list_episodes must be called with entity_id as UUID, not string"
        )


async def test_list_episodes_passes_none_entity_id_when_omitted() -> None:
    """When entity_id is None, the tool passes entity_id=None to list_episodes."""
    mcp, registered_tools = _make_fake_mcp()

    module = ChroniclerModule()
    module._db = MagicMock()
    module._db.pool = MagicMock()

    await _register(module, mcp)

    tool = registered_tools["chronicler_list_episodes"]

    with patch(
        "butlers.chronicler.storage.list_episodes",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = []

        await tool()  # entity_id omitted

        mock_list.assert_awaited_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("entity_id") is None, (
            "list_episodes must be called with entity_id=None when param is omitted"
        )


async def test_list_episodes_invalid_entity_id_raises_value_error() -> None:
    """Passing a non-UUID string as entity_id must propagate a ValueError."""
    mcp, registered_tools = _make_fake_mcp()

    module = ChroniclerModule()
    module._db = MagicMock()
    module._db.pool = MagicMock()

    await _register(module, mcp)

    tool = registered_tools["chronicler_list_episodes"]

    with pytest.raises(ValueError):
        await tool(entity_id="not-a-uuid")
