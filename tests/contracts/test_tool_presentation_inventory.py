"""Executable completeness gate for checked-in tool presentation metadata."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.config import ButlerType, load_config
from butlers.core.tool_catalog import validate_presentation_inventory
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.daemon import ButlerDaemon
from butlers.module_state import ModuleStartupStatus
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

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
            "custody.challenge",
            "custody.apply",
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


async def _core_catalog(butler_name: str, groups: tuple[str, ...]):
    daemon = ButlerDaemon(
        _REPO_ROOT / "roster" / butler_name,
        db=SimpleNamespace(pool=AsyncMock()),
    )
    daemon.config = load_config(_REPO_ROOT / "roster" / butler_name)
    daemon.config.runtime_seed = replace(daemon.config.runtime_seed, core_groups=groups)
    daemon.mcp = FastMCP(f"catalog-{butler_name}-{'-'.join(groups)}")
    daemon.spawner = MagicMock()
    before_tasks = asyncio.all_tasks()
    daemon._register_core_tools()
    registration_tasks = asyncio.all_tasks() - before_tasks
    for task in registration_tasks:
        task.cancel()
    if registration_tasks:
        await asyncio.gather(*registration_tasks, return_exceptions=True)
    daemon._collect_tool_metadata()
    return await daemon._finalize_tool_catalog()


@pytest.mark.parametrize(
    ("butler_name", "groups", "expected", "excluded"),
    (
        (
            "general",
            ("state",),
            {
                "route.execute",
                "cancel_session",
                "state_get",
                "state_set",
                "state_delete",
                "state_list",
            },
            {"notify", "ingest", "delegate_ask"},
        ),
        (
            "general",
            ("switchboard_routing",),
            {"route.execute", "cancel_session"},
            {"ingest", "connector.heartbeat", "route_to_butler"},
        ),
        (
            "switchboard",
            ("switchboard_routing",),
            {
                "custody.apply",
                "custody.challenge",
                "route.execute",
                "cancel_session",
                "answer_question",
                "cannot_answer",
                "connector.heartbeat",
                "file_bug_report",
                "ingest",
                "route_to_butler",
            },
            {"notify", "delegate_ask", "backfill.poll"},
        ),
        (
            "switchboard",
            ("delegation",),
            {"route.execute", "cancel_session", "custody.apply", "custody.challenge"},
            {"delegate_ask", "delegate_receive", "delegate_answer", "delegate_wake"},
        ),
    ),
)
async def test_actual_core_catalog_respects_group_name_and_type_gates(
    butler_name: str,
    groups: tuple[str, ...],
    expected: set[str],
    excluded: set[str],
) -> None:
    catalog = await _core_catalog(butler_name, groups)

    assert set(catalog) == expected
    assert excluded.isdisjoint(catalog)
    assert catalog.classification_complete is True


@pytest.mark.parametrize(
    ("module_name", "config_kwargs", "butler_name", "expected", "excluded"),
    (
        (
            "email",
            {"send_tools": False},
            "messenger",
            {"email_read_message", "email_search_inbox"},
            {"email_send_message", "email_reply_to_thread"},
        ),
        (
            "email",
            {"send_tools": True},
            "messenger",
            {
                "email_read_message",
                "email_search_inbox",
                "email_send_message",
                "email_reply_to_thread",
            },
            set(),
        ),
        (
            "whatsapp",
            {"send_tools": False},
            "messenger",
            set(),
            {"whatsapp_send_message", "whatsapp_reply_to_message"},
        ),
        (
            "whatsapp",
            {"send_tools": True},
            "messenger",
            {"whatsapp_send_message", "whatsapp_reply_to_message"},
            set(),
        ),
        (
            "home_assistant",
            {"read_only": True},
            "home",
            {
                "ha_get_entity_state",
                "ha_get_history",
                "ha_get_statistics",
                "ha_list_areas",
                "ha_list_entities",
                "ha_list_services",
                "ha_maintenance_list",
                "ha_render_template",
            },
            {"ha_call_service", "ha_activate_scene", "ha_maintenance_create"},
        ),
    ),
)
async def test_actual_module_catalog_respects_boolean_config_gates(
    module_name: str,
    config_kwargs: dict[str, object],
    butler_name: str,
    expected: set[str],
    excluded: set[str],
) -> None:
    registry = default_registry()
    module = registry._modules[module_name]()
    daemon = ButlerDaemon(
        _REPO_ROOT / "roster" / butler_name,
        db=SimpleNamespace(pool=AsyncMock(), schema=butler_name, db_name="butlers"),
    )
    daemon.config = load_config(_REPO_ROOT / "roster" / butler_name)
    daemon.mcp = FastMCP(f"catalog-{module_name}-{len(expected)}")
    daemon._modules = [module]
    daemon._module_configs = {module_name: module.config_schema(**config_kwargs)}

    await daemon._register_module_tools()
    daemon._collect_tool_metadata()
    catalog = await daemon._finalize_tool_catalog()

    assert set(catalog) == expected
    assert excluded.isdisjoint(catalog)
    assert catalog.classification_complete is True


async def test_actual_module_catalog_respects_groups_and_registration_failures() -> None:
    registry = default_registry()
    education = registry._modules["education"]()
    daemon = ButlerDaemon(
        _REPO_ROOT / "roster" / "education",
        db=SimpleNamespace(pool=AsyncMock(), schema="education", db_name="butlers"),
    )
    daemon.config = load_config(_REPO_ROOT / "roster" / "education")
    daemon.mcp = FastMCP("catalog-education-analytics")
    daemon._modules = [education]
    daemon._module_configs = {"education": education.config_schema(groups=["analytics"])}

    await daemon._register_module_tools()
    daemon._collect_tool_metadata()
    catalog = await daemon._finalize_tool_catalog()

    assert set(catalog) == {
        "analytics_get_cross_topic",
        "analytics_get_snapshot",
        "analytics_get_trend",
    }
    assert "mind_map_create" not in catalog
    assert catalog.classification_complete is True

    failed = ButlerDaemon(
        _REPO_ROOT / "roster" / "education",
        db=SimpleNamespace(pool=AsyncMock(), schema="education", db_name="butlers"),
    )
    failed.config = daemon.config
    failed.mcp = FastMCP("catalog-education-failed-startup")
    failed._modules = [education]
    failed._module_configs = daemon._module_configs
    failed._module_statuses["education"] = ModuleStartupStatus(
        status="failed", phase="startup", error="synthetic startup failure"
    )

    await failed._register_module_tools()
    failed._collect_tool_metadata()
    failed_catalog = await failed._finalize_tool_catalog()

    assert set(failed_catalog) == set()
    assert failed_catalog.classification_complete is True

    class PartialRegistrationModule:
        name = "partial"

        async def register_tools(self, mcp, config, db, butler_name):
            @mcp.tool()
            async def partial_registered() -> None:
                pass

            raise RuntimeError("synthetic registration failure")

        def tool_metadata(self):
            return {}

    partial_module = PartialRegistrationModule()
    partial = ButlerDaemon(
        _REPO_ROOT / "roster" / "education",
        db=SimpleNamespace(pool=AsyncMock(), schema="education", db_name="butlers"),
    )
    partial.config = daemon.config
    partial.mcp = FastMCP("catalog-partial-registration")
    partial._modules = [partial_module]
    partial._module_configs = {"partial": None}

    await partial._register_module_tools()
    partial._collect_tool_metadata()
    partial_catalog = await partial._finalize_tool_catalog()

    assert set(partial_catalog) == {"partial_registered"}
    assert partial._module_statuses["partial"].status == "failed"
    assert partial_catalog.classification_complete is False
