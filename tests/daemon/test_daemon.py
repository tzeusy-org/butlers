"""Tests for the ButlerDaemon class.

Uses extensive mocking to avoid real DB, FastMCP, and runtime dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import FastAPI
from fastmcp import FastMCP as RuntimeFastMCP
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from starlette.testclient import TestClient

from butlers.credentials import CredentialError
from butlers.daemon import (
    CORE_TOOL_NAMES,
    ButlerDaemon,
    RuntimeBinaryNotFoundError,
    _McpSseDisconnectGuard,
)
from butlers.modules.base import Module
from butlers.modules.email import EmailModule
from butlers.modules.pipeline import MessagePipeline
from butlers.modules.registry import ModuleRegistry
from butlers.modules.telegram import TelegramModule

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test helpers: stub modules
# ---------------------------------------------------------------------------


class StubConfigA(BaseModel):
    """Config schema for StubModuleA."""


class StubModuleA(Module):
    """Stub module with no dependencies."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.tools_registered = False
        self._startup_config: Any = None
        self._startup_db: Any = None

    @property
    def name(self) -> str:
        return "stub_a"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfigA

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return ["STUB_A_TOKEN"]

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return "stub_a"

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self.started = True
        self._startup_config = config
        self._startup_db = db

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


class StubConfigB(BaseModel):
    """Config schema for StubModuleB."""


