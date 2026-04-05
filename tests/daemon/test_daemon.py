"""Tests for the ButlerDaemon class — condensed.

Covers:
- Startup sequence ordering
- Core and module tool registration
- Shutdown sequence (module order, DB close, MCP server)
- MCP server startup (routes, background task, port conflict)
- SSE disconnect guard
- Status tool (info fields, health conditions)
- Startup failure propagation
- Schedule sync
- Module credentials (TOML override, class fallback, identity-scoped)
- Secret detection
- Config flatten helper
- Runtime binary check
- Switchboard client lifecycle (connect, disconnect, heartbeat)
- notify() tool behavior
- route.execute tool behavior
- Non-fatal module startup failures
- Staffer briefing exclusion
- Switchboard registration type field
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        raise RuntimeError("on_startup boom")

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


# ---------------------------------------------------------------------------
# Fixtures and helpers
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
    db_name: str = "butlers",
) -> Path:
    """Write a minimal butler.toml in tmp_path and return the directory."""
    modules = modules or {}
    schema = butler_name.replace("-", "_")
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        f'name = "{db_name}"',
        f'schema = "{schema}"',
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
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    mock_sock = MagicMock()

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "socket": patch("butlers.daemon.socket.socket", return_value=mock_sock),
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
        "mock_sock": mock_sock,
    }


async def _start_daemon(butler_dir: Path, patches: dict, **kwargs) -> ButlerDaemon:
    """Start a daemon with all standard patches applied."""
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir, **kwargs)
        await daemon.start()
    return daemon


# ---------------------------------------------------------------------------
# Startup sequence
# ---------------------------------------------------------------------------


async def test_startup_sequence(butler_dir: Path) -> None:
    """Key startup stages execute in documented order; config and started_at are set."""
    patches = _patch_infra()
    call_order: list[str] = []

    with (
        patches["db_from_env"] as mock_from_env,
        patches["run_migrations"] as mock_migrations,
        patches["validate_credentials"] as mock_validate,
        patches["validate_module_credentials"] as mock_mod_validate,
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
        before = time.monotonic()
        await daemon.start()
        after = time.monotonic()

    # Config is loaded
    assert daemon.config is not None
    assert daemon.config.name == "test-butler"
    assert daemon.config.port == 9100

    # started_at is recorded
    assert daemon._started_at is not None
    assert before <= daemon._started_at <= after

    # Order: key milestones in correct sequence
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
    filtered = [c for c in call_order if c in expected_order]
    pos = 0
    for expected in expected_order:
        assert expected in filtered[pos:]
        pos = filtered.index(expected, pos) + 1


# ---------------------------------------------------------------------------
# Core and module tool registration
# ---------------------------------------------------------------------------


async def test_all_core_tools_registered(butler_dir: Path) -> None:
    """All core tools should be registered on FastMCP via @mcp.tool()."""
    patches = _patch_infra()
    registered_tools: list[str] = []

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
        daemon = ButlerDaemon(butler_dir, registry=ModuleRegistry())
        await daemon.start()

    assert set(registered_tools) == CORE_TOOL_NAMES


async def test_trigger_tool_uses_trigger_source_contract(butler_dir: Path) -> None:
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


async def test_module_lifecycle(butler_dir_with_modules: Path) -> None:
    """Module tools registered, order is topological, on_startup called, migrations run."""
    registry = _make_registry(StubModuleA, StubModuleB)
    patches = _patch_infra()

    with (
        patches["db_from_env"],
        patches["run_migrations"] as mock_migrations,
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
        await daemon.start()

    # Tools registered for all modules
    for mod in daemon._modules:
        assert mod.tools_registered, f"Module {mod.name} tools not registered"

    # on_startup called for all modules
    for mod in daemon._modules:
        assert mod.started, f"Module {mod.name} on_startup not called"

    # Topological order: stub_a before stub_b
    module_names = [m.name for m in daemon._modules]
    assert module_names.index("stub_a") < module_names.index("stub_b")

    # Migrations run for core and stub_a (stub_b returns None)
    migration_chains = [
        c.kwargs.get("chain", c.args[1] if len(c.args) > 1 else None)
        for c in mock_migrations.call_args_list
    ]
    assert "core" in migration_chains
    assert "stub_a" in migration_chains


# ---------------------------------------------------------------------------
# Shutdown sequence
# ---------------------------------------------------------------------------



async def test_shutdown_stops_mcp_server(butler_dir: Path) -> None:
    """shutdown() signals uvicorn to exit, awaits server task; reverse-topological module shutdown."""
    # MCP server shutdown: signals uvicorn and awaits task
    patches = _patch_infra()
    daemon = await _start_daemon(butler_dir, patches)

    mock_server = MagicMock()
    mock_server.should_exit = False

    async def _fake_serve():
        pass

    mock_task = asyncio.ensure_future(_fake_serve())
    await mock_task

    daemon._server = mock_server
    daemon._server_task = mock_task

    await daemon.shutdown()

    assert mock_server.should_exit is True
    assert mock_task.done()
    assert daemon._server is None
    assert daemon._server_task is None

    # Module shutdown: reverse topological order, errors don't abort shutdown
    registry = _make_registry(StubModuleA, StubModuleB)
    shutdown_mods_dir = butler_dir.parent / "shutdown_mods"
    shutdown_mods_dir.mkdir(exist_ok=True)
    butler_dir_m = _make_butler_toml(shutdown_mods_dir,
                                     modules={"stub_a": {}, "stub_b": {}})
    patches2 = _patch_infra()
    mock_db2 = patches2["mock_db"]
    shutdown_order: list[str] = []

    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patches2["connect_switchboard"],
        patches2["create_audit_pool"],
        patches2["recover_route_inbox"],
    ):
        daemon2 = ButlerDaemon(butler_dir_m, registry=registry)
        await daemon2.start()

    for mod in daemon2._modules:
        original = mod.on_shutdown
        if mod.name == "stub_b":
            async def failing_shutdown():
                shutdown_order.append("stub_b")
                raise RuntimeError("shutdown failed")
            mod.on_shutdown = failing_shutdown
        else:
            async def make_tracker(name=mod.name, orig=original):
                shutdown_order.append(name)
                await orig()
            mod.on_shutdown = make_tracker  # type: ignore[method-assign]

    await daemon2.shutdown()
    assert "stub_b" in shutdown_order
    mock_db2.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# MCP server startup
# ---------------------------------------------------------------------------


async def test_start_mcp_server(butler_dir: Path) -> None:
    """_start_mcp_server: builds correct routes, runs as background task, raises on port conflict."""
    # Part 1: correct routes, uvicorn config, socket setup
    patches = _patch_infra()
    mock_mcp = MagicMock()
    streamable_app = FastAPI()
    streamable_app.add_api_route("/mcp", endpoint=lambda: None, methods=["GET", "POST", "DELETE"])
    sse_app = FastAPI()
    sse_app.add_api_route("/sse", endpoint=lambda: None, methods=["GET"])
    sse_app.mount("/messages", app=FastAPI())
    mock_mcp.http_app.side_effect = [streamable_app, sse_app]

    mock_uvicorn_server = MagicMock()
    mock_uvicorn_server.serve = AsyncMock()
    mock_sock = MagicMock()

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patch("butlers.daemon.uvicorn.Config") as mock_config_cls,
        patch("butlers.daemon.uvicorn.Server", return_value=mock_uvicorn_server),
        patch("butlers.daemon.socket.socket", return_value=mock_sock),
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

        assert mock_mcp.http_app.call_args_list == [
            call(path="/mcp", transport="streamable-http"),
            call(path="/sse", transport="sse"),
        ]
        mock_config_cls.assert_called_once()
        args, kwargs = mock_config_cls.call_args
        wrapped_app = args[0]
        assert isinstance(wrapped_app, _McpSseDisconnectGuard)
        route_map = {route.path: route for route in wrapped_app._app.routes}
        assert "/mcp" in route_map
        assert "/sse" in route_map
        assert "/messages" in route_map
        assert type(route_map["/messages"]).__name__ == "Mount"
        assert wrapped_app._butler_name == "test-butler"
        assert kwargs == {
            "host": "0.0.0.0",
            "port": 9100,
            "log_level": "warning",
            "timeout_graceful_shutdown": 0,
        }
        mock_sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mock_sock.bind.assert_called_once_with(("0.0.0.0", 9100))
        mock_uvicorn_server.serve.assert_called_once_with(sockets=[mock_sock])

    assert daemon._server is mock_uvicorn_server
    assert daemon._server_task is not None
    daemon._server_task.cancel()
    try:
        await daemon._server_task
    except asyncio.CancelledError:
        pass

    # Part 2: server runs as background task (does not block start())
    patches2 = _patch_infra()

    async def mock_serve(sockets=None):
        await asyncio.sleep(999)

    mock_mcp2 = MagicMock()
    streamable_app2 = FastAPI()
    streamable_app2.add_api_route("/mcp", endpoint=lambda: None, methods=["GET", "POST"])
    sse_app2 = FastAPI()
    sse_app2.add_api_route("/sse", endpoint=lambda: None, methods=["GET"])
    sse_app2.mount("/messages", app=FastAPI())
    mock_mcp2.http_app.side_effect = [streamable_app2, sse_app2]
    mock_uvicorn2 = MagicMock()
    mock_uvicorn2.serve = mock_serve

    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp2),
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patch("butlers.daemon.uvicorn.Config"),
        patch("butlers.daemon.uvicorn.Server", return_value=mock_uvicorn2),
        patch("butlers.daemon.socket.socket", return_value=MagicMock()),
    ):
        daemon2 = ButlerDaemon(butler_dir)
        await daemon2.start()

    assert daemon2._server_task is not None
    assert not daemon2._server_task.done()
    assert daemon2._started_at is not None
    daemon2._server_task.cancel()
    try:
        await daemon2._server_task
    except asyncio.CancelledError:
        pass

    # Part 3: port already in use raises OSError
    patches3 = _patch_infra()
    mock_mcp3 = MagicMock()
    streamable_app3 = FastAPI()
    streamable_app3.add_api_route("/mcp", endpoint=lambda: None, methods=["GET", "POST"])
    sse_app3 = FastAPI()
    sse_app3.add_api_route("/sse", endpoint=lambda: None, methods=["GET"])
    sse_app3.mount("/messages", app=FastAPI())
    mock_mcp3.http_app.side_effect = [streamable_app3, sse_app3]
    mock_sock3 = MagicMock()
    mock_sock3.bind.side_effect = OSError(98, "address already in use")

    with (
        patches3["db_from_env"],
        patches3["run_migrations"],
        patches3["validate_credentials"],
        patches3["validate_module_credentials"],
        patches3["init_telemetry"],
        patches3["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp3),
        patches3["Spawner"],
        patches3["get_adapter"],
        patches3["shutil_which"],
        patch("butlers.daemon.uvicorn.Config"),
        patch("butlers.daemon.uvicorn.Server"),
        patch("butlers.daemon.socket.socket", return_value=mock_sock3),
    ):
        daemon3 = ButlerDaemon(butler_dir)
        with pytest.raises(OSError, match="address already in use"):
            await daemon3.start()


def test_build_mcp_http_app_routes_and_health() -> None:
    """Combined app keeps SSE routes, serves streamable HTTP at /mcp, and /health returns 200."""
    mcp = RuntimeFastMCP("test-butler")
    app = ButlerDaemon._build_mcp_http_app(mcp, butler_name="test-butler")

    assert isinstance(app, _McpSseDisconnectGuard)
    route_map = {(type(route).__name__, route.path): route for route in app._app.routes}
    assert ("Route", "/mcp") in route_map
    assert ("Route", "/sse") in route_map
    assert ("Route", "/health") in route_map
    assert ("Mount", "/messages") in route_map
    assert route_map[("Route", "/mcp")].methods is None
    assert route_map[("Route", "/sse")].methods == {"GET", "HEAD"}

    with TestClient(app) as client:
        # Wrong Content-Type returns 400
        bad_response = client.post(
            "/mcp",
            headers={"accept": "application/json, text/event-stream", "content-type": "text/plain"},
            content="{}",
        )
        assert bad_response.status_code == 400

        # Health endpoint returns 200
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# SSE disconnect guard
# ---------------------------------------------------------------------------


async def test_sse_disconnect_guard_suppresses_messages_post(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ClientDisconnect on /messages/ POST is suppressed with 202."""
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
        {"type": "http.response.start", "status": 202, "headers": [(b"content-length", b"0")]},
        {"type": "http.response.body", "body": b""},
    ]


