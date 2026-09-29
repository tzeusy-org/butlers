"""Contract: HA person mapping has no MCP/runtime tool surface (home-assistant-person-mapping).

Mapping submission is a dashboard-only owner route. No registered tool may submit, read or
describe mappings, so private identifiers cannot flow through any LLM-visible registry.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from butlers.config import ButlerType
from butlers.modules.registry import default_registry
from tests.contracts.test_tool_presentation_inventory import _CONFIG_OVERRIDES
from tests.contracts.test_tool_surface_isolation import _record_core_registrations

pytestmark = pytest.mark.contract

_MAPPING_MARKERS = (
    "person_mapping",
    "person-mapping",
    "ha_person_id",
    "home_assistant_persons",
    "ha_person_mapping_receipts",
)


async def _module_tool_texts() -> dict[str, str]:
    """Register every discovered module into an actual FastMCP and render each tool."""
    registry = default_registry()
    texts: dict[str, str] = {}
    for module_name in registry.available_modules:
        module = registry._modules[module_name]()
        schema = module.config_schema
        config = schema(**_CONFIG_OVERRIDES.get(module_name, {})) if schema is not None else {}
        mcp = FastMCP(f"mapping-absence-{module_name}")
        db = SimpleNamespace(pool=AsyncMock(), schema="home", db_name="home")
        before = asyncio.all_tasks()
        await module.register_tools(mcp, config, db, butler_name="home")
        started = asyncio.all_tasks() - before
        for task in started:
            task.cancel()
        if started:
            await asyncio.gather(*started, return_exceptions=True)
        for tool in await mcp.list_tools():
            texts[tool.name] = json.dumps(
                [tool.name, tool.description, tool.parameters], default=str
            ).lower()
    return texts


async def test_actual_registries_expose_no_mapping_surface() -> None:
    module_tools = await _module_tool_texts()
    core_tools = {
        name
        for butler_name, butler_type in (
            ("home", ButlerType.BUTLER),
            ("switchboard", ButlerType.STAFFER),
        )
        for name, _group in await _record_core_registrations(butler_name, butler_type)
    }
    # Positive controls: both registries are populated, including Home Assistant tools.
    assert any(name.startswith("ha_") for name in module_tools), sorted(module_tools)
    assert {"notify", "state_get"} <= core_tools

    leaks = sorted(
        (name, marker)
        for name, text in [*module_tools.items(), *((n, n.lower()) for n in core_tools)]
        for marker in _MAPPING_MARKERS
        if marker in text
    )
    assert leaks == []