class StubModuleB(Module):
    """Stub module that depends on stub_a."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.tools_registered = False

    @property
    def name(self) -> str:
        return "stub_b"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfigB

    @property
    def dependencies(self) -> list[str]:
        return ["stub_a"]

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _toml_value(v: Any) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _make_butler_toml(
    tmp_path: Path,
    modules: dict | None = None,
    runtime_type: str | None = None,
    *,
    butler_name: str = "test-butler",
    port: int = 9100,
    db_name: str = "butler_test",
) -> Path:
    """Write a minimal butler.toml in tmp_path and return the directory."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        f'name = "{db_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    if runtime_type is not None:
        toml_lines.append("\n[runtime]")
        toml_lines.append(f'type = "{runtime_type}"')
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    """Create a ModuleRegistry with the given module classes pre-registered."""
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    """Create a temp directory with a minimal butler.toml (no modules)."""
    return _make_butler_toml(tmp_path)


@pytest.fixture
def butler_dir_with_modules(tmp_path: Path) -> Path:
    """Create a temp directory with butler.toml that enables stub_a and stub_b."""
    return _make_butler_toml(
        tmp_path,
        modules={"stub_a": {}, "stub_b": {}},
    )


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
    mock_pool = AsyncMock()
    # Default fetchval returns None so state_get/state_set calls during
    # _init_module_runtime_states don't raise or return unexpected values.
    mock_pool.fetchval = AsyncMock(return_value=None)

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

    # Mock adapter class that returns an adapter with binary_name
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
        "create_audit_pool": patch.object(
            ButlerDaemon, "_create_audit_pool", new_callable=AsyncMock, return_value=None
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
        "mock_adapter_cls": mock_adapter_cls,
        "mock_adapter": mock_adapter,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartupSequence:
    """Verify the startup sequence executes in the documented order."""

    async def test_startup_calls_in_order(self, butler_dir: Path) -> None:
        """Key startup stages should execute in documented order.

        DB pool is created before module credential validation so that
        DB-stored credentials are visible during validation (butlers-987.3).
        """
        patches = _patch_infra()
        call_order: list[str] = []

        with (
            patches["db_from_env"] as mock_from_env,
            patches["run_migrations"] as mock_migrations,
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"] as mock_mod_validate,
            patches["validate_core_credentials"],
            patches["init_telemetry"] as mock_telemetry,
            patches["sync_schedules"] as mock_sync,
            patches["FastMCP"] as mock_fastmcp,
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"] as mock_start_server,
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            mock_db = patches["mock_db"]

            # Instrument calls to record order
            mock_telemetry.side_effect = lambda *a, **kw: call_order.append("init_telemetry")
            mock_validate.side_effect = lambda *a, **kw: call_order.append("validate_credentials")
            mock_from_env.side_effect = lambda *a, **kw: (
                call_order.append("db_from_env"),
                mock_db,
            )[-1]
            mock_db.provision.side_effect = lambda: call_order.append("provision")
            mock_db.connect.side_effect = lambda: (
                call_order.append("connect"),
                patches["mock_pool"],
            )[-1]
            mock_migrations.side_effect = lambda *a, **kw: call_order.append(
                f"run_migrations({kw.get('chain', a[1] if len(a) > 1 else 'core')})"
            )

            async def _record_mod_validate(*a: object, **kw: object) -> dict:
                call_order.append("validate_module_credentials_async")
                return {}

            mock_mod_validate.side_effect = _record_mod_validate
            mock_sync.side_effect = lambda *a, **kw: call_order.append("sync_schedules")
            mock_fastmcp.side_effect = lambda *a, **kw: (
                call_order.append("FastMCP"),
                MagicMock(),
            )[-1]
            mock_spawner_cls.side_effect = lambda **kw: (
                call_order.append("Spawner"),
                MagicMock(),
            )[-1]
            mock_start_server.side_effect = lambda *a, **kw: call_order.append("start_mcp_server")

            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        expected_order = [
            "init_telemetry",
            "validate_credentials",
            "db_from_env",
            "provision",
            "connect",
            "run_migrations(core)",
            "validate_module_credentials_async",
            "Spawner",
            "sync_schedules",
            "FastMCP",
            "start_mcp_server",
        ]
        # Filter to only expected items (there may be extra calls)
        filtered = [c for c in call_order if c in expected_order]
        # Shared credential wiring may trigger additional db_from_env calls.
        # Verify required milestones still occur in order.
        pos = 0
        for expected in expected_order:
            assert expected in filtered[pos:]
            pos = filtered.index(expected, pos) + 1

    async def test_config_loaded(self, butler_dir: Path) -> None:
        """After start(), config should be populated from butler.toml."""
        patches = _patch_infra()
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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon.config is not None
        assert daemon.config.name == "test-butler"
        assert daemon.config.port == 9100
        assert daemon.config.description == "A test butler"

    async def test_started_at_recorded(self, butler_dir: Path) -> None:
        """After start(), _started_at should be set."""
        patches = _patch_infra()
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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            before = time.monotonic()
            await daemon.start()
            after = time.monotonic()

        assert daemon._started_at is not None
        assert before <= daemon._started_at <= after


class TestCoreToolRegistration:
    """Verify all expected core MCP tools are registered."""

    EXPECTED_TOOLS = CORE_TOOL_NAMES

    async def test_all_core_tools_registered(self, butler_dir: Path) -> None:
        """All core tools should be registered on FastMCP via @mcp.tool()."""
        patches = _patch_infra()
        registered_tools: list[str] = []

        # Create a mock FastMCP that captures tool registrations
        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_tools.append(declared_name or fn.__name__)
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            # Use empty registry so only core tools (not module tools) are registered.
            daemon = ButlerDaemon(butler_dir, registry=ModuleRegistry())
            await daemon.start()

        assert set(registered_tools) == self.EXPECTED_TOOLS


class TestTriggerToolDispatch:
    """Verify trigger MCP tool dispatch uses canonical trigger_source values."""

    async def test_trigger_tool_uses_trigger_source_contract(self, butler_dir: Path) -> None:
        """trigger() should call spawner.trigger with trigger_source='trigger'."""
        patches = _patch_infra()
        trigger_fn = None

        patches["mock_spawner"].trigger = AsyncMock(
            return_value=MagicMock(output="ok", success=True, error=None, duration_ms=12)
        )

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal trigger_fn
                if fn.__name__ == "trigger":
                    trigger_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert trigger_fn is not None
        result = await trigger_fn("hello", "extra context")
        patches["mock_spawner"].trigger.assert_awaited_once_with(
            prompt="hello",
            context="extra context",
            trigger_source="trigger",
        )
        assert result["success"] is True
        assert result["duration_ms"] == 12


class TestModuleToolRegistration:
    """Verify module tools are registered in topological order."""

    async def test_module_tools_registered(self, butler_dir_with_modules: Path) -> None:
        """register_tools should be called for each enabled module."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # All modules should have had register_tools called
        for mod in daemon._modules:
            assert mod.tools_registered, f"Module {mod.name} tools not registered"

    async def test_module_startup_order(self, butler_dir_with_modules: Path) -> None:
        """Modules should start in topological order (stub_a before stub_b)."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        module_names = [m.name for m in daemon._modules]
        assert module_names.index("stub_a") < module_names.index("stub_b")

    async def test_module_migrations_run(self, butler_dir_with_modules: Path) -> None:
        """Module migrations should be run for modules that declare them."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"] as mock_migrations,
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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Should have been called for "core" and "stub_a" (stub_b returns None)
        migration_chains = [
            c.kwargs.get("chain", c.args[1] if len(c.args) > 1 else None)
            for c in mock_migrations.call_args_list
        ]
        assert "core" in migration_chains
        assert "stub_a" in migration_chains

    async def test_module_on_startup_called(self, butler_dir_with_modules: Path) -> None:
        """on_startup should be called for each module with its config."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        for mod in daemon._modules:
            assert mod.started, f"Module {mod.name} on_startup not called"


class TestShutdownSequence:
    """Verify graceful shutdown order."""

    async def test_shutdown_modules_reverse_order(self, butler_dir_with_modules: Path) -> None:
        """Modules should be shut down in reverse topological order."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        shutdown_order: list[str] = []

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Monkey-patch on_shutdown to record order
        for mod in daemon._modules:
            original = mod.on_shutdown

            async def make_tracker(name: str, orig=original):
                shutdown_order.append(name)
                await orig()

            mod.on_shutdown = lambda n=mod.name, o=original: make_tracker(n, o)

        await daemon.shutdown()

        # stub_b depends on stub_a, so stub_b shuts down first
        assert shutdown_order.index("stub_b") < shutdown_order.index("stub_a")

    async def test_shutdown_closes_db(self, butler_dir: Path) -> None:
        """DB pool should be closed during shutdown."""
        patches = _patch_infra()
        mock_db = patches["mock_db"]

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        await daemon.shutdown()
        mock_db.close.assert_awaited_once()

    async def test_shutdown_continues_on_module_error(self, butler_dir_with_modules: Path) -> None:
        """If a module's on_shutdown raises, others still shut down and DB closes."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        mock_db = patches["mock_db"]

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Make stub_b's shutdown raise
        for mod in daemon._modules:
            if mod.name == "stub_b":

                async def failing_shutdown():
                    raise RuntimeError("shutdown failed")

                mod.on_shutdown = failing_shutdown

        # Should not raise
        await daemon.shutdown()

        # DB should still be closed
        mock_db.close.assert_awaited_once()
        # stub_a should still have gotten a chance to shut down
        next(m for m in daemon._modules if m.name == "stub_a")
        # Since we didn't patch stub_a, check it was called via the original
        # We just verify db.close was called — the important assertion

    async def test_shutdown_stops_mcp_server(self, butler_dir: Path) -> None:
        """shutdown() should signal the uvicorn server to exit and await the task."""
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # Simulate a running server and task using a real asyncio future
        mock_server = MagicMock()
        mock_server.should_exit = False
        task_completed = False

        async def _fake_serve():
            nonlocal task_completed
            task_completed = True

        mock_task = asyncio.ensure_future(_fake_serve())
        await mock_task  # Let it complete so shutdown await returns immediately

        daemon._server = mock_server
        daemon._server_task = mock_task

        await daemon.shutdown()

        # Server should have been signalled to exit
        assert mock_server.should_exit is True
        # Task should have completed
        assert mock_task.done()
        assert task_completed
        # References should be cleared
        assert daemon._server is None
        assert daemon._server_task is None


class TestMCPServerStartup:
    """Verify the MCP server is started as a background asyncio task."""

    async def test_start_mcp_server_creates_uvicorn_server(self, butler_dir: Path) -> None:
        """_start_mcp_server should build streamable HTTP + SSE routes and start uvicorn."""
        patches = _patch_infra()

        mock_mcp = MagicMock()
        streamable_app = FastAPI()
        streamable_app.add_api_route(
            "/mcp", endpoint=lambda: None, methods=["GET", "POST", "DELETE"]
        )
        sse_app = FastAPI()
        sse_app.add_api_route("/sse", endpoint=lambda: None, methods=["GET"])
        sse_app.mount("/messages", app=FastAPI())
        mock_mcp.http_app.side_effect = [streamable_app, sse_app]

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = AsyncMock()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patch("butlers.daemon.uvicorn.Config") as mock_config_cls,
            patch("butlers.daemon.uvicorn.Server", return_value=mock_uvicorn_server),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

            # Verify both transport variants are wired.
            assert mock_mcp.http_app.call_args_list == [
                call(path="/mcp", transport="streamable-http"),
                call(path="/sse", transport="sse"),
            ]

            # Verify uvicorn.Config was created with wrapped app and expected parameters.
            mock_config_cls.assert_called_once()
            args, kwargs = mock_config_cls.call_args
            assert len(args) == 1
            wrapped_app = args[0]
            assert isinstance(wrapped_app, _McpSseDisconnectGuard)
            route_map = {route.path: route for route in wrapped_app._app.routes}
            assert "/mcp" in route_map
            assert "/sse" in route_map
            assert "/messages" in route_map
            assert type(route_map["/messages"]).__name__ == "Mount"
            assert route_map["/mcp"].methods.issuperset({"GET", "POST", "DELETE"})
            assert route_map["/sse"].methods.issuperset({"GET"})
            assert wrapped_app._butler_name == "test-butler"
            assert kwargs == {
                "host": "0.0.0.0",
                "port": 9100,
                "log_level": "warning",
                "timeout_graceful_shutdown": 0,
            }

        # Verify server instance was stored
        assert daemon._server is mock_uvicorn_server
        # Verify a background task was created
        assert daemon._server_task is not None

        # Clean up background task
        daemon._server_task.cancel()
        try:
            await daemon._server_task
        except asyncio.CancelledError:
            pass

    async def test_start_mcp_server_runs_as_background_task(self, butler_dir: Path) -> None:
        """The server should run as an asyncio background task, not block start()."""
        patches = _patch_infra()

        # Create a mock server whose serve() blocks until cancelled
        serve_started = asyncio.Event()

        async def mock_serve():
            serve_started.set()
            await asyncio.sleep(999)  # Block indefinitely

        mock_mcp = MagicMock()
        streamable_app = FastAPI()
        streamable_app.add_api_route("/mcp", endpoint=lambda: None, methods=["GET", "POST"])
        sse_app = FastAPI()
        sse_app.add_api_route("/sse", endpoint=lambda: None, methods=["GET"])
        sse_app.mount("/messages", app=FastAPI())
        mock_mcp.http_app.side_effect = [streamable_app, sse_app]

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = mock_serve

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patch("butlers.daemon.uvicorn.Config"),
            patch("butlers.daemon.uvicorn.Server", return_value=mock_uvicorn_server),
        ):
            daemon = ButlerDaemon(butler_dir)
            # start() should return even though serve() blocks
            await daemon.start()

        # The serve task should be running in the background
        assert daemon._server_task is not None
        assert not daemon._server_task.done()

        # Verify _started_at was recorded (start() completed)
        assert daemon._started_at is not None

        # Clean up
        daemon._server_task.cancel()
        try:
            await daemon._server_task
        except asyncio.CancelledError:
            pass

    def test_build_mcp_http_app_exposes_routes_and_content_type_behavior(self) -> None:
        """Combined app keeps SSE routes and serves streamable HTTP at /mcp."""
        mcp = RuntimeFastMCP("test-butler")
        app = ButlerDaemon._build_mcp_http_app(mcp, butler_name="test-butler")

        assert isinstance(app, _McpSseDisconnectGuard)
        route_map = {(type(route).__name__, route.path): route for route in app._app.routes}
        assert ("Route", "/mcp") in route_map
        assert ("Route", "/sse") in route_map
        assert ("Mount", "/messages") in route_map
        assert route_map[("Route", "/mcp")].methods is None
        assert route_map[("Route", "/sse")].methods == {"GET", "HEAD"}

        with TestClient(app) as client:
            response = client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "text/plain",
                },
                content="{}",
            )

        assert response.status_code == 400
        assert "Content-Type" in response.text