@pytest.mark.parametrize(
    "exc_type,path,should_bubble",
    [
        (RuntimeError, "/messages/", True),  # non-disconnect on /messages/ bubbles
        (ClientDisconnect, "/health", True),  # disconnect on other path bubbles
    ],
)
async def test_sse_disconnect_guard_bubbles(exc_type, path, should_bubble) -> None:
    """Non-disconnect or non-messages path exceptions bubble through the guard."""
    raised_exc = exc_type("boom") if exc_type is RuntimeError else exc_type()

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise raised_exc

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        return None

    guard = _McpSseDisconnectGuard(inner_app, butler_name="test-butler")
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b""}

    with pytest.raises(exc_type):
        await guard(scope, receive, send)


# ---------------------------------------------------------------------------
# Status tool
# ---------------------------------------------------------------------------


async def _get_status_fn(butler_dir, patches, *, registry=None):
    """Start daemon and extract the status tool function."""
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
        daemon = ButlerDaemon(butler_dir, registry=registry or ModuleRegistry())
        await daemon.start()

    return daemon, status_fn


async def test_status_tool(butler_dir: Path, butler_dir_with_modules: Path) -> None:
    """status() returns correct info fields; with modules lists their names; health=ok on SELECT 1."""
    # No modules: basic fields + health=ok
    patches = _patch_infra()
    patches["mock_pool"].fetchval = AsyncMock(return_value=1)
    _, status_fn = await _get_status_fn(butler_dir, patches)
    assert status_fn is not None
    result = await status_fn()
    assert result["name"] == "test-butler"
    assert result["description"] == "A test butler"
    assert result["port"] == 9100
    assert result["modules"] == {}
    assert result["health"] == "ok"
    assert isinstance(result["uptime_seconds"], float)
    patches["mock_pool"].fetchval.assert_awaited_with("SELECT 1")

    # With modules: module names and active statuses returned
    registry = _make_registry(StubModuleA, StubModuleB)
    patches2 = _patch_infra()
    _, status_fn2 = await _get_status_fn(butler_dir_with_modules, patches2, registry=registry)
    assert status_fn2 is not None
    result2 = await status_fn2()
    assert set(result2["modules"].keys()) == {"stub_a", "stub_b"}
    assert all(v["status"] == "active" for v in result2["modules"].values())


