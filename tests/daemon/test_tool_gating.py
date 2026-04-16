"""Tests for module tool-call gating based on enabled/disabled state.

Covers:
- Disabled module tools return structured error; enabled tools execute normally
- Toggling enabled state takes effect immediately (no restart)
- Core tools (no module_runtime_states) are never gated
- Tool-to-module mapping populated after startup
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import (
    ButlerDaemon,
    ModuleRuntimeState,
    _SpanWrappingMCP,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub modules
# ---------------------------------------------------------------------------


class _CalendarModule(Module):
    """Stub calendar module with two tools."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def config_schema(self) -> type[BaseModel]:
        return BaseModel

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        calls = self.calls

        @mcp.tool()
        async def calendar_get_events(**kwargs: Any) -> dict:
            calls.append("calendar_get_events")
            return {"events": []}

        @mcp.tool()
        async def calendar_check_availability(**kwargs: Any) -> dict:
            calls.append("calendar_check_availability")
            return {"available": True}

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class _EmailModule(Module):
    """Stub email module with one tool."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "email"

    @property
    def config_schema(self) -> type[BaseModel]:
        return BaseModel

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        calls = self.calls

        @mcp.tool()
        async def email_read_inbox(**kwargs: Any) -> dict:
            calls.append("email_read_inbox")
            return {"messages": []}

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path, modules: dict | None = None) -> Path:
    modules = modules or {}
    lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_butler"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            lines.append(f"{k} = {v!r}")
    (tmp_path / "butler.toml").write_text("\n".join(lines))
    return tmp_path


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


def _make_mock_pool(state_store: dict | None = None) -> AsyncMock:
    """Return a mock pool whose fetchval respects a simple in-memory state store."""
    store = state_store if state_store is not None else {}
    pool = AsyncMock()

    async def _fetchval(sql, key, *args):
        if "SELECT value FROM state" in sql:
            val = store.get(key)
            return val
        return MagicMock()

    async def _execute(sql, key, *args):
        if "INSERT INTO state" in sql:
            value_str = args[0] if args else None
            if value_str is not None:
                store[key] = json.loads(value_str)
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _patch_infra(mock_pool: AsyncMock | None = None):
    if mock_pool is None:
        mock_pool = _make_mock_pool()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon(
    butler_dir: Path,
    registry: ModuleRegistry | None = None,
    state_store: dict | None = None,
) -> ButlerDaemon:
    """Helper: start a daemon with patched infrastructure."""
    mock_pool = _make_mock_pool(state_store)
    patches = _patch_infra(mock_pool)

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    return daemon


# ---------------------------------------------------------------------------
# Unit tests: _SpanWrappingMCP gating logic
# ---------------------------------------------------------------------------


class TestSpanWrappingMCPGating:
    """Tests for the gating layer inside _SpanWrappingMCP."""

    async def test_gating_and_live_toggle(self) -> None:
        """Disabled module returns module_disabled error; enabled executes normally; live toggle takes effect immediately."""
        runtime_states: dict[str, ModuleRuntimeState] = {
            "calendar": ModuleRuntimeState(health="active", enabled=False),
        }
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn

        proxy = _SpanWrappingMCP(
            mock_mcp,
            "test-butler",
            module_name="calendar",
            module_runtime_states=runtime_states,
        )
        call_count = 0

        @proxy.tool()
        async def calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": []}

        # Disabled: returns structured error, not called
        result = await calendar_get_events()
        assert result == {
            "error": "module_disabled",
            "module": "calendar",
            "message": "The calendar module is disabled. Enable it from the dashboard.",
        }
        assert call_count == 0

        # Enable: executes normally
        runtime_states["calendar"].enabled = True
        result2 = await calendar_get_events()
        assert result2 == {"events": []}
        assert call_count == 1

        # Disable again live: gates immediately
        runtime_states["calendar"].enabled = False
        result3 = await calendar_get_events()
        assert result3["error"] == "module_disabled"
        assert call_count == 1  # unchanged

    async def test_no_runtime_states_and_unknown_module_not_gated(self) -> None:
        """module_runtime_states=None and unknown module are both never gated."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn

        # No runtime states
        proxy = _SpanWrappingMCP(
            mock_mcp, "test-butler", module_name="calendar", module_runtime_states=None
        )
        call_count = 0

        @proxy.tool()
        async def tool_a(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"ok": True}

        result = await tool_a()
        assert result == {"ok": True}
        assert call_count == 1

        # Unknown module in empty states
        proxy2 = _SpanWrappingMCP(
            mock_mcp, "test-butler", module_name="calendar", module_runtime_states={}
        )
        call_count2 = 0

        @proxy2.tool()
        async def tool_b(**kwargs: Any) -> dict:
            nonlocal call_count2
            call_count2 += 1
            return {"ok": True}

        result2 = await tool_b()
        assert result2 == {"ok": True}
        assert call_count2 == 1


# ---------------------------------------------------------------------------
# Integration tests: ButlerDaemon with live module toggling
# ---------------------------------------------------------------------------


class TestDaemonToolGating:
    """Integration-level tests using a started ButlerDaemon."""

    async def test_tool_module_map_populated_and_modules_gated_independently(
        self, tmp_path: Path
    ) -> None:
        """Tool-module map populated; disabling one module does not affect other modules."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}, "email": {}})
        registry = _make_registry(_CalendarModule, _EmailModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        tool_map = daemon._tool_module_map
        assert tool_map["calendar_get_events"] == "calendar"
        assert tool_map["calendar_check_availability"] == "calendar"
        assert tool_map["email_read_inbox"] == "email"

        await daemon.set_module_enabled("calendar", False)
        assert daemon._module_runtime_states["calendar"].enabled is False
        assert daemon._module_runtime_states["email"].enabled is True

    async def test_gating_reflects_live_module_state(self, tmp_path: Path) -> None:
        """Disabling a module via set_module_enabled updates the shared state reference."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}})
        registry = _make_registry(_CalendarModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        assert states["calendar"].enabled is True

        await daemon.set_module_enabled("calendar", False)
        assert daemon._module_runtime_states["calendar"].enabled is False