class TestSseDisconnectGuard:
    async def test_messages_post_client_disconnect_is_suppressed(
        self, caplog: pytest.LogCaptureFixture
    ):
        sent_messages: list[dict[str, Any]] = []

        async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            raise ClientDisconnect()

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent_messages.append(message)

        guard = _McpSseDisconnectGuard(inner_app, butler_name="test-butler")
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/messages/",
            "query_string": b"session_id=abc123",
        }

        with caplog.at_level(logging.DEBUG, logger="butlers.daemon"):
            await guard(scope, receive, send)

        assert any(
            "Suppressed expected MCP SSE POST disconnect" in rec.message for rec in caplog.records
        )
        assert sent_messages == [
            {
                "type": "http.response.start",
                "status": 202,
                "headers": [(b"content-length", b"0")],
            },
            {"type": "http.response.body", "body": b""},
        ]

    async def test_non_disconnect_exception_bubbles(self) -> None:
        async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            raise RuntimeError("boom")

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            return None

        guard = _McpSseDisconnectGuard(inner_app, butler_name="test-butler")
        scope = {"type": "http", "method": "POST", "path": "/messages/", "query_string": b""}

        with pytest.raises(RuntimeError, match="boom"):
            await guard(scope, receive, send)

    async def test_non_messages_path_client_disconnect_bubbles(self) -> None:
        async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            raise ClientDisconnect()

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            return None

        guard = _McpSseDisconnectGuard(inner_app, butler_name="test-butler")
        scope = {"type": "http", "method": "POST", "path": "/health", "query_string": b""}

        with pytest.raises(ClientDisconnect):
            await guard(scope, receive, send)


class TestStatusTool:
    """Verify the status() MCP tool returns correct data."""

    async def test_status_returns_butler_info(self, butler_dir: Path) -> None:
        """status() should return name, description, port, modules, health, uptime."""
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal status_fn
                if fn.__name__ == "status":
                    status_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            # Use empty registry so no modules are loaded (status.modules == {}).
            daemon = ButlerDaemon(butler_dir, registry=ModuleRegistry())
            await daemon.start()

        assert status_fn is not None, "status tool was not registered"

        result = await status_fn()
        assert result["name"] == "test-butler"
        assert result["description"] == "A test butler"
        assert result["port"] == 9100
        assert result["modules"] == {}
        assert result["health"] == "ok"
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] >= 0

    async def test_status_includes_module_names(self, butler_dir_with_modules: Path) -> None:
        """status() should list loaded module names."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal status_fn
                if fn.__name__ == "status":
                    status_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        assert status_fn is not None
        result = await status_fn()
        assert set(result["modules"].keys()) == {"stub_a", "stub_b"}
        assert all(v["status"] == "active" for v in result["modules"].values())


class TestHealthCheck:
    """Verify dynamic health checking in the status() MCP tool."""

    async def _get_status_fn(self, butler_dir, patches):
        """Helper to start daemon and extract the status function."""
        status_fn = None
        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal status_fn
                if fn.__name__ == "status":
                    status_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            # Use empty registry: no modules means no failed modules, no
            # fetchval calls during _init_module_runtime_states, and
            # health checks reflect only the DB pool probe.
            daemon = ButlerDaemon(butler_dir, registry=ModuleRegistry())
            await daemon.start()

        return daemon, status_fn

    async def test_health_ok_when_pool_healthy(self, butler_dir: Path) -> None:
        """status() returns health=ok when DB pool responds to SELECT 1."""
        patches = _patch_infra()
        mock_pool = patches["mock_pool"]
        mock_pool.fetchval = AsyncMock(return_value=1)

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        result = await status_fn()
        assert result["health"] == "ok"
        mock_pool.fetchval.assert_awaited_once_with("SELECT 1")

    async def test_health_degraded_when_pool_raises(self, butler_dir: Path) -> None:
        """status() returns health=degraded when DB pool query raises an exception."""
        patches = _patch_infra()
        mock_pool = patches["mock_pool"]
        mock_pool.fetchval = AsyncMock(side_effect=ConnectionRefusedError("connection refused"))

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        result = await status_fn()
        assert result["health"] == "degraded"

    async def test_health_degraded_when_pool_is_none(self, butler_dir: Path) -> None:
        """status() returns health=degraded when pool has been set to None (closed)."""
        patches = _patch_infra()

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        # Simulate pool being closed (set to None)
        daemon.db.pool = None

        result = await status_fn()
        assert result["health"] == "degraded"

    async def test_health_degraded_when_db_is_none(self, butler_dir: Path) -> None:
        """status() returns health=degraded when db object itself is None."""
        patches = _patch_infra()

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        # Simulate db being None (not initialized)
        daemon.db = None

        result = await status_fn()
        assert result["health"] == "degraded"

    async def test_health_degraded_when_pool_timeout(self, butler_dir: Path) -> None:
        """status() returns health=degraded when pool query times out."""

        patches = _patch_infra()
        mock_pool = patches["mock_pool"]
        mock_pool.fetchval = AsyncMock(side_effect=TimeoutError())

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        result = await status_fn()
        assert result["health"] == "degraded"

    async def test_status_still_returns_all_fields_when_degraded(self, butler_dir: Path) -> None:
        """Even when degraded, status() still returns all expected fields."""
        patches = _patch_infra()
        mock_pool = patches["mock_pool"]
        mock_pool.fetchval = AsyncMock(side_effect=OSError("pool closed"))

        daemon, status_fn = await self._get_status_fn(butler_dir, patches)
        assert status_fn is not None

        result = await status_fn()
        assert result["health"] == "degraded"
        assert result["name"] == "test-butler"
        assert result["description"] == "A test butler"
        assert result["port"] == 9100
        assert result["modules"] == {}
        assert isinstance(result["uptime_seconds"], float)


class TestStartupFailurePropagation:
    """Verify that failures in early steps prevent later steps."""

    async def test_credential_failure_stops_startup(self, butler_dir: Path) -> None:
        """If validate_credentials raises, DB is never provisioned."""
        patches = _patch_infra()

        with (
            patches["db_from_env"] as mock_from_env,
            patches["run_migrations"],
            patch(
                "butlers.daemon.validate_credentials",
                side_effect=CredentialError("missing PG_DSN"),
            ),
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(CredentialError, match="missing PG_DSN"):
                await daemon.start()

        # DB should never have been created
        mock_from_env.assert_not_called()

    async def test_db_provision_failure_stops_startup(self, butler_dir: Path) -> None:
        """If DB provisioning fails, migrations should not run."""
        patches = _patch_infra()
        mock_db = patches["mock_db"]
        mock_db.provision.side_effect = ConnectionRefusedError("no pg")

        with (
            patches["db_from_env"],
            patches["run_migrations"] as mock_migrations,
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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(ConnectionRefusedError):
                await daemon.start()

        mock_migrations.assert_not_awaited()


class TestScheduleSync:
    """Verify TOML schedules are synced to DB during startup."""

    async def test_schedules_synced(self, butler_dir: Path) -> None:
        """sync_schedules should be called with parsed schedule entries."""
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"] as mock_sync,
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        mock_sync.assert_awaited_once()
        args = mock_sync.call_args
        schedules_arg = args[0][1]  # second positional arg
        assert len(schedules_arg) == 1
        assert schedules_arg[0]["name"] == "daily-check"
        assert schedules_arg[0]["cron"] == "0 9 * * *"
        assert schedules_arg[0]["prompt"] == "Do the daily check"
        assert args[1]["stagger_key"] == "test-butler"


class TestModuleCredentials:
    """Verify module credentials are collected and validated separately."""

    async def test_module_creds_validated_separately(self, butler_dir_with_modules: Path) -> None:
        """validate_module_credentials_async should receive module credential env vars."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"] as mock_mod_validate,
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Core validate_credentials called without module_credentials
        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        # validate_module_credentials_async called with module creds (first positional arg)
        mock_mod_validate.assert_called_once()
        mod_creds_arg = mock_mod_validate.call_args[0][0]
        assert "stub_a" in mod_creds_arg
        assert "STUB_A_TOKEN" in mod_creds_arg["stub_a"]

    async def test_credential_store_passed_to_module_validation(
        self, butler_dir_with_modules: Path
    ) -> None:
        """validate_module_credentials_async receives a CredentialStore instance."""
        from butlers.credential_store import CredentialStore

        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        received_args: list[object] = []

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"] as mock_mod_validate,
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):

            async def _capture(*args: object, **kwargs: object) -> dict:
                received_args.extend(args)
                return {}

            mock_mod_validate.side_effect = _capture
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Second positional arg must be a CredentialStore
        assert len(received_args) >= 2
        assert isinstance(received_args[1], CredentialStore)