@pytest.mark.parametrize(
    "setup_pool",
    [
        "pool_raises_connection",
        "pool_is_none",
        "db_is_none",
        "pool_timeout",
        "pool_raises_oserror",
    ],
)
async def test_health_degraded(butler_dir: Path, setup_pool) -> None:
    """status() returns health=degraded when DB pool is unhealthy."""
    patches = _patch_infra()
    mock_pool = patches["mock_pool"]

    daemon, status_fn = await _get_status_fn(butler_dir, patches)
    assert status_fn is not None

    if setup_pool == "pool_raises_connection":
        mock_pool.fetchval = AsyncMock(side_effect=ConnectionRefusedError("refused"))
    elif setup_pool == "pool_is_none":
        daemon.db.pool = None
    elif setup_pool == "db_is_none":
        daemon.db = None
    elif setup_pool == "pool_timeout":
        mock_pool.fetchval = AsyncMock(side_effect=TimeoutError())
    elif setup_pool == "pool_raises_oserror":
        mock_pool.fetchval = AsyncMock(side_effect=OSError("pool closed"))

    result = await status_fn()
    assert result["health"] == "degraded"
    # All fields still present even when degraded
    if setup_pool not in ("db_is_none",):
        assert result["name"] == "test-butler"
        assert result["port"] == 9100


# ---------------------------------------------------------------------------
# Startup failure propagation
# ---------------------------------------------------------------------------


async def test_startup_failure_propagation(butler_dir: Path) -> None:
    """Credential failure prevents DB; DB provision failure prevents migrations."""
    # Credential failure: DB never provisioned
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
    mock_from_env.assert_not_called()

    # DB provision failure: migrations not run
    patches2 = _patch_infra()
    patches2["mock_db"].provision.side_effect = ConnectionRefusedError("no pg")
    with (
        patches2["db_from_env"],
        patches2["run_migrations"] as mock_migrations,
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patches2["connect_switchboard"],
        patches2["create_audit_pool"],
        patches2["recover_route_inbox"],
    ):
        daemon2 = ButlerDaemon(butler_dir)
        with pytest.raises(ConnectionRefusedError):
            await daemon2.start()
    mock_migrations.assert_not_awaited()


# ---------------------------------------------------------------------------
# Schedule sync
# ---------------------------------------------------------------------------


async def test_schedules_synced(butler_dir: Path) -> None:
    """sync_schedules should be called with parsed schedule entries."""
    patches = _patch_infra()
    daemon = await _start_daemon(butler_dir, patches)

    # Inspect via mock directly after start
    patches = _patch_infra()
    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
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
    schedules_arg = args[0][1]
    assert len(schedules_arg) == 1
    assert schedules_arg[0]["name"] == "daily-check"
    assert schedules_arg[0]["cron"] == "0 9 * * *"
    assert schedules_arg[0]["prompt"] == "Do the daily check"
    assert args[1]["stagger_key"] == "test-butler"


# ---------------------------------------------------------------------------
# Module credentials
# ---------------------------------------------------------------------------


async def test_module_creds_validated_separately(butler_dir_with_modules: Path) -> None:
    """Module creds validated separately from core; CredentialStore passed to validator."""
    from butlers.credential_store import CredentialStore

    registry = _make_registry(StubModuleA, StubModuleB)
    patches = _patch_infra()
    received_args: list[object] = []

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"] as mock_validate,
        patches["validate_module_credentials"] as mock_mod_validate,
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

    # Core validate does not receive module credentials
    mock_validate.assert_called_once()
    assert "module_credentials" not in mock_validate.call_args.kwargs

    # Module validate receives module env list and CredentialStore
    assert len(received_args) >= 2
    mod_creds_arg = received_args[0]
    assert "stub_a" in mod_creds_arg
    assert "STUB_A_TOKEN" in mod_creds_arg["stub_a"]
    assert isinstance(received_args[1], CredentialStore)


@pytest.mark.parametrize(
    "toml_creds,expected_creds,expect_in_module_creds",
    [
        # TOML override
        (["TOML_TOKEN_A", "TOML_SECRET_A"], ["TOML_TOKEN_A", "TOML_SECRET_A"], True),
        # Empty list → no fallback to class
        ([], [], True),
        # Invalid type → treated as explicitly empty
        (123, [], True),
        # Filter non-strings and empty
        (["TOKEN_A", "", 123, "TOKEN_B"], ["TOKEN_A", "TOKEN_B"], True),
    ],
)
async def test_toml_credentials_override(
    tmp_path: Path, toml_creds, expected_creds, expect_in_module_creds
) -> None:
    """TOML credentials_env overrides class property with various edge cases."""
    butler_dir = _make_butler_toml(
        tmp_path,
        modules={"stub_a": {"credentials_env": toml_creds}},
    )
    registry = _make_registry(StubModuleA)
    patches = _patch_infra()

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
        patches["socket"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    module_creds = daemon._collect_module_credentials()
    assert ("stub_a" in module_creds) is expect_in_module_creds
    if expect_in_module_creds:
        assert module_creds["stub_a"] == expected_creds
    assert "STUB_A_TOKEN" not in module_creds.get("stub_a", [])


async def test_class_credentials_fallback_and_spawner_forwarding(
    butler_dir_with_modules: Path, tmp_path: Path
) -> None:
    """Class creds used as fallback; TOML creds override and are forwarded to Spawner."""
    # Class fallback: no TOML credentials_env → class property used
    registry = _make_registry(StubModuleA, StubModuleB)
    patches = _patch_infra()

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
        patches["socket"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir_with_modules, registry=registry)
        await daemon.start()

    module_creds = daemon._collect_module_credentials()
    assert "stub_a" in module_creds
    assert module_creds["stub_a"] == ["STUB_A_TOKEN"]
    assert "stub_b" not in module_creds

    # TOML override forwarded to Spawner
    (tmp_path / "toml_spawner").mkdir(exist_ok=True)
    butler_dir2 = _make_butler_toml(
        tmp_path / "toml_spawner",
        modules={"stub_a": {"credentials_env": ["TOML_KEY"]}},
    )
    registry2 = _make_registry(StubModuleA)
    patches2 = _patch_infra()

    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"] as mock_spawner_cls,
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["socket"],
        patches2["connect_switchboard"],
        patches2["create_audit_pool"],
        patches2["recover_route_inbox"],
    ):
        daemon2 = ButlerDaemon(butler_dir2, registry=registry2)
        await daemon2.start()

    mock_spawner_cls.assert_called_once()
    assert mock_spawner_cls.call_args.kwargs["module_credentials_env"] == {"stub_a": ["TOML_KEY"]}


