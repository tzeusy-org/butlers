"""Tests for graceful shutdown with runtime session draining.

Covers:
- Normal shutdown with session draining
- Shutdown timeout expiry with session cancellation
- Startup failure cleanup of already-initialized modules
- Configurable shutdown_timeout_s in butler.toml
- Spawner rejecting new triggers after stop_accepting()
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.config import load_config
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.daemon import ButlerDaemon
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class StubConfig(BaseModel):
    """Config schema for stub modules."""


class StubModuleOk(Module):
    """Module that starts and shuts down without errors."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False

    @property
    def name(self) -> str:
        return "stub_ok"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str = "") -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


class StubModuleFailing(Module):
    """Module whose on_startup raises an error."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False

    @property
    def name(self) -> str:
        return "stub_failing"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return ["stub_ok"]

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str = "") -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        raise RuntimeError("Module startup failed")

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


class StubModuleAfterFailing(Module):
    """Module that depends on the failing module — should never start."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False

    @property
    def name(self) -> str:
        return "stub_after"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return ["stub_failing"]

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str = "") -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


def _make_butler_toml(
    tmp_path: Path,
    modules: dict | None = None,
    shutdown_timeout_s: float | None = None,
) -> Path:
    """Write a butler.toml with optional shutdown timeout and modules."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_butler"',
    ]
    if shutdown_timeout_s is not None:
        toml_lines.extend(
            [
                "",
                "[butler.shutdown]",
                f"timeout_s = {shutdown_timeout_s}",
            ]
        )
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            if isinstance(v, str):
                toml_lines.append(f'{k} = "{v}"')
            else:
                toml_lines.append(f"{k} = {v}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    """Create a ModuleRegistry with the given module classes pre-registered."""
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


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
    # Support `async with pool.acquire() as conn:` pattern
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetchval = AsyncMock(return_value=None)
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

    # Separate mock for the audit DB (switchboard schema) so that its close()
    # does not interfere with call-order assertions on the main DB's close().
    mock_audit_db = MagicMock()
    mock_audit_db.provision = AsyncMock()
    mock_audit_db.connect = AsyncMock(return_value=mock_pool)
    mock_audit_db.close = AsyncMock()
    mock_audit_db.pool = mock_pool

    _db_call_count = 0

    def _db_from_env_factory(db_name: str) -> MagicMock:
        nonlocal _db_call_count
        _db_call_count += 1
        # First call is the main butler DB; second is the audit pool
        if _db_call_count == 1:
            return mock_db
        return mock_audit_db

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    return {
        "db_from_env": patch(
            "butlers.lifecycle.Database.from_env", side_effect=_db_from_env_factory
        ),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.lifecycle.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {
                    "binary_name": "claude",
                    "__init__": lambda self, **kwargs: None,
                },
            ),
        ),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_audit_db": mock_audit_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestShutdownTimeoutConfig:
    """Verify shutdown_timeout_s is parsed from butler.toml."""

    def test_shutdown_timeout_config(self, tmp_path: Path) -> None:
        """Default 30.0; custom value parsed; zero accepted."""
        _make_butler_toml(tmp_path)
        assert load_config(tmp_path).shutdown_timeout_s == 30.0

        _make_butler_toml(tmp_path, shutdown_timeout_s=10.0)
        assert load_config(tmp_path).shutdown_timeout_s == 10.0

        _make_butler_toml(tmp_path, shutdown_timeout_s=0)
        assert load_config(tmp_path).shutdown_timeout_s == 0.0


# ---------------------------------------------------------------------------
# Spawner draining tests
# ---------------------------------------------------------------------------


class _SlowMockAdapter(RuntimeAdapter):
    """Adapter that signals startup and blocks until released."""

    def __init__(self, started_event: asyncio.Event, delay: float = 999) -> None:
        self._started_event = started_event
        self._delay = delay

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *args, **kwargs):
        self._started_event.set()
        await asyncio.sleep(self._delay)
        return "done", [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        p = tmp_dir / "mock.json"
        p.write_text("{}")
        return p

    def parse_system_prompt_file(self, config_dir):
        return ""


class _SimpleMockAdapter(RuntimeAdapter):
    """Adapter that returns immediately."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *args, **kwargs):
        return "ok", [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        p = tmp_dir / "mock.json"
        p.write_text("{}")
        return p

    def parse_system_prompt_file(self, config_dir):
        return ""


class TestSpawnerDraining:
    """Test Spawner session tracking and drain behavior."""

    def _make_spawner(self, runtime=None) -> Spawner:
        """Create a spawner with no pool (no DB logging)."""
        from butlers.config import ButlerConfig

        config = ButlerConfig(name="test", port=9100)
        return Spawner(
            config=config,
            config_dir=Path("/tmp/nonexistent"),
            pool=None,
            runtime=runtime,
        )

    async def test_spawner_drain_and_stop_accepting(self) -> None:
        """in_flight_count starts at 0; drain() with no sessions returns immediately;
        stop_accepting() rejects triggers; drain waits for completion; timeout cancels."""
        # Empty drain and stop_accepting
        spawner = self._make_spawner()
        assert spawner.in_flight_count == 0
        await spawner.drain(timeout=1.0)
        spawner.stop_accepting()
        with pytest.raises(RuntimeError, match="not accepting new triggers"):
            await spawner.trigger(prompt="hello", trigger_source="trigger_tool")

        # Drain timeout cancels sessions
        session_started = asyncio.Event()
        spawner2 = self._make_spawner(runtime=_SlowMockAdapter(started_event=session_started))
        trigger_task = asyncio.create_task(
            spawner2.trigger(prompt="hang", trigger_source="trigger_tool")
        )
        await session_started.wait()
        spawner2.stop_accepting()
        assert spawner2.in_flight_count == 1
        await spawner2.drain(timeout=0.1)
        assert spawner2.in_flight_count == 0
        with pytest.raises((asyncio.CancelledError, Exception)):
            await trigger_task


# ---------------------------------------------------------------------------
# Daemon shutdown tests
# ---------------------------------------------------------------------------


class TestDaemonGracefulShutdown:
    """Test the daemon's graceful shutdown sequence."""

    async def _start_daemon(self, butler_dir: Path, patches: dict, registry=None) -> ButlerDaemon:
        """Start a daemon with all infra patched, return the daemon."""
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
        ):
            kwargs = {"registry": registry} if registry is not None else {}
            daemon = ButlerDaemon(butler_dir, **kwargs)
            await daemon.start()
        return daemon

    async def test_shutdown_sequence(self, tmp_path: Path) -> None:
        """shutdown(): accepting_connections=False; stop_accepting + drain called with configured timeout;
        order is stop_accepting → drain → module_shutdown → db_close."""
        # Basic: accepting flag + drain/stop_accepting
        patches = _patch_infra()
        daemon = await self._start_daemon(_make_butler_toml(tmp_path), patches)
        assert daemon._accepting_connections is True
        await daemon.shutdown()
        assert daemon._accepting_connections is False
        patches["mock_spawner"].stop_accepting.assert_called_once()
        patches["mock_spawner"].drain.assert_awaited_once()

        # Configured timeout forwarded to drain()
        subdir = tmp_path / "t2"
        subdir.mkdir()
        patches2 = _patch_infra()
        daemon2 = await self._start_daemon(
            _make_butler_toml(subdir, shutdown_timeout_s=15.0), patches2
        )
        await daemon2.shutdown()
        patches2["mock_spawner"].drain.assert_awaited_once_with(timeout=15.0)

        # Correct shutdown order
        registry = _make_registry(StubModuleOk)
        patches3 = _patch_infra()
        call_order: list[str] = []
        patches3["mock_spawner"].stop_accepting = MagicMock(
            side_effect=lambda: call_order.append("stop_accepting")
        )
        patches3["mock_spawner"].drain = AsyncMock(
            side_effect=lambda **kw: call_order.append("drain")
        )
        subdir3 = tmp_path / "t3"
        subdir3.mkdir()
        butler_dir3 = _make_butler_toml(subdir3, modules={"stub_ok": {}})
        daemon3 = await self._start_daemon(butler_dir3, patches3, registry=registry)
        for mod in daemon3._modules:
            original = mod.on_shutdown

            async def make_tracker(name, orig=original):
                call_order.append(f"module_shutdown:{name}")
                await orig()

            mod.on_shutdown = lambda n=mod.name, o=original: make_tracker(n, o)
        patches3["mock_db"].close = AsyncMock(side_effect=lambda: call_order.append("db_close"))
        await daemon3.shutdown()
        assert call_order == ["stop_accepting", "drain", "module_shutdown:stub_ok", "db_close"]