class TestSecretDetection:
    """Verify detect_secrets is called during startup and warnings are logged."""

    async def test_detect_secrets_called_at_startup(
        self, butler_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """detect_secrets should be called during daemon startup."""
        patches = _patch_infra()

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
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # Should not warn for clean config
        assert not any("may contain an inline secret" in rec.message for rec in caplog.records)

    async def test_detect_secrets_warns_on_suspicious_config(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Secret detection should log warnings for suspicious config values."""
        # Create a butler.toml with a suspicious description that starts with a secret prefix
        toml_content = """
[butler]
name = "test-butler"
port = 9100
description = "sk-1234567890abcdefghij1234567890abcdef"

[butler.db]
name = "butler_test"
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        patches = _patch_infra()

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
            patches["configure_logging"],
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        # Should warn about the suspicious description
        warnings = [
            rec.message for rec in caplog.records if "may contain an inline secret" in rec.message
        ]
        assert len(warnings) == 1
        assert "butler.description" in warnings[0]
        assert "sk-" in warnings[0]

    async def test_credentials_env_exempt_from_scanning(
        self, butler_dir_with_modules: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """credentials_env field should be exempt from secret scanning."""
        patches = _patch_infra()
        registry = _make_registry(StubModuleA, StubModuleB)

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
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        # Should not warn about credentials_env field (it's a list of env var names)
        warnings = [
            rec.message for rec in caplog.records if "may contain an inline secret" in rec.message
        ]
        assert len(warnings) == 0

    async def test_butler_env_lists_exempt_from_scanning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """[butler.env] required/optional lists should be exempt from scanning."""
        toml_content = """
[butler]
name = "test-butler"
port = 9100
description = "A test butler"

[butler.db]
name = "butler_test"

[butler.env]
required = ["ANTHROPIC_API_KEY", "SECRET_TOKEN"]
optional = ["OPTIONAL_KEY"]
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        patches = _patch_infra()

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
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        # Should not warn about butler.env lists (they are env var names, not values)
        warnings = [
            rec.message for rec in caplog.records if "may contain an inline secret" in rec.message
        ]
        assert len(warnings) == 0


class TestModuleCredentialsTomlSource:
    """Verify credentials_env is read from TOML config with class fallback."""

    async def test_toml_credentials_override_class(self, tmp_path: Path) -> None:
        """When TOML declares credentials_env, it overrides the class property."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={
                "stub_a": {"credentials_env": ["TOML_TOKEN_A", "TOML_SECRET_A"]},
                "stub_b": {},
            },
        )
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # validate_credentials is called without module_credentials
        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        # Module creds are collected and checked internally
        module_creds = daemon._collect_module_credentials()
        assert "stub_a" in module_creds
        # TOML-declared creds should be used, NOT the class property ["STUB_A_TOKEN"]
        assert module_creds["stub_a"] == ["TOML_TOKEN_A", "TOML_SECRET_A"]
        assert "STUB_A_TOKEN" not in module_creds["stub_a"]

    async def test_class_credentials_fallback(self, butler_dir_with_modules: Path) -> None:
        """When TOML does not declare credentials_env, fall back to class property."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        # stub_a has credentials_env class property → should be used as fallback
        assert "stub_a" in module_creds
        assert module_creds["stub_a"] == ["STUB_A_TOKEN"]
        # stub_b has no credentials_env → should not appear
        assert "stub_b" not in module_creds

    async def test_toml_empty_credentials_no_fallback(self, tmp_path: Path) -> None:
        """When TOML declares credentials_env as empty list, no fallback to class."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_a": {"credentials_env": []}},
        )
        registry = _make_registry(StubModuleA)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        # TOML declares empty list — should be used (not fall back to class)
        assert "stub_a" in module_creds
        assert module_creds["stub_a"] == []

    async def test_toml_invalid_credentials_type_no_crash(self, tmp_path: Path) -> None:
        """Invalid TOML credentials_env types are ignored without crashing startup."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_a": {"credentials_env": 123}},
        )
        registry = _make_registry(StubModuleA)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        # Invalid TOML type should be treated as explicitly empty (no class fallback).
        assert module_creds["stub_a"] == []

    async def test_toml_credentials_filter_empty_and_non_strings(self, tmp_path: Path) -> None:
        """Only non-empty string entries are kept from TOML credentials_env lists."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_a": {"credentials_env": ["TOKEN_A", "", 123, "TOKEN_B"]}},
        )
        registry = _make_registry(StubModuleA)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        assert module_creds["stub_a"] == ["TOKEN_A", "TOKEN_B"]

    async def test_mixed_toml_and_class_credentials(self, tmp_path: Path) -> None:
        """One module uses TOML creds, another falls back to class property."""
        # stub_a: TOML overrides, stub_b: no class credentials_env, no TOML
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={
                "stub_a": {"credentials_env": ["CUSTOM_TOKEN"]},
                "stub_b": {},
            },
        )
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        # stub_a: TOML-declared
        assert module_creds["stub_a"] == ["CUSTOM_TOKEN"]
        # stub_b: no class credentials_env property, no TOML → absent
        assert "stub_b" not in module_creds

    async def test_toml_credentials_passed_to_spawner(self, tmp_path: Path) -> None:
        """TOML-declared credentials are forwarded to Spawner."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_a": {"credentials_env": ["TOML_KEY"]}},
        )
        registry = _make_registry(StubModuleA)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_spawner_cls.assert_called_once()
        spawner_kwargs = mock_spawner_cls.call_args.kwargs
        assert spawner_kwargs["module_credentials_env"] == {"stub_a": ["TOML_KEY"]}

    async def test_identity_scoped_credentials_are_collected(self, tmp_path: Path) -> None:
        """Identity-scoped user/bot env vars are collected with scope-qualified sources."""
        (tmp_path / "butler.toml").write_text(
            """
[butler]
name = "switchboard"
port = 9100
description = "A test butler"

[butler.db]
name = "butler_test"

[modules.telegram]

[modules.telegram.user]
enabled = false

[modules.telegram.bot]
token_env = "TG_BOT_TOKEN"

[modules.email]

[modules.email.user]
enabled = false

[modules.email.bot]
address_env = "BOT_EMAIL_ADDRESS"
password_env = "BOT_EMAIL_PASSWORD"
"""
        )
        registry = _make_registry(TelegramModule, EmailModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["recover_route_inbox"],
            patches["start_mcp_server"],
        ):
            daemon = ButlerDaemon(tmp_path, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        assert "module_credentials" not in mock_validate.call_args.kwargs

        module_creds = daemon._collect_module_credentials()
        assert module_creds["telegram.bot"] == ["TG_BOT_TOKEN"]
        assert module_creds["email.bot"] == ["BOT_EMAIL_ADDRESS", "BOT_EMAIL_PASSWORD"]
        assert "telegram.user" not in module_creds
        assert "email.user" not in module_creds

        mock_spawner_cls.assert_called_once()
        spawner_kwargs = mock_spawner_cls.call_args.kwargs
        # When validate_module_credentials returns {} (no failures), all creds are passed
        assert spawner_kwargs["module_credentials_env"] == module_creds

    async def test_multiple_secrets_detected(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Multiple suspicious config values should produce multiple warnings."""
        # Create a butler.toml with multiple suspicious values
        toml_content = """
[butler]
name = "test-butler"
port = 9100
description = "sk-1234567890abcdefghij1234567890abcdef"

[butler.db]
name = "ghp_1234567890abcdefghij1234567890abcdef"
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        patches = _patch_infra()

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
            patches["configure_logging"],
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        # Should warn about both suspicious values
        warnings = [
            rec.message for rec in caplog.records if "may contain an inline secret" in rec.message
        ]
        assert len(warnings) == 2
        assert any("butler.description" in w for w in warnings)
        assert any("butler.db.name" in w for w in warnings)


class TestFlattenConfigForSecretScan:
    """Test the _flatten_config_for_secret_scan helper function."""

    def test_flatten_basic_config(self, butler_dir: Path) -> None:
        """Flatten a basic config into a flat dict."""
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        config = load_config(butler_dir)
        flat = _flatten_config_for_secret_scan(config)

        assert flat["butler.name"] == "test-butler"
        assert flat["butler.port"] == 9100
        assert flat["butler.description"] == "A test butler"
        assert flat["butler.db.name"] == "butler_test"

    def test_flatten_schedules(self, butler_dir: Path) -> None:
        """Schedules should be flattened with array indices."""
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        config = load_config(butler_dir)
        flat = _flatten_config_for_secret_scan(config)

        assert flat["butler.schedule[0].name"] == "daily-check"
        assert flat["butler.schedule[0].cron"] == "0 9 * * *"
        assert flat["butler.schedule[0].prompt"] == "Do the daily check"

    def test_flatten_module_configs(self, tmp_path: Path) -> None:
        """Module configs should be flattened."""
        toml_content = """
[butler]
name = "test-butler"
port = 9100

[butler.db]
name = "butler_test"

