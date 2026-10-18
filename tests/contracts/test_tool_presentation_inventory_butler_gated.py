"""Butler-name-gated tools must be classified too [bu-kx18qv].

``test_tool_presentation_inventory`` registers modules as one synthetic butler, so a tool
registered only for particular butler names (the Calendar response fence tool for
messenger and relationship, memory for relationship) could be missing from the checked-in
inventory without failing it. This sweep registers every module under every real butler
name and requires each registered tool to have an inventory entry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.modules.registry import default_registry
from tests.contracts.test_tool_presentation_inventory import _CONFIG_OVERRIDES

pytestmark = pytest.mark.contract

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


def _real_butler_names() -> list[str]:
    return sorted(path.parent.name for path in _ROSTER.glob("*/butler.toml"))


async def _registered_tools() -> dict[str, set[str]]:
    """``{tool name: {"module@butler", ...}}`` across every module and real butler name."""
    registry = default_registry()
    seen: dict[str, set[str]] = {}
    for module_name in registry.available_modules:
        module = registry._modules[module_name]()
        schema = module.config_schema
        config = schema(**_CONFIG_OVERRIDES.get(module_name, {})) if schema is not None else {}
        for butler_name in _real_butler_names():
            mcp = FastMCP(f"gated-{module_name}-{butler_name}")
            db = SimpleNamespace(pool=AsyncMock(), schema=butler_name, db_name=butler_name)
            before = asyncio.all_tasks()
            await module.register_tools(mcp, config, db, butler_name=butler_name)
            tasks = asyncio.all_tasks() - before
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for tool in await mcp.list_tools():
                seen.setdefault(tool.name, set()).add(f"{module_name}@{butler_name}")
    return seen


async def test_every_tool_registered_for_a_real_butler_has_an_inventory_entry() -> None:
    declared = {item.canonical_name for item in TOOL_PRESENTATION_INVENTORY}
    registered = await _registered_tools()

    missing = {name: sorted(where) for name, where in registered.items() if name not in declared}
    assert not missing, f"registered but unclassified in the presentation inventory: {missing}"


async def test_calendar_fence_tools_are_registered_for_their_owners_and_daemon_only() -> None:
    registered = await _registered_tools()
    by_name = {item.canonical_name: item for item in TOOL_PRESENTATION_INVENTORY}

    fence = registered["calendar_response_fence_status"]
    assert {"calendar@messenger", "calendar@relationship"} <= fence
    assert not any(owner.endswith("@general") for owner in fence)
    assert by_name["calendar_response_fence_status"].llm_presentable is False
    assert "calendar@relationship" in registered["calendar_respond"]
    assert by_name["calendar_respond"].llm_presentable is False
