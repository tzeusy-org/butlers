"""Tests for the ButlerDaemon class.

Uses extensive mocking to avoid real DB, FastMCP, and runtime dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.credentials import CredentialError
from butlers.daemon import ButlerDaemon, RuntimeBinaryNotFoundError
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

    async def on_startup(self, config: Any, db: Any) -> None:
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

    async def on_startup(self, config: Any, db: Any) -> None:
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
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
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
        "mock_adapter_cls": mock_adapter_cls,
        "mock_adapter": mock_adapter,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartupSequence:
    """Verify the startup sequence executes in the documented order."""

    async def test_startup_calls_in_order(self, butler_dir: Path) -> None:
        """Steps 1-13 should execute in documented order."""
        patches = _patch_infra()
        call_order: list[str] = []

        with (
            patches["db_from_env"] as mock_from_env,
            patches["run_migrations"] as mock_migrations,
            patches["validate_credentials"] as mock_validate,
            patches["init_telemetry"] as mock_telemetry,
            patches["sync_schedules"] as mock_sync,
            patches["FastMCP"] as mock_fastmcp,
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"] as mock_start_server,
            patches["connect_switchboard"],
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
            "Spawner",
            "sync_schedules",
            "FastMCP",
            "start_mcp_server",
        ]
        # Filter to only expected items (there may be extra calls)
        filtered = [c for c in call_order if c in expected_order]
        assert filtered == expected_order

    async def test_config_loaded(self, butler_dir: Path) -> None:
        """After start(), config should be populated from butler.toml."""
        patches = _patch_infra()
        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
            before = time.monotonic()
            await daemon.start()
            after = time.monotonic()

        assert daemon._started_at is not None
        assert before <= daemon._started_at <= after


class TestCoreToolRegistration:
    """Verify all expected core MCP tools are registered."""

    EXPECTED_TOOLS = {
        "status",
        "trigger",
        "tick",
        "state_get",
        "state_set",
        "state_delete",
        "state_list",
        "schedule_list",
        "schedule_create",
        "schedule_update",
        "schedule_delete",
        "sessions_list",
        "sessions_get",
        "notify",
    }

    async def test_all_core_tools_registered(self, butler_dir: Path) -> None:
        """All 14 core tools should be registered on FastMCP via @mcp.tool()."""
        patches = _patch_infra()
        registered_tools: list[str] = []

        # Create a mock FastMCP that captures tool registrations
        mock_mcp = MagicMock()

        def tool_decorator():
            def decorator(fn):
                registered_tools.append(fn.__name__)
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
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

        def tool_decorator():
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
        """_start_mcp_server should create a uvicorn server with SSE transport."""
        patches = _patch_infra()

        mock_mcp = MagicMock()
        mock_app = MagicMock()
        mock_mcp.http_app.return_value = mock_app

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = AsyncMock()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
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

            # Verify http_app was called with SSE transport
            mock_mcp.http_app.assert_called_once_with(transport="sse")

            # Verify uvicorn.Config was created with correct parameters
            mock_config_cls.assert_called_once_with(
                mock_app,
                host="0.0.0.0",
                port=9100,
                log_level="info",
                timeout_graceful_shutdown=0,
            )

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
        mock_app = MagicMock()
        mock_mcp.http_app.return_value = mock_app

        mock_uvicorn_server = MagicMock()
        mock_uvicorn_server.serve = mock_serve

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
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


class TestStatusTool:
    """Verify the status() MCP tool returns correct data."""

    async def test_status_returns_butler_info(self, butler_dir: Path) -> None:
        """status() should return name, description, port, modules, health, uptime."""
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator():
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert status_fn is not None, "status tool was not registered"

        result = await status_fn()
        assert result["name"] == "test-butler"
        assert result["description"] == "A test butler"
        assert result["port"] == 9100
        assert result["modules"] == []
        assert result["health"] == "ok"
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] >= 0

    async def test_status_includes_module_names(self, butler_dir_with_modules: Path) -> None:
        """status() should list loaded module names."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()
        status_fn = None

        mock_mcp = MagicMock()

        def tool_decorator():
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        assert status_fn is not None
        result = await status_fn()
        assert set(result["modules"]) == {"stub_a", "stub_b"}


class TestHealthCheck:
    """Verify dynamic health checking in the status() MCP tool."""

    async def _get_status_fn(self, butler_dir, patches):
        """Helper to start daemon and extract the status function."""
        status_fn = None
        mock_mcp = MagicMock()

        def tool_decorator():
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
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
        assert result["modules"] == []
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
                side_effect=CredentialError("missing ANTHROPIC_API_KEY"),
            ),
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
            with pytest.raises(CredentialError, match="missing ANTHROPIC_API_KEY"):
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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
            patches["init_telemetry"],
            patches["sync_schedules"] as mock_sync,
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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