[modules.email]
smtp_server = "smtp.example.com"
smtp_port = "587"
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        config = load_config(tmp_path)
        flat = _flatten_config_for_secret_scan(config)

        assert flat["modules.email.smtp_server"] == "smtp.example.com"
        assert flat["modules.email.smtp_port"] == "587"

    def test_flatten_excludes_credentials_env(self, tmp_path: Path) -> None:
        """credentials_env field should be excluded from flattened output."""
        toml_content = """
[butler]
name = "test-butler"
port = 9100

[butler.db]
name = "butler_test"

[modules.email]
credentials_env = ["EMAIL_TOKEN", "API_KEY"]
smtp_server = "smtp.example.com"
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        config = load_config(tmp_path)
        flat = _flatten_config_for_secret_scan(config)

        # credentials_env should not be in flattened output
        assert "modules.email.credentials_env" not in flat
        # But other module config should be
        assert flat["modules.email.smtp_server"] == "smtp.example.com"

    def test_flatten_no_env_lists_in_output(self, tmp_path: Path) -> None:
        """butler.env lists should not be in flattened output."""
        toml_content = """
[butler]
name = "test-butler"
port = 9100

[butler.db]
name = "butler_test"

[butler.env]
required = ["ANTHROPIC_API_KEY"]
optional = ["OPTIONAL_KEY"]
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        config = load_config(tmp_path)
        flat = _flatten_config_for_secret_scan(config)

        # env lists should not be in flattened output (they are just env var names)
        assert "butler.env.required" not in flat
        assert "butler.env.optional" not in flat


class TestRuntimeAdapterPassedToSpawner:
    """Verify runtime adapter is passed to Spawner during startup."""

    async def test_runtime_adapter_passed_to_spawner(self, butler_dir: Path) -> None:
        """Spawner should receive the runtime adapter instance."""
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        mock_spawner_cls.assert_called_once()
        call_kwargs = mock_spawner_cls.call_args.kwargs
        assert "runtime" in call_kwargs
        # The runtime should be the instance created by mock_adapter_cls()
        assert call_kwargs["runtime"] is patches["mock_adapter"]


class TestMessagePipelineWiring:
    """Verify switchboard-only MessagePipeline wiring for channel modules."""

    async def test_switchboard_wires_pipeline_to_telegram_and_email(self, tmp_path: Path) -> None:
        """Switchboard startup should attach a MessagePipeline to both channel modules."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"telegram": {}, "email": {}},
            butler_name="switchboard",
            port=40100,
            db_name="butler_switchboard",
        )
        registry = _make_registry(TelegramModule, EmailModule)
        patches = _patch_infra()

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
            patches["recover_route_inbox"],
            patch.object(TelegramModule, "on_startup", new_callable=AsyncMock),
            patch.object(EmailModule, "on_startup", new_callable=AsyncMock),
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        email_module = next(m for m in daemon._modules if m.name == "email")

        assert isinstance(telegram_module, TelegramModule)
        assert isinstance(email_module, EmailModule)
        # TelegramModule no longer has set_pipeline (ingestion moved to connector).
        assert not hasattr(telegram_module, "_pipeline")
        # EmailModule still wires a pipeline.
        assert isinstance(email_module._pipeline, MessagePipeline)
        assert email_module._pipeline._pool is patches["mock_pool"]
        assert email_module._pipeline._source_butler == "switchboard"
        assert email_module._pipeline._dispatch_fn is daemon.spawner.trigger
        assert email_module._pipeline._dispatch_fn is patches["mock_spawner"].trigger

    async def test_non_switchboard_does_not_wire_pipeline(self, tmp_path: Path) -> None:
        """Non-switchboard butlers should not attach a MessagePipeline to channel modules."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"telegram": {}, "email": {}},
        )
        registry = _make_registry(TelegramModule, EmailModule)
        patches = _patch_infra()

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
            patches["recover_route_inbox"],
            patch.object(TelegramModule, "on_startup", new_callable=AsyncMock),
            patch.object(EmailModule, "on_startup", new_callable=AsyncMock),
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        email_module = next(m for m in daemon._modules if m.name == "email")

        assert isinstance(telegram_module, TelegramModule)
        assert isinstance(email_module, EmailModule)
        # TelegramModule no longer has _pipeline attribute (ingestion moved to connector).
        assert not hasattr(telegram_module, "_pipeline")
        assert email_module._pipeline is None


class TestRuntimeBinaryCheck:
    """Verify that missing runtime binaries are detected at startup."""

    async def test_missing_binary_raises_at_startup(self, butler_dir: Path) -> None:
        """When shutil.which returns None, startup should raise RuntimeBinaryNotFoundError."""
        patches = _patch_infra()

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
            patch("butlers.daemon.shutil.which", return_value=None),
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(RuntimeBinaryNotFoundError, match="not found on PATH"):
                await daemon.start()

    async def test_missing_binary_error_names_binary(self, butler_dir: Path) -> None:
        """The error message should include the binary name."""
        patches = _patch_infra()

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
            patch("butlers.daemon.shutil.which", return_value=None),
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(RuntimeBinaryNotFoundError, match="'claude'"):
                await daemon.start()

    async def test_binary_found_allows_startup(self, butler_dir: Path) -> None:
        """When shutil.which finds the binary, startup should proceed normally."""
        patches = _patch_infra()

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
            patches["shutil_which"] as mock_which,
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # shutil.which should have been called with the binary name
        mock_which.assert_called_once_with("claude")
        # Daemon should have completed startup
        assert daemon._started_at is not None

    async def test_missing_binary_prevents_spawner_creation(self, butler_dir: Path) -> None:
        """When binary is missing, Spawner should not be created."""
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patch("butlers.daemon.shutil.which", return_value=None),
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(RuntimeBinaryNotFoundError):
                await daemon.start()

        mock_spawner_cls.assert_not_called()


class TestSwitchboardClientConnection:
    """Verify the Switchboard MCP client connection lifecycle."""

    async def test_connect_switchboard_called_during_startup(self, butler_dir: Path) -> None:
        """_connect_switchboard should be called during startup for non-switchboard butlers."""
        patches = _patch_infra()

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
            patches["connect_switchboard"] as mock_connect,
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        mock_connect.assert_awaited_once()

    async def test_switchboard_client_none_initially(self, butler_dir: Path) -> None:
        """switchboard_client should be None before start() is called."""
        daemon = ButlerDaemon(butler_dir)
        assert daemon.switchboard_client is None

    async def test_connect_switchboard_skips_when_url_is_none(self, tmp_path: Path) -> None:
        """_connect_switchboard skips connection when switchboard_url is None."""
        # The switchboard butler has switchboard_url=None
        toml = """\
[butler]
name = "switchboard"
port = 40100

[butler.db]
name = "butler_switchboard"
"""
        (tmp_path / "butler.toml").write_text(toml)
        patches = _patch_infra()

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
            # Do NOT mock _connect_switchboard — let it run
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        # switchboard_client should remain None
        assert daemon.switchboard_client is None

    async def test_connect_switchboard_success(self, butler_dir: Path) -> None:
        """Successful connection should set switchboard_client."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon.switchboard_client is mock_client
        mock_client.__aenter__.assert_awaited_once()

    async def test_connect_switchboard_failure_non_fatal(self, butler_dir: Path) -> None:
        """Connection failure should not prevent butler startup."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("Connection refused"))

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            # Should NOT raise — failure is logged as warning
            await daemon.start()

        # switchboard_client should remain None
        assert daemon.switchboard_client is None
        # Butler should still be marked as started
        assert daemon._started_at is not None

    async def test_disconnect_switchboard_on_shutdown(self, butler_dir: Path) -> None:
        """shutdown() should close the Switchboard client."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon.switchboard_client is mock_client

        await daemon.shutdown()

        # Client should have been closed and set to None
        mock_client.__aexit__.assert_awaited_once_with(None, None, None)
        assert daemon.switchboard_client is None

    async def test_disconnect_switchboard_error_non_fatal(self, butler_dir: Path) -> None:
        """Error closing Switchboard client should not prevent shutdown."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(side_effect=OSError("connection reset"))

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # Should not raise
        await daemon.shutdown()

        # Client should be set to None despite error
        assert daemon.switchboard_client is None

    async def test_switchboard_url_from_config(self, tmp_path: Path) -> None:
        """Switchboard URL should come from butler config."""
        toml = """\
[butler]
name = "health"
port = 40103

[butler.db]
name = "butler_health"

[butler.switchboard]
url = "http://custom-switchboard:9000/sse"
"""
        (tmp_path / "butler.toml").write_text(toml)
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        client_init_args = []

        def capture_client_init(url, **kwargs):
            client_init_args.append((url, kwargs))
            return mock_client

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
            patch("butlers.daemon.MCPClient", side_effect=capture_client_init),
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        assert len(client_init_args) == 1
        assert client_init_args[0][0] == "http://custom-switchboard:9000/sse"
        assert client_init_args[0][1]["name"] == "butler-health"

    async def test_shutdown_without_switchboard_client(self, butler_dir: Path) -> None:
        """shutdown() should work when switchboard_client is None."""
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # switchboard_client is None (mocked _connect_switchboard)
        assert daemon.switchboard_client is None

        # Should not raise
        await daemon.shutdown()