async def test_identity_scoped_credentials_are_collected(tmp_path: Path) -> None:
    """Identity-scoped user/bot env vars are collected with scope-qualified sources."""
    (tmp_path / "butler.toml").write_text(
        """
[butler]
name = "switchboard"
port = 9100
description = "A test butler"

[butler.db]
name = "butlers"
schema = "switchboard"

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
        patches["validate_credentials"],
        patches["validate_module_credentials"],
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

    module_creds = daemon._collect_module_credentials()
    assert module_creds["telegram.bot"] == ["TG_BOT_TOKEN"]
    assert module_creds["email.bot"] == ["BOT_EMAIL_ADDRESS", "BOT_EMAIL_PASSWORD"]
    assert "telegram.user" not in module_creds
    assert "email.user" not in module_creds

    mock_spawner_cls.assert_called_once()
    spawner_kwargs = mock_spawner_cls.call_args.kwargs
    assert spawner_kwargs["module_credentials_env"] == module_creds


# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------


async def test_detect_secrets(
    butler_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Suspicious config values warn; clean config produces no warnings."""
    # Suspicious config warns on detected fields
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
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["socket"],
        patches["configure_logging"],
        caplog.at_level(logging.WARNING, logger="butlers.daemon"),
    ):
        daemon = ButlerDaemon(tmp_path)
        await daemon.start()

    warnings = [
        rec.message for rec in caplog.records if "may contain an inline secret" in rec.message
    ]
    assert len(warnings) == 2
    assert any("butler.description" in w for w in warnings)
    assert any("butler.db.name" in w for w in warnings)

    # Clean config produces no secret warnings; env lists and credentials_env are exempt
    caplog.clear()
    patches2 = _patch_infra()
    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["socket"],
        caplog.at_level(logging.WARNING, logger="butlers.daemon"),
    ):
        daemon2 = ButlerDaemon(butler_dir)
        await daemon2.start()
    assert not any("may contain an inline secret" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Config flatten helper
# ---------------------------------------------------------------------------


def test_flatten_config_for_secret_scan(butler_dir: Path, tmp_path: Path) -> None:
    """_flatten_config_for_secret_scan produces correct flat keys; excludes credentials/env."""
    from butlers.config import load_config
    from butlers.daemon import _flatten_config_for_secret_scan

    # Basic config with schedule
    config = load_config(butler_dir)
    flat = _flatten_config_for_secret_scan(config)

    assert flat["butler.name"] == "test-butler"
    assert flat["butler.port"] == 9100
    assert flat["butler.description"] == "A test butler"
    assert flat["butler.db.name"] == "butlers"
    assert flat["butler.schedule[0].name"] == "daily-check"
    assert flat["butler.schedule[0].cron"] == "0 9 * * *"

    # Module config is flattened; credentials_env excluded
    toml_content = """
[butler]
name = "test-butler"
port = 9100

[butler.db]
name = "butlers"
schema = "test_butler"

[modules.email]
smtp_server = "smtp.example.com"
credentials_env = ["EMAIL_TOKEN"]

[butler.env]
required = ["PG_DSN"]
optional = ["OPTIONAL_KEY"]
"""
    (tmp_path / "butler.toml").write_text(toml_content)
    config2 = load_config(tmp_path)
    flat2 = _flatten_config_for_secret_scan(config2)

    assert flat2["modules.email.smtp_server"] == "smtp.example.com"
    assert "modules.email.credentials_env" not in flat2
    assert "butler.env.required" not in flat2
    assert "butler.env.optional" not in flat2


# ---------------------------------------------------------------------------
# Runtime adapter
# ---------------------------------------------------------------------------


async def test_runtime_adapter_and_binary_check(butler_dir: Path) -> None:
    """Spawner receives runtime adapter; missing binary raises; found binary proceeds."""
    # Adapter passed to spawner
    patches = _patch_infra()
    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"] as mock_spawner_cls,
        patches["get_adapter"],
        patches["shutil_which"],
        patches["socket"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
    mock_spawner_cls.assert_called_once()
    call_kwargs = mock_spawner_cls.call_args.kwargs
    assert "runtime" in call_kwargs and call_kwargs["runtime"] is patches["mock_adapter"]


# ---------------------------------------------------------------------------
# Message pipeline wiring
# ---------------------------------------------------------------------------


async def test_message_pipeline_wiring(tmp_path: Path) -> None:
    """Switchboard wires pipeline; non-switchboard butlers do not. Modules lack _pipeline."""
    registry = _make_registry(TelegramModule, EmailModule)

    for butler_name, expect_pipeline in [("switchboard", False), ("test-butler", False)]:
        subdir = tmp_path / butler_name
        subdir.mkdir(exist_ok=True)
        butler_dir = _make_butler_toml(
            subdir,
            modules={"telegram": {}, "email": {}},
            butler_name=butler_name,
            port=41100,
        )
        patches = _patch_infra()

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
            patches["recover_route_inbox"],
            patch.object(TelegramModule, "on_startup", new_callable=AsyncMock),
            patch.object(EmailModule, "on_startup", new_callable=AsyncMock),
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        tg = next(m for m in daemon._modules if m.name == "telegram")
        em = next(m for m in daemon._modules if m.name == "email")
        assert not hasattr(tg, "_pipeline")
        assert not hasattr(em, "_pipeline")


    # Missing binary → raises, Spawner not created
    patches_m = _patch_infra()
    with (
        patches_m["db_from_env"],
        patches_m["run_migrations"],
        patches_m["validate_credentials"],
        patches_m["validate_module_credentials"],
        patches_m["init_telemetry"],
        patches_m["sync_schedules"],
        patches_m["FastMCP"],
        patches_m["Spawner"] as mock_spawner_cls2,
        patches_m["get_adapter"],
        patch("butlers.daemon.shutil.which", return_value=None),
    ):
        daemon_m = ButlerDaemon(butler_dir)
        with pytest.raises(RuntimeBinaryNotFoundError, match="'claude'"):
            await daemon_m.start()
    mock_spawner_cls2.assert_not_called()

    # Found binary → startup proceeds
    patches_f = _patch_infra()
    with (
        patches_f["db_from_env"],
        patches_f["run_migrations"],
        patches_f["validate_credentials"],
        patches_f["validate_module_credentials"],
        patches_f["init_telemetry"],
        patches_f["sync_schedules"],
        patches_f["FastMCP"],
        patches_f["Spawner"],
        patches_f["get_adapter"],
        patches_f["shutil_which"] as mock_which,
        patches_f["socket"],
    ):
        daemon_f = ButlerDaemon(butler_dir)
        await daemon_f.start()
    mock_which.assert_called_once_with("claude")
    assert daemon_f._started_at is not None


# ---------------------------------------------------------------------------
# Switchboard client connection lifecycle
# ---------------------------------------------------------------------------


async def test_switchboard_client_lifecycle(butler_dir: Path, tmp_path: Path) -> None:
    """Client None pre-start; no-URL skips; connect/disconnect lifecycle; errors non-fatal."""
    # Before start: client is always None
    assert ButlerDaemon(butler_dir).switchboard_client is None

    # switchboard_url=None → client remains None after start
    no_url_dir = tmp_path / "no_url"
    no_url_dir.mkdir()
    (no_url_dir / "butler.toml").write_text(
        "[butler]\nname = \"switchboard\"\nport = 41100\n\n[butler.db]\nname = \"butlers\"\nschema = \"switchboard\"\n"
    )
    patches_no_url = _patch_infra()
    with (
        patches_no_url["db_from_env"],
        patches_no_url["run_migrations"],
        patches_no_url["validate_credentials"],
        patches_no_url["validate_module_credentials"],
        patches_no_url["init_telemetry"],
        patches_no_url["sync_schedules"],
        patches_no_url["FastMCP"],
        patches_no_url["Spawner"],
        patches_no_url["get_adapter"],
        patches_no_url["shutil_which"],
        patches_no_url["start_mcp_server"],
        # Do NOT mock _connect_switchboard — let it see switchboard_url=None
    ):
        daemon_no_url = ButlerDaemon(no_url_dir)
        await daemon_no_url.start()
    assert daemon_no_url.switchboard_client is None

    # Successful connect → client set; shutdown → client cleared
    patches = _patch_infra()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
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
        patch("butlers.daemon.MCPClient", return_value=mock_client),
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
    assert daemon.switchboard_client is mock_client
    mock_client.__aenter__.assert_awaited_once()
    await daemon.shutdown()
    mock_client.__aexit__.assert_awaited_once_with(None, None, None)
    assert daemon.switchboard_client is None

    # Connect failure: startup still succeeds, client stays None
    patches2 = _patch_infra()
    mock_client2 = AsyncMock()
    mock_client2.__aenter__ = AsyncMock(side_effect=RuntimeError("Connection refused"))
    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patch("butlers.daemon.MCPClient", return_value=mock_client2),
    ):
        daemon2 = ButlerDaemon(butler_dir)
        await daemon2.start()
    assert daemon2.switchboard_client is None
    assert daemon2._started_at is not None

    # Disconnect error: shutdown still completes; client set to None
    patches3 = _patch_infra()
    mock_client3 = AsyncMock()
    mock_client3.__aenter__ = AsyncMock(return_value=mock_client3)
    mock_client3.__aexit__ = AsyncMock(side_effect=OSError("connection reset"))
    with (
        patches3["db_from_env"],
        patches3["run_migrations"],
        patches3["validate_credentials"],
        patches3["validate_module_credentials"],
        patches3["init_telemetry"],
        patches3["sync_schedules"],
        patches3["FastMCP"],
        patches3["Spawner"],
        patches3["get_adapter"],
        patches3["shutil_which"],
        patches3["start_mcp_server"],
        patch("butlers.daemon.MCPClient", return_value=mock_client3),
    ):
        daemon3 = ButlerDaemon(butler_dir)
        await daemon3.start()
    await daemon3.shutdown()
    assert daemon3.switchboard_client is None

    # Custom URL from config forwarded to MCPClient with correct butler name
    custom_dir = tmp_path / "custom_url"
    custom_dir.mkdir()
    (custom_dir / "butler.toml").write_text("""\
[butler]
name = "health"
port = 41103

[butler.db]
name = "butlers"
schema = "health"

[butler.switchboard]
url = "http://custom-switchboard:9000/sse"
""")
    patches4 = _patch_infra()
    mock_client4 = AsyncMock()
    mock_client4.__aenter__ = AsyncMock(return_value=mock_client4)
    mock_client4.__aexit__ = AsyncMock(return_value=False)
    client_init_args: list = []

    def capture_client_init(url, **kwargs):
        client_init_args.append((url, kwargs))
        return mock_client4

    with (
        patches4["db_from_env"],
        patches4["run_migrations"],
        patches4["validate_credentials"],
        patches4["validate_module_credentials"],
        patches4["init_telemetry"],
        patches4["sync_schedules"],
        patches4["FastMCP"],
        patches4["Spawner"],
        patches4["get_adapter"],
        patches4["shutil_which"],
        patches4["start_mcp_server"],
        patch("butlers.daemon.MCPClient", side_effect=capture_client_init),
    ):
        daemon4 = ButlerDaemon(custom_dir)
        await daemon4.start()

    assert len(client_init_args) == 1
    assert client_init_args[0][0] == "http://custom-switchboard:9000/sse"
    assert client_init_args[0][1]["name"] == "butler-health"


# ---------------------------------------------------------------------------
# notify() tool
# ---------------------------------------------------------------------------


async def _start_daemon_with_notify(butler_dir: Path, patches: dict):
    """Start daemon and extract the notify function."""
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


async def test_notify_registered_and_schema_contract(butler_dir: Path) -> None:
    """notify is registered; description and schema conform to contract."""
    patches = _patch_infra()
    runtime_mcp = RuntimeFastMCP("test-butler")

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
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
    assert "NOT a JSON string" in description

    params = notify_tool["parameters"]
    assert set(params["properties"]["channel"]["enum"]) == {"telegram", "email", "whatsapp"}
    assert set(params["properties"]["intent"]["enum"]) == {"send", "reply", "react", "insight"}

    rc_json = json.dumps(params["properties"]["request_context"])
    assert "request_id" in rc_json
    assert "source_thread_identity" in rc_json


async def test_notify_channels_and_delivery(butler_dir: Path) -> None:
    """Unsupported channel errors; missing switchboard errors; successful delivery payload correct."""
    patches = _patch_infra()
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
    assert notify_fn is not None

    # Unsupported channel → channel error
    result = await notify_fn(channel="sms", message="Hello")
    assert result["status"] == "error"
    assert "sms" in result["error"]
    assert "Unsupported channel" in result["error"]

    # Valid channels fail due to missing switchboard (not channel error)
    for channel in ("telegram", "email"):
        result = await notify_fn(channel=channel, message="Hello")
        assert result["status"] == "error"
        assert "Switchboard is not connected" in result["error"]

    # Successful delivery: payload structure, source_butler, schema_version
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {"notification_id": "abc-123", "status": "sent"}
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    daemon.switchboard_client = mock_client

    result = await notify_fn(channel="email", message="Hello world")
    assert result["status"] == "ok"
    assert result["result"] == {"notification_id": "abc-123", "status": "sent"}
    call_args = mock_client.call_tool.await_args
    assert call_args.args[0] == "deliver"
    payload = call_args.args[1]
    assert payload["source_butler"] == "test-butler"
    assert payload["notify_request"]["schema_version"] == "notify.v1"
    assert payload["notify_request"]["delivery"] == {
        "intent": "send",
        "channel": "email",
        "message": "Hello world",
    }

    # With recipient: forwarded in delivery
    from butlers.identity import ResolvedContact

    known = ResolvedContact(
        contact_id=__import__("uuid").UUID("00000000-0000-0000-0000-ffffffffffff"),
        name="Test",
        roles=["owner"],
        entity_id=None,
    )
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    with patch(
        "butlers.identity.resolve_contact_by_channel",
        new=AsyncMock(return_value=known),
    ):
        result2 = await notify_fn(
            channel="email", message="Weekly report", recipient="user@example.com"
        )
    assert result2["status"] == "ok"
    delivery = mock_client.call_tool.await_args.args[1]["notify_request"]["delivery"]
    assert delivery["recipient"] == "user@example.com"

    # Without recipient: field omitted
    mock_call_result3 = MagicMock()
    mock_call_result3.is_error = False
    mock_call_result3.data = {"notification_id": "ghi-789", "status": "sent"}
    mock_client.call_tool = AsyncMock(return_value=mock_call_result3)
    result3 = await notify_fn(channel="email", message="Alert")
    assert result3["status"] == "ok"
    assert "recipient" not in mock_client.call_tool.call_args[0][1]


@pytest.mark.parametrize(
    "intent,with_chat_id,expected_status",
    [
        ("send", True, "ok"),
        ("send", False, "error"),
        ("insight", True, "ok"),
        ("insight", False, "error"),
    ],
)
async def test_notify_telegram_owner_resolution(
    butler_dir: Path, intent, with_chat_id, expected_status
) -> None:
    """Telegram send/insight resolves owner chat ID; fails when absent."""
    patches = _patch_infra()
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
    assert notify_fn is not None

    mock_client = AsyncMock()
    if with_chat_id:
        mock_call_result = MagicMock()
        mock_call_result.is_error = False
        mock_call_result.data = {"notification_id": "jkl-012", "status": "sent"}
        mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    daemon.switchboard_client = mock_client

    chat_id = "123456789" if with_chat_id else None
    with patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=chat_id)):
        result = await notify_fn(channel="telegram", message="Update", intent=intent)

    assert result["status"] == expected_status
    if expected_status == "ok":
        payload = mock_client.call_tool.await_args.args[1]
        assert payload["notify_request"]["delivery"]["recipient"] == "123456789"
        assert payload["notify_request"]["delivery"]["intent"] == intent
    else:
        assert "No bot <-> user telegram chat has been configured" in result["error"]
        mock_client.call_tool.assert_not_awaited()