# ---------------------------------------------------------------------------
# Startup failure cleanup tests
# ---------------------------------------------------------------------------


class TestStartupFailureCleanup:
    """Test that module startup failures are non-fatal and properly recorded."""

    async def _start_daemon(self, butler_dir: Path, registry=None) -> ButlerDaemon:
        """Start a daemon with all infra patched, return the daemon."""
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
        ):
            kwargs = {"registry": registry} if registry is not None else {}
            daemon = ButlerDaemon(butler_dir, **kwargs)
            await daemon.start()
        return daemon

    async def test_module_startup_failure_and_cascade(self, tmp_path: Path) -> None:
        """Failing module marked failed+startup; active modules unaffected; failed not shutdown;
        dependent module cascade-fails and never starts."""
        registry = _make_registry(StubModuleOk, StubModuleFailing, StubModuleAfterFailing)
        butler_dir = _make_butler_toml(
            tmp_path, modules={"stub_ok": {}, "stub_failing": {}, "stub_after": {}}
        )
        daemon = await self._start_daemon(butler_dir, registry=registry)

        stub_ok = next(m for m in daemon._modules if m.name == "stub_ok")
        assert stub_ok.started is True
        assert daemon._module_statuses["stub_failing"].status == "failed"
        assert daemon._module_statuses["stub_failing"].phase == "startup"
        assert daemon._module_statuses["stub_ok"].status == "active"
        assert daemon._module_statuses["stub_after"].status == "cascade_failed"
        stub_after = next(m for m in daemon._modules if m.name == "stub_after")
        assert stub_after.started is False

        await daemon.shutdown()
        assert stub_ok.shutdown_called is True
        stub_failing = next(m for m in daemon._modules if m.name == "stub_failing")
        assert stub_failing.shutdown_called is False