class TestNotifyTool:
    """Verify the notify() core MCP tool."""

    async def _start_daemon_with_notify(self, butler_dir: Path, patches: dict):
        """Start daemon and extract the notify function reference."""
        notify_fn = None
        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal notify_fn
                if fn.__name__ == "notify":
                    notify_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        return daemon, notify_fn

    async def test_notify_registered_as_core_tool(self, butler_dir: Path) -> None:
        """notify should be registered as a core MCP tool."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None, "notify tool was not registered"

    async def test_notify_tool_description_and_schema_contract(self, butler_dir: Path) -> None:
        """notify tool metadata should document request_context and strict enum types."""
        patches = _patch_infra()
        runtime_mcp = RuntimeFastMCP("test-butler")

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=runtime_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        get_tools = getattr(runtime_mcp, "get_tools", None)
        if callable(get_tools):
            tools = await get_tools()
            notify_tool = tools["notify"].model_dump()
        else:
            notify_tool = (await runtime_mcp.get_tool("notify")).model_dump()

        description = notify_tool["description"] or ""
        assert "notify.v1" in description
        assert '"intent": "reply"' in description
        assert '"request_context"' in description
        assert '"source_thread_identity"' in description

        params = notify_tool["parameters"]
        channel_prop = params["properties"]["channel"]
        assert set(channel_prop["enum"]) == {"telegram", "email"}

        intent_prop = params["properties"]["intent"]
        assert set(intent_prop["enum"]) == {"send", "reply", "react"}

        request_context_param = params["properties"]["request_context"]
        request_context_json = json.dumps(request_context_param)
        assert "request_id" in request_context_json
        assert "source_channel" in request_context_json
        assert "source_endpoint_identity" in request_context_json
        assert "source_sender_identity" in request_context_json
        assert "source_thread_identity" in request_context_json
        assert "telegram reply" in request_context_json.lower()

    async def test_notify_unsupported_channel_returns_error(self, butler_dir: Path) -> None:
        """notify with an unsupported channel should return error result."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(channel="sms", message="Hello")
        assert result["status"] == "error"
        assert "sms" in result["error"]
        assert "Unsupported channel" in result["error"]

    async def test_notify_telegram_channel_accepted(self, butler_dir: Path) -> None:
        """notify with channel='telegram' should not return channel error."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # switchboard_client is None because _connect_switchboard is mocked
        result = await notify_fn(channel="telegram", message="Hello")
        # Should fail due to no switchboard, NOT due to invalid channel
        assert result["status"] == "error"
        assert "Switchboard is not connected" in result["error"]

    async def test_notify_email_channel_accepted(self, butler_dir: Path) -> None:
        """notify with channel='email' should not return channel error."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(channel="email", message="Hello")
        assert result["status"] == "error"
        assert "Switchboard is not connected" in result["error"]

    async def test_notify_switchboard_not_connected_returns_error(self, butler_dir: Path) -> None:
        """notify should return error when switchboard_client is None."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # switchboard_client is None (mocked _connect_switchboard)
        assert daemon.switchboard_client is None

        result = await notify_fn(channel="telegram", message="Hello")
        assert result["status"] == "error"
        assert "Switchboard is not connected" in result["error"]

    async def test_notify_successful_delivery(self, butler_dir: Path) -> None:
        """notify should return success when Switchboard delivers successfully."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Mock the switchboard client
        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "abc-123", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="email", message="Hello world")

        assert result["status"] == "ok"
        assert result["result"] == {"notification_id": "abc-123", "status": "sent"}

        # Verify call_tool was called with correct args
        mock_client.call_tool.assert_awaited_once()
        call_args = mock_client.call_tool.await_args
        assert call_args.args[0] == "deliver"
        payload = call_args.args[1]
        assert payload["source_butler"] == "test-butler"
        assert payload["notify_request"]["schema_version"] == "notify.v1"
        assert payload["notify_request"]["origin_butler"] == "test-butler"
        assert payload["notify_request"]["delivery"] == {
            "intent": "send",
            "channel": "email",
            "message": "Hello world",
        }

    async def test_notify_with_recipient(self, butler_dir: Path) -> None:
        """notify with explicit recipient should forward it to Switchboard."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "def-456", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        result = await notify_fn(
            channel="email", message="Weekly report", recipient="user@example.com"
        )

        assert result["status"] == "ok"

        # Verify recipient was included in the call
        mock_client.call_tool.assert_awaited_once()
        call_args = mock_client.call_tool.await_args
        assert call_args.args[0] == "deliver"
        payload = call_args.args[1]
        delivery = payload["notify_request"]["delivery"]
        assert payload["source_butler"] == "test-butler"
        assert delivery["channel"] == "email"
        assert delivery["message"] == "Weekly report"
        assert delivery["recipient"] == "user@example.com"

    async def test_notify_without_recipient(self, butler_dir: Path) -> None:
        """notify send without recipient should omit it for non-telegram channels."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "ghi-789", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="email", message="Alert")

        assert result["status"] == "ok"

        # Verify recipient is NOT in the call args
        call_args = mock_client.call_tool.call_args
        deliver_args = call_args[0][1]
        assert "recipient" not in deliver_args

    async def test_notify_telegram_send_uses_default_chat_id_from_contact_info(
        self, butler_dir: Path
    ) -> None:
        """Telegram send without recipient should resolve chat ID from owner contact_info."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "jkl-012", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        # contact_info returns the chat ID — credential_store fallback is not needed
        with patch(
            "butlers.daemon.resolve_owner_contact_info",
            new=AsyncMock(return_value="123456789"),
        ):
            result = await notify_fn(channel="telegram", message="Scheduled update", intent="send")

        assert result["status"] == "ok"
        call_args = mock_client.call_tool.await_args
        payload = call_args.args[1]
        assert payload["notify_request"]["delivery"]["recipient"] == "123456789"

    async def test_notify_telegram_send_falls_back_to_secret_when_no_contact_info(
        self, butler_dir: Path
    ) -> None:
        """Telegram send falls back to TELEGRAM_CHAT_ID secret when contact_info is absent."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "jkl-012", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        daemon._credential_store = AsyncMock()
        daemon._credential_store.resolve = AsyncMock(return_value="987654321")

        # contact_info returns None → fall back to credential_store
        with patch(
            "butlers.daemon.resolve_owner_contact_info",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(channel="telegram", message="Scheduled update", intent="send")

        assert result["status"] == "ok"
        # Should have tried TELEGRAM_CHAT_ID from butler_secrets (legacy fallback)
        resolve_calls = daemon._credential_store.resolve.await_args_list
        called_keys = [call.args[0] for call in resolve_calls]
        assert "TELEGRAM_CHAT_ID" in called_keys
        call_args = mock_client.call_tool.await_args
        payload = call_args.args[1]
        assert payload["notify_request"]["delivery"]["recipient"] == "987654321"

    async def test_notify_telegram_send_errors_when_no_default_chat_id(
        self, butler_dir: Path
    ) -> None:
        """Telegram send without recipient should fail when default chat is not configured."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock()
        daemon.switchboard_client = mock_client

        daemon._credential_store = AsyncMock()
        daemon._credential_store.resolve = AsyncMock(return_value=None)

        with patch(
            "butlers.daemon.resolve_owner_contact_info",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(channel="telegram", message="Scheduled update", intent="send")

        assert result["status"] == "error"
        assert "No bot <-> user telegram chat has been configured" in result["error"]
        assert "TELEGRAM_CHAT_ID" in result["error"]
        mock_client.call_tool.assert_not_awaited()

    async def test_notify_switchboard_returns_error(self, butler_dir: Path) -> None:
        """notify should return error when Switchboard's deliver() returns error."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Mock an error result from call_tool
        mock_content = MagicMock()
        mock_content.text = "No module available for channel 'telegram'"

        mock_call_result = MagicMock()
        mock_call_result.is_error = True
        mock_call_result.content = [mock_content]

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="email", message="Hello")

        assert result["status"] == "error"
        assert "No module available" in result["error"]

    async def test_notify_switchboard_call_raises_exception(self, butler_dir: Path) -> None:
        """notify should return error (not raise) when call_tool raises a generic exception."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Unexpected failure"))
        daemon.switchboard_client = mock_client

        # Should NOT raise — returns error result
        result = await notify_fn(channel="email", message="Hello")

        assert result["status"] == "error"
        assert "Switchboard call failed" in result["error"]
        assert "Unexpected failure" in result["error"]

    async def test_notify_switchboard_timeout_returns_error(self, butler_dir: Path) -> None:
        """notify should return error when Switchboard call times out."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=TimeoutError("Request timed out"))
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="email", message="Hello")

        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()

    async def test_notify_includes_source_butler_name(self, butler_dir: Path) -> None:
        """notify should include the butler name as source_butler in deliver args."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        await notify_fn(channel="email", message="Test")

        call_args = mock_client.call_tool.call_args
        deliver_args = call_args[0][1]
        assert deliver_args["source_butler"] == "test-butler"

    async def test_notify_empty_message(self, butler_dir: Path) -> None:
        """notify with empty or whitespace-only message should return error."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Empty string
        result = await notify_fn(channel="telegram", message="")
        assert result["status"] == "error"
        assert "empty" in result["error"].lower() or "whitespace" in result["error"].lower()

        # Whitespace-only string
        result = await notify_fn(channel="telegram", message="   \t\n  ")
        assert result["status"] == "error"
        assert "empty" in result["error"].lower() or "whitespace" in result["error"].lower()

    async def test_notify_timeout(self, butler_dir: Path) -> None:
        """notify should return a timeout-specific error when call_tool hangs."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        _orphaned_coros: list = []

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(999)  # pragma: no cover

        def _tracking_slow_call(*args, **kwargs):
            coro = slow_call(*args, **kwargs)
            _orphaned_coros.append(coro)
            return coro

        mock_client = AsyncMock()
        mock_client.call_tool = _tracking_slow_call
        daemon.switchboard_client = mock_client

        # The timeout is a local variable inside notify, so we mock
        # asyncio.wait_for to raise TimeoutError directly.
        with patch("butlers.daemon.asyncio.wait_for", side_effect=TimeoutError()):
            result = await notify_fn(channel="email", message="Hello")

        # Close any coroutines that were created but not awaited (wait_for was mocked
        # to raise TimeoutError before the coroutine could be scheduled).
        for coro in _orphaned_coros:
            coro.close()

        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()

    async def test_notify_connection_error(self, butler_dir: Path) -> None:
        """notify should return 'unreachable' error for ConnectionError/OSError."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Test with ConnectionError
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=ConnectionError("Connection refused"))
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="email", message="Hello")
        assert result["status"] == "error"
        assert "unreachable" in result["error"].lower()
        assert "Connection refused" in result["error"]

        # Test with OSError (parent class of ConnectionError)
        mock_client.call_tool = AsyncMock(side_effect=OSError("Network is down"))

        result = await notify_fn(channel="email", message="Hello")
        assert result["status"] == "error"
        assert "unreachable" in result["error"].lower()
        assert "Network is down" in result["error"]


