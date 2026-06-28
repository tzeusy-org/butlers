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
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from starlette.testclient import TestClient

from butlers.credentials import CredentialError
from butlers.daemon import (
    DOMAIN_CORE_TOOL_NAMES,
    UNIVERSAL_CORE_TOOL_NAMES,
    ButlerDaemon,
    _McpSseDisconnectGuard,
)
from butlers.mcp_patches import (
    apply_streamable_http_client_disconnect_patch,
    apply_streamable_http_disconnect_patch,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test helpers: stub modules
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_file_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep daemon tests independent from workspace log directory permissions."""
    monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")


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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        raise RuntimeError("on_startup boom")

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


class _FakeSocket:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.bound: tuple[str, int] | None = None
        self.backlog: int | None = None
        self.closed = False

    def setsockopt(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def listen(self, backlog: int) -> None:
        self.backlog = backlog

    def close(self) -> None:
        self.closed = True


class _FakeUvicornConfig:
    def __init__(self, app: Any, **kwargs: Any) -> None:
        self.app = app
        self.kwargs = kwargs
        self.backlog = 2048


class _DelayedStartedServer:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.started = False
        self.should_exit = False

    async def serve(self, sockets: list[Any] | None = None) -> None:
        await asyncio.sleep(0.05)
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.01)


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


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict:
    """Return a dict-like row for the runtime_config table, as returned by asyncpg.fetchrow."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "test-butler"):
    """Return an async side_effect for pool.fetchrow that returns runtime_config rows
    for runtime_config queries and None for all other queries."""

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    # Support `async with pool.acquire() as conn:` for _ensure_owner_entity
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetch = AsyncMock(return_value=[])

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
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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

    # "general" is a domain butler → gets universal + domain tools, not
    # messenger or switchboard tools.
    assert set(registered_tools) == UNIVERSAL_CORE_TOOL_NAMES | DOMAIN_CORE_TOOL_NAMES


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
    butler_dir_m = _make_butler_toml(shutdown_mods_dir, modules={"stub_a": {}, "stub_b": {}})
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


async def test_start_mcp_server_waits_until_uvicorn_reports_started(butler_dir: Path) -> None:
    """Startup must not advance until the local /mcp server is actually ready."""
    from butlers.config import load_config

    daemon = ButlerDaemon(butler_dir)
    daemon.config = load_config(butler_dir)
    daemon.mcp = RuntimeFastMCP("test-butler")

    with (
        patch.object(ButlerDaemon, "_build_mcp_http_app", return_value=object()),
        patch("butlers.daemon.uvicorn.Config", _FakeUvicornConfig),
        patch("butlers.daemon.uvicorn.Server", _DelayedStartedServer),
        patch("butlers.daemon.socket.socket", _FakeSocket),
    ):
        await daemon._start_mcp_server()

    assert daemon._server is not None
    assert daemon._server.started is True
    assert daemon._server.config.kwargs["timeout_graceful_shutdown"] == (
        daemon.config.shutdown_timeout_s
    )
    assert daemon._server.config.kwargs["timeout_graceful_shutdown"] > 0
    assert daemon._server_task is not None
    assert daemon._mcp_socket is not None

    daemon._server.should_exit = True
    await asyncio.wait_for(daemon._server_task, timeout=1)
    daemon._mcp_socket.close()
    daemon._server_task = None
    daemon._server = None
    daemon._mcp_socket = None


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


async def test_sse_disconnect_guard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ClientDisconnect on /messages/ suppressed with 202 and logged; non-disconnect bubbles."""
    sent_messages: list[dict[str, Any]] = []

    async def disconnecting_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise ClientDisconnect()

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    guard = _McpSseDisconnectGuard(disconnecting_app, butler_name="test-butler")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/messages/",
        "query_string": b"session_id=abc123",
    }

    with caplog.at_level(logging.DEBUG, logger="butlers.guards"):
        await guard(scope, receive, send)

    assert any(
        "Suppressed expected MCP SSE POST disconnect" in rec.message for rec in caplog.records
    )
    assert sent_messages == [
        {"type": "http.response.start", "status": 202, "headers": [(b"content-length", b"0")]},
        {"type": "http.response.body", "body": b""},
    ]

    # Non-disconnect exception bubbles through
    async def booming_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    guard2 = _McpSseDisconnectGuard(booming_app, butler_name="test-butler")
    scope2 = {"type": "http", "method": "POST", "path": "/messages/", "query_string": b""}

    async def noop_send(message: dict[str, Any]) -> None:
        return None

    with pytest.raises(RuntimeError):
        await guard2(scope2, receive, noop_send)


async def test_sse_disconnect_guard_completes_started_response() -> None:
    """ClientDisconnect after response start MUST finish the response body only."""
    sent_messages: list[dict[str, Any]] = []

    async def disconnecting_after_start(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 202, "headers": []})
        raise ClientDisconnect()

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    guard = _McpSseDisconnectGuard(disconnecting_after_start, butler_name="test-butler")
    await guard(
        {
            "type": "http",
            "method": "POST",
            "path": "/messages/",
            "query_string": b"session_id=abc123",
        },
        receive,
        send,
    )

    assert sent_messages == [
        {"type": "http.response.start", "status": 202, "headers": []},
        {"type": "http.response.body", "body": b""},
    ]


async def test_sse_disconnect_guard_leaves_completed_response_untouched() -> None:
    """ClientDisconnect after a fully completed response MUST NOT emit additional ASGI messages."""
    sent_messages: list[dict[str, Any]] = []

    async def disconnecting_after_complete(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 202, "headers": []})
        # Final body chunk (more_body omitted, defaults to False) — response is complete.
        await send({"type": "http.response.body", "body": b"ok"})
        # Simulate ClientDisconnect raised during cleanup, after the response completed.
        raise ClientDisconnect()

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    guard = _McpSseDisconnectGuard(disconnecting_after_complete, butler_name="test-butler")
    await guard(
        {
            "type": "http",
            "method": "POST",
            "path": "/messages/",
            "query_string": b"session_id=abc123",
        },
        receive,
        send,
    )

    # Guard MUST leave the completed response alone — no synthetic start, no extra body.
    assert sent_messages == [
        {"type": "http.response.start", "status": 202, "headers": []},
        {"type": "http.response.body", "body": b"ok"},
    ]


async def test_streamable_http_disconnect_patch_is_idempotent() -> None:
    """Repeated calls MUST leave exactly one filter instance attached."""
    import mcp.server.streamable_http as streamable_http

    from butlers.mcp_patches import _StandaloneSseDisconnectFilter

    apply_streamable_http_disconnect_patch()
    apply_streamable_http_disconnect_patch()
    apply_streamable_http_disconnect_patch()

    filters = [
        f for f in streamable_http.logger.filters if isinstance(f, _StandaloneSseDisconnectFilter)
    ]
    assert len(filters) == 1


async def test_streamable_http_disconnect_patch_downgrades_client_disconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expected client-disconnect tracebacks MUST be downgraded to DEBUG.

    Exercises the upstream logger directly so we catch behavior regressions
    regardless of how ``_handle_get_request`` evolves internally. We log
    exactly what upstream logs from its ``standalone_sse_writer`` inner
    coroutine (message + exc_info) and assert the filter rewrites the record.
    """
    import mcp.server.streamable_http as streamable_http

    apply_streamable_http_disconnect_patch()

    logger = streamable_http.logger
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        try:
            raise anyio.ClosedResourceError()
        except anyio.ClosedResourceError:
            logger.exception("Error in standalone SSE writer")

    target = [rec for rec in caplog.records if rec.name == logger.name]
    assert target, "expected at least one log record on the streamable_http logger"
    rec = target[-1]
    # Filter rewrites msg to the disconnect text at DEBUG level and strips exc_info.
    assert rec.levelno == logging.DEBUG
    assert rec.exc_info is None
    assert rec.exc_text is None
    assert rec.getMessage() == "Standalone SSE stream closed during client disconnect"
    # The original upstream error message MUST NOT appear anywhere.
    assert not any("Error in standalone SSE writer" in r.getMessage() for r in target)


async def test_streamable_http_disconnect_patch_preserves_unrelated_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unrelated exceptions on the same logger MUST surface normally."""
    import mcp.server.streamable_http as streamable_http

    apply_streamable_http_disconnect_patch()

    logger = streamable_http.logger
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("Error in standalone SSE writer")

        # Different message + disconnect exc — also should pass through unchanged.
        try:
            raise anyio.ClosedResourceError()
        except anyio.ClosedResourceError:
            logger.exception("Something else entirely")

    records = [rec for rec in caplog.records if rec.name == logger.name]
    # Unrelated RuntimeError keeps its ERROR level and traceback.
    runtime_rec = next(r for r in records if r.getMessage() == "Error in standalone SSE writer")
    assert runtime_rec.levelno == logging.ERROR
    assert runtime_rec.exc_info is not None

    # Disconnect under a different message is untouched.
    other_rec = next(r for r in records if r.getMessage() == "Something else entirely")
    assert other_rec.levelno == logging.ERROR
    assert other_rec.exc_info is not None


async def test_streamable_http_client_disconnect_patch_is_idempotent() -> None:
    """Repeated calls MUST leave exactly one client-side filter instance attached."""
    import mcp.client.streamable_http as streamable_http

    from butlers.mcp_patches import _ClientSseParseDisconnectFilter

    apply_streamable_http_client_disconnect_patch()
    apply_streamable_http_client_disconnect_patch()
    apply_streamable_http_client_disconnect_patch()

    filters = [
        f for f in streamable_http.logger.filters if isinstance(f, _ClientSseParseDisconnectFilter)
    ]
    assert len(filters) == 1


async def test_streamable_http_client_disconnect_patch_downgrades_closed_resource_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expected client-side teardown tracebacks MUST be downgraded to DEBUG."""
    import mcp.client.streamable_http as streamable_http

    apply_streamable_http_client_disconnect_patch()

    logger = streamable_http.logger
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        try:
            raise anyio.ClosedResourceError()
        except anyio.ClosedResourceError:
            logger.exception("Error parsing SSE message")

    target = [rec for rec in caplog.records if rec.name == logger.name]
    assert target, "expected at least one log record on the streamable_http client logger"
    rec = target[-1]
    assert rec.levelno == logging.DEBUG
    assert rec.exc_info is None
    assert rec.exc_text is None
    assert rec.getMessage() == "Streamable HTTP SSE reader closed during client disconnect"
    assert not any("Error parsing SSE message" in r.getMessage() for r in target)


async def test_streamable_http_client_disconnect_patch_preserves_unrelated_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Actual parse/runtime failures on the client logger MUST surface normally."""
    import mcp.client.streamable_http as streamable_http

    apply_streamable_http_client_disconnect_patch()

    logger = streamable_http.logger
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("Error parsing SSE message")

        try:
            raise anyio.ClosedResourceError()
        except anyio.ClosedResourceError:
            logger.exception("Different client message")

    records = [rec for rec in caplog.records if rec.name == logger.name]
    parse_rec = next(r for r in records if r.getMessage() == "Error parsing SSE message")
    assert parse_rec.levelno == logging.ERROR
    assert parse_rec.exc_info is not None

    other_rec = next(r for r in records if r.getMessage() == "Different client message")
    assert other_rec.levelno == logging.ERROR
    assert other_rec.exc_info is not None


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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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


async def test_health_degraded(butler_dir: Path) -> None:
    """status() returns health=degraded when pool raises or DB is None."""
    patches = _patch_infra()
    mock_pool = patches["mock_pool"]
    daemon, status_fn = await _get_status_fn(butler_dir, patches)
    assert status_fn is not None

    mock_pool.fetchval = AsyncMock(side_effect=ConnectionRefusedError("refused"))
    result = await status_fn()
    assert result["health"] == "degraded"

    # DB is None also degrades
    mock_pool.fetchval = AsyncMock(return_value=1)
    daemon.db = None
    result2 = await status_fn()
    assert result2["health"] == "degraded"


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
            "butlers.lifecycle.validate_credentials",
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
# Switchboard client connection lifecycle
# ---------------------------------------------------------------------------


async def test_switchboard_client_lifecycle(butler_dir: Path) -> None:
    """Client None pre-start; successful connect sets client; shutdown clears it; connect failure non-fatal."""
    # Before start: client is always None
    assert ButlerDaemon(butler_dir).switchboard_client is None

    # Successful connect → client set; shutdown → client cleared
    patches = _patch_infra()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    apply_patch_mock = MagicMock()
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
        patch("butlers.switchboard_wiring.MCPClient", return_value=mock_client),
        patch(
            "butlers.switchboard_wiring.apply_streamable_http_client_disconnect_patch",
            apply_patch_mock,
        ),
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
    assert daemon.switchboard_client is mock_client
    apply_patch_mock.assert_called_once_with()
    await daemon.shutdown()
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
        patch("butlers.switchboard_wiring.MCPClient", return_value=mock_client2),
    ):
        daemon2 = ButlerDaemon(butler_dir)
        await daemon2.start()
    assert daemon2.switchboard_client is None
    assert daemon2._started_at is not None


async def test_switchboard_heartbeat_uses_ping_for_liveness_probe(butler_dir: Path) -> None:
    """Heartbeat checks Switchboard liveness with ping(), not list_tools()."""
    daemon = ButlerDaemon(butler_dir)
    ping_called = asyncio.Event()

    class _Client:
        def __init__(self) -> None:
            self.ping_calls = 0
            self.list_tools_calls = 0

        async def ping(self) -> bool:
            self.ping_calls += 1
            ping_called.set()
            return True

        async def list_tools(self) -> list[object]:
            self.list_tools_calls += 1
            return []

    client = _Client()
    daemon.switchboard_client = client

    with patch("butlers.switchboard_wiring._SWITCHBOARD_HEARTBEAT_INTERVAL_S", 0):
        task = asyncio.create_task(daemon._switchboard_heartbeat_loop())
        await asyncio.wait_for(ping_called.wait(), timeout=1.0)
        task.cancel()
        await task

    assert client.ping_calls == 1
    assert client.list_tools_calls == 0


async def test_switchboard_heartbeat_reconnects_on_stale_ping_error(butler_dir: Path) -> None:
    """A stale Switchboard client is evicted and reconnected on heartbeat."""
    daemon = ButlerDaemon(butler_dir)
    ping_called = asyncio.Event()
    reconnect_done = asyncio.Event()

    class _Client:
        def __init__(self) -> None:
            self.ping_calls = 0

        async def ping(self) -> None:
            self.ping_calls += 1
            ping_called.set()
            raise anyio.ClosedResourceError()

    client = _Client()
    daemon.switchboard_client = client

    disconnect = AsyncMock()

    async def connect(_: Any) -> None:
        reconnect_done.set()

    with (
        patch("butlers.switchboard_wiring._SWITCHBOARD_HEARTBEAT_INTERVAL_S", 0),
        patch("butlers.switchboard_wiring.disconnect_switchboard", new=disconnect),
        patch("butlers.switchboard_wiring.connect_switchboard", new=AsyncMock(side_effect=connect)),
    ):
        task = asyncio.create_task(daemon._switchboard_heartbeat_loop())
        await asyncio.wait_for(ping_called.wait(), timeout=1.0)
        await asyncio.wait_for(reconnect_done.wait(), timeout=1.0)
        task.cancel()
        await task

    assert client.ping_calls == 1
    disconnect.assert_awaited_once_with(daemon)


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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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


async def test_notify_schema_and_channels(butler_dir: Path) -> None:
    """notify schema contract; unsupported channel rejects; no switchboard rejects valid channels."""
    patches = _patch_infra()
    runtime_mcp = RuntimeFastMCP("test-butler")

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=runtime_mcp),
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

    params = notify_tool["parameters"]
    channel_schema = params["properties"]["channel"]
    # `channel` is optional (`Literal[...] | None`), so its JSON schema may carry
    # the enum at the top level OR inside an `anyOf` branch (string-enum branch +
    # null branch). Extract the enum robustly from whichever shape is generated.
    if "enum" in channel_schema:
        channel_enum = set(channel_schema["enum"])
        permits_null = channel_schema.get("type") == "null"
    else:
        branches = channel_schema.get("anyOf", [])
        channel_enum = {v for b in branches for v in b.get("enum", [])}
        permits_null = any(b.get("type") == "null" for b in branches)
    # Advertised channels must match notify()'s _SUPPORTED_CHANNELS exactly
    # (telegram, email). whatsapp delivery is wired at the routing layer but is
    # NOT enabled through the notify() tool, so it must not be over-advertised.
    assert channel_enum == {"telegram", "email"}
    # Optional contract: the schema must permit null/omission of channel.
    permits_omission = permits_null or "channel" not in params.get("required", [])
    assert permits_omission, f"channel must be optional; schema={channel_schema!r}"

    # Unsupported channel → error
    patches2 = _patch_infra()
    _, notify_fn = await _start_daemon_with_notify(butler_dir, patches2)
    result = await notify_fn(channel="sms", message="Hello")
    assert result["status"] == "error"
    assert "Unsupported channel" in result["error"]

    # whatsapp is advertised nowhere and rejected at runtime: the signature and
    # _SUPPORTED_CHANNELS stay in sync (regression guard for bu-82ufx).
    result_wa = await notify_fn(channel="whatsapp", message="Hello")
    assert result_wa["status"] == "error"
    assert "Unsupported channel" in result_wa["error"]

    # Valid channel, no switchboard → error (not channel error)
    result2 = await notify_fn(channel="email", message="Hello")
    assert result2["status"] == "error"
    assert "Switchboard is not connected" in result2["error"]


async def test_notify_delivery_and_failures(butler_dir: Path) -> None:
    """Delivery payload correct; telegram owner resolution; delivery errors surfaced; connection errors reported."""
    patches = _patch_infra()
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
    assert notify_fn is not None

    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {"notification_id": "abc-123", "status": "sent"}
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    daemon.switchboard_client = mock_client

    # Successful delivery: payload has source_butler + schema_version
    result = await notify_fn(channel="email", message="Hello world")
    assert result["status"] == "ok"
    payload = mock_client.call_tool.await_args.args[1]
    assert payload["source_butler"] == "test-butler"
    assert payload["notify_request"]["schema_version"] == "notify.v1"

    # Telegram owner resolution: with chat_id → success; without → error
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    with patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value="123456789")):
        r_tg = await notify_fn(channel="telegram", message="Update", intent="send")
    assert r_tg["status"] == "ok"

    with patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=None)):
        r_tg2 = await notify_fn(channel="telegram", message="Update", intent="send")
    assert r_tg2["status"] == "error"

    # Delivery failure surfaced
    mock_call_result_fail = MagicMock()
    mock_call_result_fail.is_error = False
    mock_call_result_fail.data = {
        "notification_id": "fail-id",
        "status": "failed",
        "error": "source_thread_identity required",
        "error_class": "validation_error",
        "retryable": False,
    }
    mock_client.call_tool = AsyncMock(return_value=mock_call_result_fail)
    r_fail = await notify_fn(channel="email", message="Hello")
    assert r_fail["status"] == "error"
    assert r_fail["error_class"] == "validation_error"

    # Empty message rejected
    r_empty = await notify_fn(channel="telegram", message="")
    assert r_empty["status"] == "error"

    # Connection error
    mock_client2 = AsyncMock()
    mock_client2.call_tool = AsyncMock(side_effect=ConnectionError("refused"))
    daemon.switchboard_client = mock_client2
    r_conn = await notify_fn(channel="email", message="Hello")
    assert r_conn["status"] == "error"
    assert "unreachable" in r_conn["error"].lower()


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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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
    """route.execute: missing notify_request errors; success delivers; missing recipient errors."""
    # Missing notify_request → validation_error
    m1 = tmp_path / "m1"
    m1.mkdir()
    patches = _patch_infra()
    butler_dir = _make_butler_toml(
        m1, butler_name="messenger", modules={"telegram": {}, "email": {}}
    )
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

    # Success: delivery proceeds; notify_response normalized
    m2 = tmp_path / "m2"
    m2.mkdir()
    patches2 = _patch_infra()
    butler_dir2 = _make_butler_toml(
        m2, butler_name="messenger", modules={"telegram": {}, "email": {}}
    )
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
    assert result2["status"] == "ok"
    nr = result2["result"]["notify_response"]
    assert nr["status"] == "ok"
    assert nr["delivery"]["channel"] == "telegram"

    # Missing required recipient → validation_error
    m3 = tmp_path / "m3"
    m3.mkdir()
    patches3 = _patch_infra()
    butler_dir3 = _make_butler_toml(
        m3, butler_name="messenger", modules={"telegram": {}, "email": {}}
    )
    _, fn3 = await _start_daemon_with_route_execute(butler_dir3, patches3)
    assert fn3 is not None

    result3 = await fn3(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={
            "prompt": "Deliver.",
            "context": {
                "notify_request": {
                    "schema_version": "notify.v1",
                    "origin_butler": "health",
                    "delivery": {"intent": "send", "channel": "email", "message": "Report ready."},
                }
            },
        },
    )
    assert result3["status"] == "error"
    assert result3["error"]["class"] == "validation_error"


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
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={"stub_a": ["STUB_A_TOKEN"]},
        ),
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp2),
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


async def test_staffer_briefing_exclusion_and_type_field(tmp_path: Path) -> None:
    """Staffer excludes daily_briefing_contribution from schedules; liveness payload includes type='staffer'."""
    # Part 1: schedule filtering
    butler_dir = _make_staffer_toml(tmp_path, with_briefing_schedule=True)
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
    schedules_arg = mock_sync.call_args[0][1]
    job_names = [s.get("job_name") for s in schedules_arg]
    assert "daily_briefing_contribution" not in job_names
    assert "some_other_job" in job_names

    # Part 2: liveness payload type field
    sub = tmp_path / "t2"
    sub.mkdir()
    butler_dir2 = _make_staffer_toml(sub, with_briefing_schedule=False)
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
        patches2["connect_switchboard"],
        patches2["create_audit_pool"],
        patches2["recover_route_inbox"],
    ):
        daemon2 = ButlerDaemon(butler_dir2)
        await daemon2.start()

    try:
        daemon2._liveness_reporter_task.cancel()
        try:
            await daemon2._liveness_reporter_task
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
                await daemon2._liveness_reporter_loop()
            except asyncio.CancelledError:
                pass

        assert len(posted_payloads) >= 1
        assert all(p["type"] == "staffer" for p in posted_payloads)
    finally:
        await daemon2.shutdown()