async def test_notify_failure_modes(butler_dir: Path) -> None:
    """notify: delivery failures surfaced; empty/whitespace rejected; timeout returns error."""
    patches = _patch_infra()
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
    assert notify_fn is not None

    # Delivery-level failure (status=failed in payload)
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {
        "notification_id": "a9f943ce-8800-47dc-9190-cb50f3bbb8b6",
        "status": "failed",
        "error": "Telegram reply source_thread_identity must include an integer message_id.",
        "error_class": "validation_error",
        "retryable": False,
    }
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    daemon.switchboard_client = mock_client

    result = await notify_fn(channel="email", message="Hello")
    assert result["status"] == "error"
    assert "source_thread_identity" in result["error"]
    assert result["error_class"] == "validation_error"
    assert result["retryable"] is False
    assert result["notification_id"] == "a9f943ce-8800-47dc-9190-cb50f3bbb8b6"

    # Empty/whitespace messages rejected
    for msg in ("", "   \t\n  "):
        result = await notify_fn(channel="telegram", message=msg)
        assert result["status"] == "error"
        assert "empty" in result["error"].lower() or "whitespace" in result["error"].lower()

    # Timeout returns error
    _orphaned_coros: list = []

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(999)  # pragma: no cover

    def _tracking_slow_call(*args, **kwargs):
        coro = slow_call(*args, **kwargs)
        _orphaned_coros.append(coro)
        return coro

    mock_client2 = AsyncMock()
    mock_client2.call_tool = _tracking_slow_call
    daemon.switchboard_client = mock_client2

    with patch("butlers.daemon.asyncio.wait_for", side_effect=TimeoutError()):
        result = await notify_fn(channel="email", message="Hello")

    for coro in _orphaned_coros:
        coro.close()

    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()