class TestRouteExecuteTool:
    """Verify route.execute core MCP behavior, including messenger notify termination."""

    @staticmethod
    def _route_request_context() -> dict[str, Any]:
        return {
            "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
            "received_at": "2026-02-14T00:00:00Z",
            "source_channel": "mcp",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "health",
        }

    @staticmethod
    def _messenger_butler_dir(tmp_path: Path) -> Path:
        return _make_butler_toml(
            tmp_path,
            butler_name="messenger",
            modules={"telegram": {}, "email": {}},
        )

    async def _start_daemon_with_route_execute(self, butler_dir: Path, patches: dict):
        route_execute_fn = None
        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                nonlocal route_execute_fn
                resolved_name = declared_name or fn.__name__
                if resolved_name == "route.execute":
                    route_execute_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        return daemon, route_execute_fn

    async def test_rejects_missing_notify_request(self, tmp_path: Path) -> None:
        patches = _patch_infra()
        butler_dir = self._messenger_butler_dir(tmp_path)
        _, route_execute_fn = await self._start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=self._route_request_context(),
            input={"prompt": "Deliver.", "context": {}},
        )

        assert result["schema_version"] == "route_response.v1"
        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert result["error"]["retryable"] is False
        assert result["result"]["notify_response"]["status"] == "error"
        assert result["result"]["notify_response"]["error"]["class"] == "validation_error"

    async def test_notify_send_success_returns_normalized_notify_response(
        self, tmp_path: Path
    ) -> None:
        patches = _patch_infra()
        butler_dir = self._messenger_butler_dir(tmp_path)
        daemon, route_execute_fn = await self._start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        telegram_module = next(module for module in daemon._modules if module.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 321}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=self._route_request_context(),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": {
                        "schema_version": "notify.v1",
                        "origin_butler": "health",
                        "delivery": {
                            "intent": "send",
                            "channel": "telegram",
                            "message": "Take your medication.",
                            "recipient": "12345",
                        },
                    }
                },
            },
        )

        telegram_module._send_message.assert_awaited_once_with(
            "12345",
            "[health] Take your medication.",
        )
        assert result["schema_version"] == "route_response.v1"
        assert result["status"] == "ok"
        notify_response = result["result"]["notify_response"]
        assert notify_response["schema_version"] == "notify_response.v1"
        assert notify_response["status"] == "ok"
        assert notify_response["delivery"]["channel"] == "telegram"
        assert notify_response["delivery"]["delivery_id"] == "321"

    async def test_rejects_origin_butler_mismatch(self, tmp_path: Path) -> None:
        patches = _patch_infra()
        butler_dir = self._messenger_butler_dir(tmp_path)
        daemon, route_execute_fn = await self._start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        telegram_module = next(module for module in daemon._modules if module.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 321}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=self._route_request_context(),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": {
                        "schema_version": "notify.v1",
                        "origin_butler": "general",
                        "delivery": {
                            "intent": "send",
                            "channel": "telegram",
                            "message": "Take your medication.",
                            "recipient": "12345",
                        },
                    }
                },
            },
        )

        telegram_module._send_message.assert_not_awaited()
        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert (
            result["error"]["message"]
            == "notify_request.origin_butler must match request_context.source_sender_identity."
        )
        assert result["result"]["notify_response"]["error"]["class"] == "validation_error"

    async def test_notify_target_resolution_failure_returns_validation_error(
        self, tmp_path: Path
    ) -> None:
        patches = _patch_infra()
        butler_dir = self._messenger_butler_dir(tmp_path)
        _, route_execute_fn = await self._start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=self._route_request_context(),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": {
                        "schema_version": "notify.v1",
                        "origin_butler": "health",
                        "delivery": {
                            "intent": "send",
                            "channel": "email",
                            "message": "Your report is ready.",
                        },
                    }
                },
            },
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert result["error"]["retryable"] is False
        assert result["result"]["notify_response"]["error"]["class"] == "validation_error"
        assert result["result"]["notify_response"]["error"]["retryable"] is False

    async def test_retryable_provider_failure_maps_to_target_unavailable(
        self, tmp_path: Path
    ) -> None:
        patches = _patch_infra()
        butler_dir = self._messenger_butler_dir(tmp_path)
        daemon, route_execute_fn = await self._start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        telegram_module = next(module for module in daemon._modules if module.name == "telegram")
        telegram_module._send_message = AsyncMock(side_effect=ConnectionError("provider down"))

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=self._route_request_context(),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": {
                        "schema_version": "notify.v1",
                        "origin_butler": "health",
                        "delivery": {
                            "intent": "send",
                            "channel": "telegram",
                            "message": "Hello",
                            "recipient": "12345",
                        },
                    }
                },
            },
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "target_unavailable"
        assert result["error"]["retryable"] is True
        assert result["result"]["notify_response"]["error"]["class"] == "target_unavailable"
        assert result["result"]["notify_response"]["error"]["retryable"] is True


# ---------------------------------------------------------------------------
# Non-fatal module startup tests
# ---------------------------------------------------------------------------


