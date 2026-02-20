"""Tests for module tool-call gating based on enabled/disabled state.

Covers:
- Tools from disabled modules return structured error (not exception)
- Tools from enabled modules work normally
- Toggling a module's enabled state takes effect on next call (no restart)
- Tool-to-module mapping is accurate for all registered tools
- Core tools (non-module) are never gated
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
from butlers.modules.base import Module, ToolIODescriptor
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

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_calendar_get_events"),)

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_calendar_check_availability"),)

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return ()

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return ()

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        calls = self.calls

        @mcp.tool()
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            calls.append("user_calendar_get_events")
            return {"events": []}

        @mcp.tool()
        async def bot_calendar_check_availability(**kwargs: Any) -> dict:
            calls.append("bot_calendar_check_availability")
            return {"available": True}

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_email_read_inbox"),)

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return ()

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return ()

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return ()

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        calls = self.calls

        @mcp.tool()
        async def user_email_read_inbox(**kwargs: Any) -> dict:
            calls.append("user_email_read_inbox")
            return {"messages": []}

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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
        'name = "butler_test"',
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
    mock_db.db_name = "butler_test"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
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
        patches["validate_core_credentials"],
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

    async def test_disabled_module_tool_returns_structured_error(self) -> None:
        """A tool from a disabled module returns the module_disabled error dict."""
        runtime_states: dict[str, ModuleRuntimeState] = {
            "calendar": ModuleRuntimeState(health="active", enabled=False),
        }

        mock_mcp = MagicMock()
        # Make mock_mcp.tool() return a decorator that just wraps the fn.
        mock_mcp.tool.return_value = lambda fn: fn

        proxy = _SpanWrappingMCP(
            mock_mcp,
            "test-butler",
            module_name="calendar",
            module_runtime_states=runtime_states,
        )

        call_count = 0

        @proxy.tool()
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": []}

        # The decorator returns the instrumented wrapper; call it.
        result = await user_calendar_get_events()
        assert result == {
            "error": "module_disabled",
            "module": "calendar",
            "message": "The calendar module is disabled. Enable it from the dashboard.",
        }
        # The original function body must NOT have been called.
        assert call_count == 0

    async def test_enabled_module_tool_executes_normally(self) -> None:
        """A tool from an enabled module executes without gating."""
        runtime_states: dict[str, ModuleRuntimeState] = {
            "calendar": ModuleRuntimeState(health="active", enabled=True),
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
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": ["ev1"]}

        result = await user_calendar_get_events()
        assert result == {"events": ["ev1"]}
        assert call_count == 1

    async def test_no_runtime_states_means_no_gating(self) -> None:
        """When module_runtime_states is None, tools are never gated."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn

        proxy = _SpanWrappingMCP(
            mock_mcp,
            "test-butler",
            module_name="calendar",
            module_runtime_states=None,
        )

        call_count = 0

        @proxy.tool()
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": []}

        result = await user_calendar_get_events()
        assert result == {"events": []}
        assert call_count == 1

    async def test_toggle_disabled_to_enabled_takes_effect_immediately(self) -> None:
        """After toggling a module back to enabled, the next call executes normally."""
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
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": ["ev1"]}

        # First call: disabled → structured error.
        result = await user_calendar_get_events()
        assert result["error"] == "module_disabled"
        assert call_count == 0

        # Toggle enabled in the shared dict (simulates daemon.set_module_enabled).
        runtime_states["calendar"].enabled = True

        # Second call: now enabled → normal execution.
        result = await user_calendar_get_events()
        assert result == {"events": ["ev1"]}
        assert call_count == 1

    async def test_toggle_enabled_to_disabled_gates_next_call(self) -> None:
        """Disabling a module live prevents the next tool call from executing."""
        runtime_states: dict[str, ModuleRuntimeState] = {
            "calendar": ModuleRuntimeState(health="active", enabled=True),
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
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": ["ev1"]}

        # First call: enabled → normal.
        result = await user_calendar_get_events()
        assert result == {"events": ["ev1"]}
        assert call_count == 1

        # Toggle to disabled.
        runtime_states["calendar"].enabled = False

        # Second call: disabled → gated.
        result = await user_calendar_get_events()
        assert result["error"] == "module_disabled"
        assert call_count == 1  # unchanged

    async def test_error_dict_contains_correct_module_name(self) -> None:
        """The module_disabled error dict identifies the correct module name."""
        runtime_states: dict[str, ModuleRuntimeState] = {
            "email": ModuleRuntimeState(health="active", enabled=False),
        }

        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn

        proxy = _SpanWrappingMCP(
            mock_mcp,
            "test-butler",
            module_name="email",
            module_runtime_states=runtime_states,
        )

        @proxy.tool()
        async def user_email_read_inbox(**kwargs: Any) -> dict:
            return {"messages": []}

        result = await user_email_read_inbox()
        assert result["error"] == "module_disabled"
        assert result["module"] == "email"
        assert "email" in result["message"]
        assert "dashboard" in result["message"]

    async def test_unknown_module_in_states_is_not_gated(self) -> None:
        """If a module has no entry in runtime_states, its tools are not gated."""
        # runtime_states is empty — module "calendar" has no entry.
        runtime_states: dict[str, ModuleRuntimeState] = {}

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
        async def user_calendar_get_events(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"events": []}

        result = await user_calendar_get_events()
        assert result == {"events": []}
        assert call_count == 1


# ---------------------------------------------------------------------------
# Integration tests: ButlerDaemon with live module toggling
# ---------------------------------------------------------------------------


class TestDaemonToolGating:
    """Integration-level tests using a started ButlerDaemon."""

    async def test_tool_module_map_populated_after_startup(self, tmp_path: Path) -> None:
        """After startup, _tool_module_map contains all registered module tools."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}, "email": {}})
        registry = _make_registry(_CalendarModule, _EmailModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        tool_map = daemon._tool_module_map
        assert "user_calendar_get_events" in tool_map
        assert tool_map["user_calendar_get_events"] == "calendar"
        assert "bot_calendar_check_availability" in tool_map
        assert tool_map["bot_calendar_check_availability"] == "calendar"
        assert "user_email_read_inbox" in tool_map
        assert tool_map["user_email_read_inbox"] == "email"

    async def test_tool_module_map_empty_without_modules(self, tmp_path: Path) -> None:
        """With no modules registered, the tool_module_map is empty."""
        butler_dir = _make_butler_toml(tmp_path)
        # Use an empty registry (no modules) so no module tools are registered.
        empty_registry = ModuleRegistry()
        daemon = await _start_daemon(butler_dir, registry=empty_registry, state_store={})
        assert daemon._tool_module_map == {}

    async def test_gating_reflects_live_module_state(self, tmp_path: Path) -> None:
        """Disabling a module via set_module_enabled updates the shared state reference."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}})
        registry = _make_registry(_CalendarModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        # Initially enabled.
        states = daemon.get_module_states()
        assert states["calendar"].enabled is True

        # Disable via set_module_enabled (persists to state store).
        await daemon.set_module_enabled("calendar", False)

        # The _module_runtime_states dict (shared reference) now reflects disabled.
        assert daemon._module_runtime_states["calendar"].enabled is False

    async def test_multiple_modules_gated_independently(self, tmp_path: Path) -> None:
        """Disabling one module does not affect tools of other modules."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}, "email": {}})
        registry = _make_registry(_CalendarModule, _EmailModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        await daemon.set_module_enabled("calendar", False)

        # calendar disabled, email still enabled.
        assert daemon._module_runtime_states["calendar"].enabled is False
        assert daemon._module_runtime_states["email"].enabled is True

    async def test_tool_module_map_accurate_for_multiple_modules(self, tmp_path: Path) -> None:
        """Tool-to-module mapping is correct for all tools across multiple modules."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}, "email": {}})
        registry = _make_registry(_CalendarModule, _EmailModule)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        tool_map = daemon._tool_module_map
        # All calendar tools map to "calendar".
        calendar_tools = {k for k, v in tool_map.items() if v == "calendar"}
        assert "user_calendar_get_events" in calendar_tools
        assert "bot_calendar_check_availability" in calendar_tools

        # All email tools map to "email".
        email_tools = {k for k, v in tool_map.items() if v == "email"}
        assert "user_email_read_inbox" in email_tools


# ---------------------------------------------------------------------------
# Unit tests: gating does not affect tools with no module_runtime_states
# ---------------------------------------------------------------------------


class TestCoreToolsNotGated:
    """Verify that tools registered without module_runtime_states are never gated."""

    async def test_span_proxy_without_states_passes_all_calls(self) -> None:
        """_SpanWrappingMCP with no runtime_states never gates any tool."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn

        # No module_runtime_states passed — simulates core tool registration.
        proxy = _SpanWrappingMCP(
            mock_mcp,
            "test-butler",
            module_name="unknown",
            module_runtime_states=None,
        )

        call_count = 0

        @proxy.tool()
        async def some_core_tool(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"ok": True}

        result = await some_core_tool()
        assert result == {"ok": True}
        assert call_count == 1