@pytest.mark.parametrize(
    "side_effect,expected_in_error",
    [
        (MagicMock(is_error=True, content=[MagicMock(text="No module available")]), "No module"),
        (None, "Switchboard call failed"),  # will use RuntimeError side_effect
        (TimeoutError("Request timed out"), "timed out"),
        (ConnectionError("Connection refused"), "unreachable"),
        (OSError("Network is down"), "unreachable"),
    ],
)
async def test_notify_error_cases(butler_dir: Path, side_effect, expected_in_error) -> None:
    """notify returns error result for various failure modes."""
    patches = _patch_infra()
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
    assert notify_fn is not None

    mock_client = AsyncMock()
    if isinstance(side_effect, MagicMock):
        mock_client.call_tool = AsyncMock(return_value=side_effect)
    elif side_effect is None:
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Unexpected failure"))
        expected_in_error = "Switchboard call failed"
    else:
        mock_client.call_tool = AsyncMock(side_effect=side_effect)
    daemon.switchboard_client = mock_client

    result = await notify_fn(channel="email", message="Hello")
    assert result["status"] == "error"
    assert expected_in_error.lower() in result["error"].lower()


# ---------------------------------------------------------------------------
# route.execute tool
# ---------------------------------------------------------------------------


def _route_request_context() -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": "mcp",
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "health",
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
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