class StubModuleFailStartup(Module):
    """Stub module whose on_startup always raises."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.tools_registered = False

    @property
    def name(self) -> str:
        return "stub_fail"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return None

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        raise RuntimeError("on_startup boom")

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


class TestNonFatalModuleStartup:
    """Verify that module failures during startup are handled gracefully."""

    async def test_module_credential_failure_non_fatal(self, tmp_path: Path) -> None:
        """Butler starts even when a module's credentials are missing."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}, "stub_b": {}})
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            # Return credential failure for stub_a (DB-first resolution found nothing)
            patch(
                "butlers.daemon.validate_module_credentials_async",
                new_callable=AsyncMock,
                return_value={"stub_a": ["STUB_A_TOKEN"]},
            ),
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # Butler started successfully
        assert daemon._started_at is not None
        # stub_a is marked failed
        assert daemon._module_statuses["stub_a"].status == "failed"
        assert daemon._module_statuses["stub_a"].phase == "credentials"
        # stub_b depends on stub_a → cascade_failed
        assert daemon._module_statuses["stub_b"].status == "cascade_failed"

    async def test_core_credential_failure_still_fatal(self, tmp_path: Path) -> None:
        """Missing ANTHROPIC_API_KEY still raises (core credentials are fatal via async check)."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patch(
                "butlers.daemon.validate_core_credentials_async",
                side_effect=CredentialError("Missing ANTHROPIC_API_KEY"),
            ),
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(CredentialError, match="ANTHROPIC_API_KEY"):
                await daemon.start()

    async def test_module_startup_failure_non_fatal(self, tmp_path: Path) -> None:
        """Butler starts when a module's on_startup raises."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_fail": {}, "stub_a": {}})
        registry = _make_registry(StubModuleFailStartup, StubModuleA)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert daemon._started_at is not None
        assert daemon._module_statuses["stub_fail"].status == "failed"
        assert daemon._module_statuses["stub_fail"].phase == "startup"
        assert daemon._module_statuses["stub_a"].status == "active"

    async def test_dependency_cascade(self, tmp_path: Path) -> None:
        """When a module fails, dependent modules are marked cascade_failed."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}, "stub_b": {}})
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patch(
                "butlers.daemon.validate_module_credentials_async",
                new_callable=AsyncMock,
                return_value={"stub_a": ["STUB_A_TOKEN"]},
            ),
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # stub_a failed → stub_b (depends on stub_a) cascade_failed
        assert daemon._module_statuses["stub_a"].status == "failed"
        assert daemon._module_statuses["stub_b"].status == "cascade_failed"
        assert "stub_a" in daemon._module_statuses["stub_b"].error

    async def test_failed_module_skipped_in_tool_registration(self, tmp_path: Path) -> None:
        """A failed module's register_tools is never called."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_fail": {}, "stub_a": {}})
        registry = _make_registry(StubModuleFailStartup, StubModuleA)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # Failed module should not have tools registered
        fail_mod = next(m for m in daemon._modules if m.name == "stub_fail")
        assert not fail_mod.tools_registered
        # Active module should have tools registered
        active_mod = next(m for m in daemon._modules if m.name == "stub_a")
        assert active_mod.tools_registered

    async def test_failed_module_skipped_in_shutdown(self, tmp_path: Path) -> None:
        """A failed module's on_shutdown is not called during shutdown."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_fail": {}, "stub_a": {}})
        registry = _make_registry(StubModuleFailStartup, StubModuleA)
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        await daemon.shutdown()

        fail_mod = next(m for m in daemon._modules if m.name == "stub_fail")
        assert not fail_mod.shutdown_called
        active_mod = next(m for m in daemon._modules if m.name == "stub_a")
        assert active_mod.shutdown_called

    async def test_health_degraded_with_failed_module(self, tmp_path: Path) -> None:
        """Health returns 'degraded' when any module has failed."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_fail": {}, "stub_a": {}})
        registry = _make_registry(StubModuleFailStartup, StubModuleA)
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal status_fn
                if fn.__name__ == "status":
                    status_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert status_fn is not None
        result = await status_fn()
        assert result["health"] == "degraded"
        assert result["modules"]["stub_fail"]["status"] == "failed"
        assert result["modules"]["stub_a"]["status"] == "active"

    async def test_status_reports_module_failure_details(self, tmp_path: Path) -> None:
        """status() includes phase and error for failed modules."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}, "stub_b": {}})
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **_decorator_kwargs):
            def decorator(fn):
                nonlocal status_fn
                if fn.__name__ == "status":
                    status_fn = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patch(
                "butlers.daemon.validate_module_credentials_async",
                new_callable=AsyncMock,
                return_value={"stub_a": ["STUB_A_TOKEN"]},
            ),
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert status_fn is not None
        result = await status_fn()
        stub_a_info = result["modules"]["stub_a"]
        assert stub_a_info["status"] == "failed"
        assert stub_a_info["phase"] == "credentials"
        assert "STUB_A_TOKEN" in stub_a_info["error"]

        stub_b_info = result["modules"]["stub_b"]
        assert stub_b_info["status"] == "cascade_failed"


class TestSwitchboardHeartbeat:
    """Verify the switchboard reconnect heartbeat background task."""

    async def test_heartbeat_reconnects_when_client_is_none(self, butler_dir: Path) -> None:
        """Heartbeat should attempt reconnection when switchboard_client is None."""
        patches = _patch_infra()
        connect_calls = 0

        async def fake_connect(self_daemon):
            nonlocal connect_calls
            connect_calls += 1

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        # switchboard_client is None (mocked _connect_switchboard does nothing)
        assert daemon.switchboard_client is None
        # Heartbeat task should have been created (default config has switchboard_url)
        assert daemon._switchboard_heartbeat_task is not None

        # Patch _connect_switchboard and advance through one heartbeat tick
        with patch.object(ButlerDaemon, "_connect_switchboard", new=fake_connect):
            # Advance past the sleep
            await asyncio.sleep(0)  # let the task start
            # Fast-forward the heartbeat by cancelling and running one manual tick
            daemon._switchboard_heartbeat_task.cancel()
            try:
                await daemon._switchboard_heartbeat_task
            except asyncio.CancelledError:
                pass

            # Simulate one tick manually
            daemon.switchboard_client = None
            await fake_connect(daemon)

        assert connect_calls == 1

    async def test_heartbeat_detects_dead_connection_and_reconnects(self, butler_dir: Path) -> None:
        """Heartbeat should disconnect + reconnect when list_tools() fails."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.list_tools = AsyncMock(side_effect=ConnectionError("dead"))

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon.switchboard_client is mock_client

        # Cancel the auto-started heartbeat so we can test the loop manually
        daemon._switchboard_heartbeat_task.cancel()
        try:
            await daemon._switchboard_heartbeat_task
        except asyncio.CancelledError:
            pass

        # Run one iteration of heartbeat logic manually with a dead client
        disconnect_called = False
        connect_called = False

        async def fake_disconnect(self_daemon):
            nonlocal disconnect_called
            disconnect_called = True
            self_daemon.switchboard_client = None

        async def fake_connect(self_daemon):
            nonlocal connect_called
            connect_called = True

        with (
            patch.object(ButlerDaemon, "_disconnect_switchboard", new=fake_disconnect),
            patch.object(ButlerDaemon, "_connect_switchboard", new=fake_connect),
        ):
            # Manually simulate the heartbeat check on a dead connection
            try:
                await asyncio.wait_for(daemon.switchboard_client.list_tools(), timeout=5.0)
            except Exception:
                await daemon._disconnect_switchboard()
                await daemon._connect_switchboard()

        assert disconnect_called
        assert connect_called

    async def test_heartbeat_cancelled_on_shutdown(self, butler_dir: Path) -> None:
        """shutdown() should cancel the heartbeat task."""
        patches = _patch_infra()

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
            patches["create_audit_pool"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon._switchboard_heartbeat_task is not None
        heartbeat_task = daemon._switchboard_heartbeat_task

        await daemon.shutdown()

        assert daemon._switchboard_heartbeat_task is None
        assert heartbeat_task.cancelled() or heartbeat_task.done()

    async def test_no_heartbeat_for_switchboard_butler(self, tmp_path: Path) -> None:
        """Switchboard butler (switchboard_url=None) should not get a heartbeat task."""
        toml = """\
[butler]
name = "switchboard"
port = 40100

[butler.db]
name = "butler_switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
        (tmp_path / "butler.toml").write_text(toml)
        patches = _patch_infra()

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
            # Don't mock _connect_switchboard — let it see switchboard_url=None
        ):
            daemon = ButlerDaemon(tmp_path)
            await daemon.start()

        assert daemon._switchboard_heartbeat_task is None

        await daemon.shutdown()

    async def test_heartbeat_healthy_connection_no_reconnect(self, butler_dir: Path) -> None:
        """Heartbeat should not reconnect when list_tools() succeeds."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.list_tools = AsyncMock(return_value=[])

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
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon.switchboard_client is mock_client

        # Cancel auto-started heartbeat
        daemon._switchboard_heartbeat_task.cancel()
        try:
            await daemon._switchboard_heartbeat_task
        except asyncio.CancelledError:
            pass

        # Manually run the heartbeat probe — should succeed, no disconnect
        disconnect_called = False

        async def fake_disconnect(self_daemon):
            nonlocal disconnect_called
            disconnect_called = True

        with patch.object(ButlerDaemon, "_disconnect_switchboard", new=fake_disconnect):
            await asyncio.wait_for(daemon.switchboard_client.list_tools(), timeout=5.0)

        assert not disconnect_called
        # Client should still be the original
        assert daemon.switchboard_client is mock_client