class TestModuleCredentials:
    """Verify module credentials are collected and passed to validation."""

    async def test_module_creds_passed_to_validate(self, butler_dir_with_modules: Path) -> None:
        """validate_credentials should receive module credential env vars."""
        registry = _make_registry(StubModuleA, StubModuleB)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"] as mock_validate,
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        # Third argument (module_credentials keyword)
        module_creds = call_kwargs.kwargs.get(
            "module_credentials", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        )
        assert module_creds is not None
        assert "stub_a" in module_creds
        assert "STUB_A_TOKEN" in module_creds["stub_a"]


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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        module_creds = call_kwargs.kwargs.get(
            "module_credentials", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        )
        assert module_creds is not None
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        module_creds = call_kwargs.kwargs.get(
            "module_credentials", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        )
        assert module_creds is not None
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        module_creds = call_kwargs.kwargs.get(
            "module_credentials", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        )
        assert module_creds is not None
        # TOML declares empty list — should be used (not fall back to class)
        assert "stub_a" in module_creds
        assert module_creds["stub_a"] == []

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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        module_creds = call_kwargs.kwargs.get(
            "module_credentials", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        )
        assert module_creds is not None
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mock_spawner_cls.assert_called_once()
        spawner_kwargs = mock_spawner_cls.call_args.kwargs
        assert spawner_kwargs["module_credentials_env"] == {"stub_a": ["TOML_KEY"]}

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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"] as mock_spawner_cls,
            patches["get_adapter"],
            patches["shutil_which"],
            patches["connect_switchboard"],
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
            port=8100,
            db_name="butler_switchboard",
        )
        registry = _make_registry(TelegramModule, EmailModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patch.object(TelegramModule, "on_startup", new_callable=AsyncMock),
            patch.object(EmailModule, "on_startup", new_callable=AsyncMock),
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        email_module = next(m for m in daemon._modules if m.name == "email")

        assert isinstance(telegram_module, TelegramModule)
        assert isinstance(email_module, EmailModule)
        assert isinstance(telegram_module._pipeline, MessagePipeline)
        assert email_module._pipeline is telegram_module._pipeline
        assert telegram_module._pipeline._pool is patches["mock_pool"]
        assert telegram_module._pipeline._source_butler == "switchboard"
        assert telegram_module._pipeline._dispatch_fn is daemon.spawner.trigger
        assert telegram_module._pipeline._dispatch_fn is patches["mock_spawner"].trigger

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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patch.object(TelegramModule, "on_startup", new_callable=AsyncMock),
            patch.object(EmailModule, "on_startup", new_callable=AsyncMock),
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        email_module = next(m for m in daemon._modules if m.name == "email")

        assert isinstance(telegram_module, TelegramModule)
        assert isinstance(email_module, EmailModule)
        assert telegram_module._pipeline is None
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
port = 8100

[butler.db]
name = "butler_switchboard"
"""
        (tmp_path / "butler.toml").write_text(toml)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
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
port = 8103

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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
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

        def tool_decorator():
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
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        return daemon, notify_fn

    async def test_notify_registered_as_core_tool(self, butler_dir: Path) -> None:
        """notify should be registered as a core MCP tool."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None, "notify tool was not registered"

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

        result = await notify_fn(channel="telegram", message="Hello world")

        assert result["status"] == "ok"
        assert result["result"] == {"notification_id": "abc-123", "status": "sent"}

        # Verify call_tool was called with correct args
        mock_client.call_tool.assert_awaited_once_with(
            "deliver",
            {
                "channel": "telegram",
                "message": "Hello world",
                "source_butler": "test-butler",
            },
        )

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
        mock_client.call_tool.assert_awaited_once_with(
            "deliver",
            {
                "channel": "email",
                "message": "Weekly report",
                "recipient": "user@example.com",
                "source_butler": "test-butler",
            },
        )

    async def test_notify_without_recipient(self, butler_dir: Path) -> None:
        """notify without recipient should omit it from the Switchboard call."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "ghi-789", "status": "sent"}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
        daemon.switchboard_client = mock_client

        result = await notify_fn(channel="telegram", message="Alert")

        assert result["status"] == "ok"

        # Verify recipient is NOT in the call args
        call_args = mock_client.call_tool.call_args
        deliver_args = call_args[0][1]
        assert "recipient" not in deliver_args

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

        result = await notify_fn(channel="telegram", message="Hello")

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
        result = await notify_fn(channel="telegram", message="Hello")

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

        result = await notify_fn(channel="telegram", message="Hello")

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

        await notify_fn(channel="telegram", message="Test")

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

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(999)

        mock_client = AsyncMock()
        mock_client.call_tool = slow_call
        daemon.switchboard_client = mock_client

        # The timeout is a local variable inside notify, so we mock
        # asyncio.wait_for to raise TimeoutError directly.
        with patch("butlers.daemon.asyncio.wait_for", side_effect=TimeoutError()):
            result = await notify_fn(channel="telegram", message="Hello")

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

        result = await notify_fn(channel="telegram", message="Hello")
        assert result["status"] == "error"
        assert "unreachable" in result["error"].lower()
        assert "Connection refused" in result["error"]

        # Test with OSError (parent class of ConnectionError)
        mock_client.call_tool = AsyncMock(side_effect=OSError("Network is down"))

        result = await notify_fn(channel="telegram", message="Hello")
        assert result["status"] == "error"
        assert "unreachable" in result["error"].lower()
        assert "Network is down" in result["error"]