async def test_route_execute_messenger_scenarios(tmp_path: Path) -> None:
    """route.execute: missing notify_request errors; success delivers; origin mismatch rejected."""
    # Missing notify_request → validation_error
    m1 = tmp_path / "m1"
    m1.mkdir()
    patches = _patch_infra()
    butler_dir = _make_butler_toml(m1, butler_name="messenger", modules={"telegram": {}, "email": {}})
    _, fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert fn is not None

    result = await fn(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={"prompt": "Deliver.", "context": {}},
    )
    assert result["schema_version"] == "route_response.v1"
    assert result["status"] == "error"
    assert result["error"]["class"] == "validation_error"
    assert result["error"]["retryable"] is False
    assert result["result"]["notify_response"]["error"]["class"] == "validation_error"

    # Success: delivery proceeds; notify_response normalized
    m2 = tmp_path / "m2"
    m2.mkdir()
    patches2 = _patch_infra()
    butler_dir2 = _make_butler_toml(m2, butler_name="messenger", modules={"telegram": {}, "email": {}})
    daemon2, fn2 = await _start_daemon_with_route_execute(butler_dir2, patches2)
    assert fn2 is not None
    telegram_module = next(m for m in daemon2._modules if m.name == "telegram")
    telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 321}})

    result2 = await fn2(
        schema_version="route.v1",
        request_context=_route_request_context(),
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
    telegram_module._send_message.assert_awaited_once_with("12345", "[health] Take your medication.")
    assert result2["status"] == "ok"
    nr = result2["result"]["notify_response"]
    assert nr["schema_version"] == "notify_response.v1"
    assert nr["status"] == "ok"
    assert nr["delivery"]["channel"] == "telegram"
    assert nr["delivery"]["delivery_id"] == "321"

    # Origin mismatch → validation_error; delivery not attempted
    m3 = tmp_path / "m3"
    m3.mkdir()
    patches3 = _patch_infra()
    butler_dir3 = _make_butler_toml(m3, butler_name="messenger", modules={"telegram": {}, "email": {}})
    daemon3, fn3 = await _start_daemon_with_route_execute(butler_dir3, patches3)
    assert fn3 is not None
    telegram3 = next(m for m in daemon3._modules if m.name == "telegram")
    telegram3._send_message = AsyncMock()

    result3 = await fn3(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={
            "prompt": "Deliver.",
            "context": {
                "notify_request": {
                    "schema_version": "notify.v1",
                    "origin_butler": "general",  # mismatch: context says "health"
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
    telegram3._send_message.assert_not_awaited()
    assert result3["status"] == "error"
    assert result3["error"]["class"] == "validation_error"
    assert result3["result"]["notify_response"]["error"]["class"] == "validation_error"


@pytest.mark.parametrize(
    "scenario,expected_class,retryable",
    [
        ("missing_recipient", "validation_error", False),
        ("provider_down", "target_unavailable", True),
    ],
)
async def test_route_execute_error_scenarios(
    tmp_path: Path, scenario, expected_class, retryable
) -> None:
    """route.execute handles target resolution failures and provider errors."""
    patches = _patch_infra()
    butler_dir = _make_butler_toml(
        tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}
    )
    daemon, fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert fn is not None

    if scenario == "provider_down":
        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        telegram_module._send_message = AsyncMock(side_effect=ConnectionError("provider down"))

    if scenario == "missing_recipient":
        input_ctx = {
            "notify_request": {
                "schema_version": "notify.v1",
                "origin_butler": "health",
                "delivery": {
                    "intent": "send",
                    "channel": "email",
                    "message": "Your report is ready.",
                    # no recipient
                },
            }
        }
    else:
        input_ctx = {
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
        }

    result = await fn(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={"prompt": "Deliver.", "context": input_ctx},
    )

    assert result["status"] == "error"
    assert result["error"]["class"] == expected_class
    assert result["error"]["retryable"] is retryable
    assert result["result"]["notify_response"]["error"]["class"] == expected_class
    assert result["result"]["notify_response"]["error"]["retryable"] is retryable


# ---------------------------------------------------------------------------
# Non-fatal module startup
# ---------------------------------------------------------------------------


async def test_non_fatal_module_failures(tmp_path: Path) -> None:
    """Failed modules marked failed/cascade_failed; on_startup failures degrade health/tools."""
    # Credentials failure: stub_a fails, stub_b cascade_fails; butler still starts
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

    assert daemon._started_at is not None
    assert daemon._module_statuses["stub_a"].status == "failed"
    assert daemon._module_statuses["stub_a"].phase == "credentials"
    assert daemon._module_statuses["stub_b"].status == "cascade_failed"
    assert "stub_a" in daemon._module_statuses["stub_b"].error
    # status() reports phase+error for failed, cascade_failed for dependents
    assert status_fn is not None
    result = await status_fn()
    stub_a_info = result["modules"]["stub_a"]
    assert stub_a_info["status"] == "failed"
    assert stub_a_info["phase"] == "credentials"
    assert "STUB_A_TOKEN" in stub_a_info["error"]
    assert result["modules"]["stub_b"]["status"] == "cascade_failed"

    # on_startup failure: failed module skipped in tools/shutdown; active module healthy
    fail_subdir = tmp_path / "fail"
    fail_subdir.mkdir()
    butler_dir2 = _make_butler_toml(fail_subdir, modules={"stub_fail": {}, "stub_a": {}})
    registry2 = _make_registry(StubModuleFailStartup, StubModuleA)
    patches2 = _patch_infra()
    status_fn2 = None
    mock_mcp2 = MagicMock()

    def tool_decorator2(*_decorator_args, **_decorator_kwargs):
        def decorator(fn):
            nonlocal status_fn2
            if fn.__name__ == "status":
                status_fn2 = fn
            return fn

        return decorator

    mock_mcp2.tool = tool_decorator2

    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp2),
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patches2["connect_switchboard"],
        patches2["create_audit_pool"],
        patches2["recover_route_inbox"],
    ):
        daemon2 = ButlerDaemon(butler_dir2, registry=registry2)
        await daemon2.start()

    assert daemon2._started_at is not None
    assert daemon2._module_statuses["stub_fail"].status == "failed"
    assert daemon2._module_statuses["stub_fail"].phase == "startup"
    assert daemon2._module_statuses["stub_a"].status == "active"
    fail_mod = next(m for m in daemon2._modules if m.name == "stub_fail")
    active_mod = next(m for m in daemon2._modules if m.name == "stub_a")
    assert not fail_mod.tools_registered
    assert active_mod.tools_registered
    assert status_fn2 is not None
    r2 = await status_fn2()
    assert r2["health"] == "degraded"
    assert r2["modules"]["stub_fail"]["status"] == "failed"
    assert r2["modules"]["stub_a"]["status"] == "active"
    await daemon2.shutdown()
    assert not fail_mod.shutdown_called
    assert active_mod.shutdown_called


# ---------------------------------------------------------------------------
# Switchboard heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_lifecycle_and_reconnect(butler_dir: Path, tmp_path: Path) -> None:
    """Heartbeat created/cleared on start/shutdown; switchboard butler omitted; reconnects on dead connection."""
    # Normal butler: heartbeat created and cleared on shutdown
    patches = _patch_infra()
    daemon = await _start_daemon(butler_dir, patches)
    assert daemon._switchboard_heartbeat_task is not None
    heartbeat_task = daemon._switchboard_heartbeat_task
    await daemon.shutdown()
    assert daemon._switchboard_heartbeat_task is None
    assert heartbeat_task.cancelled() or heartbeat_task.done()

    # Switchboard butler (switchboard_url=None): no heartbeat task
    sw_dir = tmp_path / "sw_test"
    sw_dir.mkdir()
    (sw_dir / "butler.toml").write_text("""\
[butler]
name = "switchboard"
port = 41100

[butler.db]
name = "butlers"
schema = "switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
""")
    patches2 = _patch_infra()
    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patches2["FastMCP"],
        patches2["Spawner"],
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        # Don't mock _connect_switchboard — let it see switchboard_url=None
    ):
        daemon2 = ButlerDaemon(sw_dir)
        await daemon2.start()
    assert daemon2._switchboard_heartbeat_task is None
    await daemon2.shutdown()

    # Dead connection → disconnects and reconnects; healthy connection → no reconnect
    def _make_client(list_tools_side_effect=None, list_tools_return=None):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        if list_tools_side_effect:
            client.list_tools = AsyncMock(side_effect=list_tools_side_effect)
        else:
            client.list_tools = AsyncMock(return_value=list_tools_return or [])
        return client

    async def _start_with_client(mock_client):
        p = _patch_infra()
        with (
            p["db_from_env"],
            p["run_migrations"],
            p["validate_credentials"],
            p["validate_module_credentials"],
            p["init_telemetry"],
            p["sync_schedules"],
            p["FastMCP"],
            p["Spawner"],
            p["get_adapter"],
            p["shutil_which"],
            p["start_mcp_server"],
            patch("butlers.daemon.MCPClient", return_value=mock_client),
        ):
            d = ButlerDaemon(butler_dir)
            await d.start()
        d._switchboard_heartbeat_task.cancel()
        try:
            await d._switchboard_heartbeat_task
        except asyncio.CancelledError:
            pass
        return d

    dead_client = _make_client(list_tools_side_effect=ConnectionError("dead"))
    daemon3 = await _start_with_client(dead_client)
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
        try:
            await asyncio.wait_for(daemon3.switchboard_client.list_tools(), timeout=5.0)
        except Exception:
            await daemon3._disconnect_switchboard()
            await daemon3._connect_switchboard()

    assert disconnect_called
    assert connect_called

    # Healthy connection → no reconnect
    healthy_client = _make_client(list_tools_return=[])
    daemon4 = await _start_with_client(healthy_client)
    disconnect_called2 = False

    async def fake_disconnect2(self_daemon):
        nonlocal disconnect_called2
        disconnect_called2 = True

    with patch.object(ButlerDaemon, "_disconnect_switchboard", new=fake_disconnect2):
        await asyncio.wait_for(daemon4.switchboard_client.list_tools(), timeout=5.0)

    assert not disconnect_called2
    assert daemon4.switchboard_client is healthy_client


# ---------------------------------------------------------------------------
# Staffer briefing exclusion
# ---------------------------------------------------------------------------


def _make_staffer_toml(tmp_path: Path, *, with_briefing_schedule: bool = True) -> Path:
    """Write a minimal staffer butler.toml with optional briefing schedule."""
    lines = [
        "[butler]",
        'name = "test-staffer"',
        "port = 9200",
        'description = "A test staffer"',
        'type = "staffer"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_staffer"',
    ]
    if with_briefing_schedule:
        lines += [
            "",
            "[[butler.schedule]]",
            'name = "daily_briefing_contribution"',
            'cron = "55 6 * * *"',
            'dispatch_mode = "job"',
            'job_name = "daily_briefing_contribution"',
        ]
    lines += [
        "",
        "[[butler.schedule]]",
        'name = "other-job"',
        'cron = "0 8 * * *"',
        'dispatch_mode = "job"',
        'job_name = "some_other_job"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(lines))
    return tmp_path


@pytest.mark.parametrize(
    "butler_type,job_name,should_be_included",
    [
        ("staffer", "daily_briefing_contribution", False),
        ("butler", "daily_briefing_contribution", True),
        ("staffer", "some_other_job", True),
    ],
)
async def test_briefing_schedule_inclusion(
    tmp_path: Path, butler_type, job_name, should_be_included
) -> None:
    """Staffer excludes daily_briefing_contribution; butler includes it; others pass through."""
    if butler_type == "staffer":
        butler_dir = _make_staffer_toml(tmp_path, with_briefing_schedule=True)
    else:
        lines = [
            "[butler]",
            'name = "test-butler-sched"',
            "port = 9201",
            'description = "A test butler"',
            "",
            "[butler.db]",
            'name = "butlers"',
            'schema = "test_butler_sched"',
            "",
            "[[butler.schedule]]",
            'name = "daily_briefing_contribution"',
            'cron = "55 6 * * *"',
            'dispatch_mode = "job"',
            'job_name = "daily_briefing_contribution"',
        ]
        (tmp_path / "butler.toml").write_text("\n".join(lines))
        butler_dir = tmp_path

    patches = _patch_infra()

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
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
    schedules_arg = args[0][1]
    job_names = [s.get("job_name") for s in schedules_arg]

    if should_be_included:
        assert job_name in job_names
    else:
        assert job_name not in job_names


# ---------------------------------------------------------------------------
# Switchboard registration type field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "butler_type,expected_type",
    [
        ("butler", "butler"),
        ("staffer", "staffer"),
    ],
)
async def test_heartbeat_payload_includes_type(tmp_path: Path, butler_type, expected_type) -> None:
    """Liveness reporter payload includes correct type field."""
    if butler_type == "staffer":
        butler_dir = _make_staffer_toml(tmp_path, with_briefing_schedule=False)
    else:
        butler_dir = _make_butler_toml(tmp_path)

    patches = _patch_infra()

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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    try:
        daemon._liveness_reporter_task.cancel()
        try:
            await daemon._liveness_reporter_task
        except asyncio.CancelledError:
            pass

        posted_payloads: list[dict] = []

        class _FakeResp:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

        async def mock_post(url: str, *, json: dict) -> object:  # noqa: ANN001
            posted_payloads.append(json)
            return _FakeResp()

        mock_http_client = MagicMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = mock_post

        sleep_call_count = 0

        async def _one_then_cancel(*args: object, **kwargs: object) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count >= 2:
                raise asyncio.CancelledError

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_http_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=_one_then_cancel),
        ):
            try:
                await daemon._liveness_reporter_loop()
            except asyncio.CancelledError:
                pass

        assert len(posted_payloads) >= 1
        for payload in posted_payloads:
            assert payload["type"] == expected_type
    finally:
        await daemon.shutdown()
