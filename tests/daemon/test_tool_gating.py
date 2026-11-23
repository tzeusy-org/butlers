"""Tests for module tool-call gating based on enabled/disabled state.

Covers:
- Disabled module tools return structured error; enabled tools execute normally
- Toggling enabled state takes effect immediately (no restart)
- Core tools (no module_runtime_states) are never gated
- Tool-to-module mapping populated after startup
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from butlers.daemon import (
    ButlerDaemon,
    ModuleRuntimeState,
    _SpanWrappingMCP,
)
from butlers.modules.base import Module
from butlers.testing.tool_host import host_tools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub modules
# ---------------------------------------------------------------------------


class _CalendarModule(Module):
    """Stub calendar module with two tools."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    name = "calendar"

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
        async def calendar_get_events() -> dict:
            calls.append("calendar_get_events")
            return {"events": []}

        @mcp.tool()
        async def calendar_check_availability() -> dict:
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

    name = "email"

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
        async def email_read_inbox() -> dict:
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


def _make_mock_pool(state_store: dict | None = None) -> AsyncMock:
    """Return a mock pool whose fetchval respects a simple in-memory state store."""
    store = state_store if state_store is not None else {}
    pool = AsyncMock()

    async def _runtime_row(query, *args):
        if query.lstrip().startswith("SELECT *") and "runtime_config" in query:
            return {
                "butler_name": args[0],
                "core_groups": None,
                "core_groups_narrowing_reason": None,
                "catalog_read_sensitivity": "normal",
                "max_concurrent": 3,
                "max_queued": 10,
                "tool_exposure_policy": "eager_filtered",
                "blind_spot_preamble_enabled": True,
                "seeded_at": None,
                "updated_at": None,
            }
        return None

    pool.fetchrow = AsyncMock(side_effect=_runtime_row)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock(return_value=None)

    transaction_depth = 0

    @asynccontextmanager
    async def _transaction():
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            yield
        finally:
            transaction_depth -= 1

    mock_conn.transaction = MagicMock(side_effect=_transaction)
    mock_conn.is_in_transaction = MagicMock(side_effect=lambda: transaction_depth > 0)

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    pool.acquire = MagicMock(side_effect=_acquire)

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


async def _host_with_modules(
    butler_dir: Path, *modules: Module, state_store: dict | None = None
) -> ButlerDaemon:
    """Register *modules*' tools on a DB-free host the way startup does, with no boot.

    The subject is tool gating, not the boot: the host registers the daemon's core
    tools, then the daemon's own ``_register_module_tools`` wraps each module's tools
    with the gate over the shared runtime-state map (bu-rvgzmf.2).
    """
    host = await host_tools(butler_dir, pool=_make_mock_pool(state_store))
    daemon = host.daemon
    daemon._modules = list(modules)
    daemon._module_runtime_states = {
        mod.name: ModuleRuntimeState(health="active", enabled=True) for mod in modules
    }
    await daemon._register_module_tools()
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
    """A butler's real MCP server, with module tools registered by the daemon."""

    async def test_tool_module_map_populated_and_modules_gated_independently(
        self, tmp_path: Path
    ) -> None:
        """Tool-module map populated; disabling one module does not affect other modules."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}, "email": {}})
        calendar, email = _CalendarModule(), _EmailModule()
        daemon = await _host_with_modules(butler_dir, calendar, email, state_store={})

        tool_map = daemon._tool_module_map
        assert tool_map["calendar_get_events"] == "calendar"
        assert tool_map["calendar_check_availability"] == "calendar"
        assert tool_map["email_read_inbox"] == "email"

        await daemon.set_module_enabled("calendar", False)

        # Calls go through the registered MCP tools, as a session's would.
        gated = await (await daemon.mcp.get_tool("calendar_get_events")).fn()
        assert gated["error"] == "module_disabled"
        assert calendar.calls == []
        assert await (await daemon.mcp.get_tool("email_read_inbox")).fn() == {"messages": []}
        assert email.calls == ["email_read_inbox"]
        assert daemon._module_runtime_states["email"].enabled is True

    async def test_gating_reflects_live_module_state(self, tmp_path: Path) -> None:
        """Toggling via set_module_enabled gates and un-gates the live tool, no restart."""
        butler_dir = _make_butler_toml(tmp_path, modules={"calendar": {}})
        calendar = _CalendarModule()
        daemon = await _host_with_modules(butler_dir, calendar, state_store={})
        tool = (await daemon.mcp.get_tool("calendar_get_events")).fn

        assert daemon.get_module_states()["calendar"].enabled is True
        await daemon.set_module_enabled("calendar", False)
        assert (await tool())["error"] == "module_disabled"

        await daemon.set_module_enabled("calendar", True)
        assert await tool() == {"events": []}
        assert calendar.calls == ["calendar_get_events"]
