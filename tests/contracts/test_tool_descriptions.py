"""Every registered MCP tool advertises a description (bu-3osotc).

Strict providers reject a tool schema without one: GLM via OpenCode fails the
whole session with "tools[N]: function.description is required", so a single
undocumented handler takes a model out of rotation for every butler that
registers it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.tools import Tool

from butlers.config import load_config
from butlers.daemon import ButlerDaemon
from butlers.modules.registry import default_registry
from tests.contracts.test_tool_presentation_inventory import _CONFIG_OVERRIDES

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]


async def _drain_registration_tasks(before: set[asyncio.Task]) -> None:
    registration_tasks = asyncio.all_tasks() - before
    for task in registration_tasks:
        task.cancel()
    if registration_tasks:
        await asyncio.gather(*registration_tasks, return_exceptions=True)


async def _module_tools() -> list[Tool]:
    registry = default_registry()
    tools: list[Tool] = []
    for module_name in registry.available_modules:
        module = registry._modules[module_name]()
        config_schema = module.config_schema
        config = (
            config_schema(**_CONFIG_OVERRIDES.get(module_name, {}))
            if config_schema is not None
            else {}
        )
        butler_name = "relationship" if module_name == "memory" else "inventory"
        mcp = FastMCP(f"descriptions-{module_name}")
        db = SimpleNamespace(pool=AsyncMock(), schema="inventory", db_name="inventory")
        before = asyncio.all_tasks()
        await module.register_tools(mcp, config, db, butler_name=butler_name)
        await _drain_registration_tasks(before)
        tools.extend(await mcp.list_tools())
    return tools


async def _core_tools(butler_name: str) -> list[Tool]:
    """Register every core group through the daemon's real decorator path."""
    daemon = ButlerDaemon(_REPO_ROOT / "roster" / butler_name, db=SimpleNamespace(pool=AsyncMock()))
    daemon.config = load_config(_REPO_ROOT / "roster" / butler_name)
    daemon.config.runtime_seed = daemon.config.runtime_seed.model_copy(update={"core_groups": None})
    daemon.mcp = FastMCP(f"descriptions-core-{butler_name}")
    daemon.spawner = MagicMock()
    before = asyncio.all_tasks()
    daemon._register_core_tools()
    await _drain_registration_tasks(before)
    return await daemon.mcp.list_tools()


async def test_every_registered_tool_has_a_description() -> None:
    tools = await _module_tools()
    for butler_name in ("general", "switchboard", "messenger", "chronicler"):
        tools.extend(await _core_tools(butler_name))

    assert len(tools) > 100, "registration produced too few tools to be a real sweep"
    assert sorted({tool.name for tool in tools if not (tool.description or "").strip()}) == []
