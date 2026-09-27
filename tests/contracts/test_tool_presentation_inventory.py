"""Executable completeness gate for checked-in tool presentation metadata."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from butlers.config import ButlerType
from butlers.core.tool_catalog import validate_presentation_inventory
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.modules.registry import default_registry
from tests.contracts.test_tool_surface_isolation import _record_core_registrations

pytestmark = pytest.mark.contract

_CONFIG_OVERRIDES = {
    "calendar": {"provider": "google"},
    "contacts": {"provider": "google"},
    "email": {"send_tools": True},
    "metrics": {"prometheus_query_url": "http://example.invalid"},
    "spotify": {"playback_tools": True},
    "whatsapp": {"send_tools": True},
}


async def _module_registration_union() -> dict[str, str]:
    """Execute every discovered registrar through an actual FastMCP registry."""
    registry = default_registry()
    owners: dict[str, str] = {}
    for module_name in registry.available_modules:
        module = registry._modules[module_name]()
        config_schema = module.config_schema
        config = (
            config_schema(**_CONFIG_OVERRIDES.get(module_name, {}))
            if config_schema is not None
            else {}
        )
        butler_names = ("relationship",) if module_name == "memory" else ("inventory",)
        for butler_name in butler_names:
            mcp = FastMCP(f"inventory-{module_name}-{butler_name}")
            db = SimpleNamespace(pool=AsyncMock(), schema="inventory", db_name="inventory")
            before_tasks = asyncio.all_tasks()
            await module.register_tools(mcp, config, db, butler_name=butler_name)
            registration_tasks = asyncio.all_tasks() - before_tasks
            for task in registration_tasks:
                task.cancel()
            if registration_tasks:
                await asyncio.gather(*registration_tasks, return_exceptions=True)
            for tool in await mcp.list_tools():
                existing = owners.setdefault(tool.name, module_name)
                assert existing == module_name, (
                    f"canonical tool {tool.name!r} has multiple module owners: "
                    f"{existing!r}, {module_name!r}"
                )
    return owners


async def test_checked_in_inventory_equals_executable_provider_union() -> None:
    core_variants = (
        ("general", ButlerType.BUTLER),
        ("switchboard", ButlerType.STAFFER),
        ("messenger", ButlerType.STAFFER),
        ("chronicler", ButlerType.BUTLER),
    )
    owners = await _module_registration_union()
    core_groups: dict[str, str | None] = {}
    for butler_name, butler_type in core_variants:
        for tool_name, group in await _record_core_registrations(butler_name, butler_type):
            existing = owners.setdefault(tool_name, "core")
            assert existing == "core", (
                f"canonical tool {tool_name!r} is owned by core and {existing!r}"
            )
            core_groups[tool_name] = group

    validate_presentation_inventory(owners, TOOL_PRESENTATION_INVENTORY)
    by_name = {item.canonical_name: item for item in TOOL_PRESENTATION_INVENTORY}
    assert all(
        by_name[name].group_name == (group if group is not None else "direct")
        for name, group in core_groups.items()
    )
    assert all(
        not by_name[name].llm_presentable
        for name in {
            "route.execute",
            "cancel_session",
            "tick",
            "shutdown",
            "delegate_wake",
            "receive_domain_event",
            "ingest",
            "connector.heartbeat",
            "backfill.poll",
            "module.set_enabled",
            "pipeline.process",
        }
    )
    assert all(
        by_name[name].llm_presentable
        for name in {
            "notify",
            "state_get",
            "schedule_create",
            "memory_access",
            "get_attachment",
            "deadline_create",
            "delegate_ask",
            "publish_event",
        }
    )
