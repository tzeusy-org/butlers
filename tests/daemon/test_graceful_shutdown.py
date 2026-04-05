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

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
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

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
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

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
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
    mock_pool.fetchrow = AsyncMock(return_value=None)
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
    mock_audit_db.connect = AsyncMock()
    mock_audit_db.close = AsyncMock()
    mock_audit_db.pool = AsyncMock()

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
        "db_from_env": patch("butlers.daemon.Database.from_env", side_effect=_db_from_env_factory),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.daemon.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {
                    "binary_name": "claude",
                    "__init__": lambda self, **kwargs: None,
                },
            ),
        ),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
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

    async def test_spawner_initial_state_and_stop_accepting(self) -> None:
        """in_flight_count starts at 0; drain() with no sessions returns immediately;
        stop_accepting() causes trigger() to raise RuntimeError."""
        spawner = self._make_spawner()
        assert spawner.in_flight_count == 0
        # drain with no in-flight sessions returns immediately
        await spawner.drain(timeout=1.0)
        # stop_accepting rejects new triggers
        spawner.stop_accepting()
        with pytest.raises(RuntimeError, match="not accepting new triggers"):
            await spawner.trigger(prompt="hello", trigger_source="trigger_tool")

    async def test_drain_waits_for_completion(self) -> None:
        """drain() should wait for an in-flight session to finish."""
        session_completed = asyncio.Event()
        drain_started = asyncio.Event()

        class _SlowCompletingAdapter(RuntimeAdapter):
            @property
            def binary_name(self):
                return "mock"

            async def invoke(self, *args, **kwargs):
                drain_started.set()
                await asyncio.sleep(0.3)
                session_completed.set()
                return "done", [], None

            def build_config_file(self, mcp_servers, tmp_dir):
                p = tmp_dir / "mock.json"
                p.write_text("{}")
                return p

            def parse_system_prompt_file(self, config_dir):
                return ""

        spawner = self._make_spawner(runtime=_SlowCompletingAdapter())

        # Start a trigger in a background task
        trigger_task = asyncio.create_task(
            spawner.trigger(prompt="slow", trigger_source="trigger_tool")
        )

        # Wait for the session to actually start
        await drain_started.wait()

        # Now stop and drain
        spawner.stop_accepting()
        assert spawner.in_flight_count == 1

        await spawner.drain(timeout=5.0)

        # Session should have completed
        assert session_completed.is_set()
        assert spawner.in_flight_count == 0

        # Clean up
        await trigger_task

    async def test_drain_timeout_cancels_sessions(self) -> None:
        """drain() should cancel sessions that exceed the timeout."""
        session_started = asyncio.Event()
        spawner = self._make_spawner(runtime=_SlowMockAdapter(started_event=session_started))

        # Start a trigger in a background task
        trigger_task = asyncio.create_task(
            spawner.trigger(prompt="hang", trigger_source="trigger_tool")
        )

        # Wait for the session to start
        await session_started.wait()

        # Now stop and drain with a very short timeout
        spawner.stop_accepting()
        assert spawner.in_flight_count == 1

        await spawner.drain(timeout=0.1)

        # After drain with timeout, in-flight should be cleared
        assert spawner.in_flight_count == 0

        # The trigger task should be cancelled
        with pytest.raises((asyncio.CancelledError, Exception)):
            await trigger_task


# ---------------------------------------------------------------------------
# Daemon shutdown tests
# ---------------------------------------------------------------------------


class TestDaemonGracefulShutdown:
    """Test the daemon's graceful shutdown sequence."""

    async def _start_daemon(
        self, butler_dir: Path, patches: dict, registry=None
    ) -> ButlerDaemon:
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

    async def test_shutdown_basics(self, tmp_path: Path) -> None:
        """shutdown() sets _accepting_connections=False; calls stop_accepting+drain;
        passes configured timeout to drain()."""
        # Part 1: accepting flag cleared; spawner stop_accepting + drain called
        patches = _patch_infra()
        daemon = await self._start_daemon(_make_butler_toml(tmp_path), patches)
        assert daemon._accepting_connections is True
        await daemon.shutdown()
        assert daemon._accepting_connections is False
        patches["mock_spawner"].stop_accepting.assert_called_once()
        patches["mock_spawner"].drain.assert_awaited_once()

        # Part 2: configured timeout forwarded to drain()
        subdir = tmp_path / "t2"
        subdir.mkdir()
        patches2 = _patch_infra()
        daemon2 = await self._start_daemon(_make_butler_toml(subdir, shutdown_timeout_s=15.0), patches2)
        await daemon2.shutdown()
        patches2["mock_spawner"].drain.assert_awaited_once_with(timeout=15.0)

    async def test_shutdown_order(self, tmp_path: Path) -> None:
        """Shutdown should: stop accepting, drain, module shutdown, close DB."""
        registry = _make_registry(StubModuleOk)
        patches = _patch_infra()
        call_order: list[str] = []
        patches["mock_spawner"].stop_accepting = MagicMock(
            side_effect=lambda: call_order.append("stop_accepting")
        )
        patches["mock_spawner"].drain = AsyncMock(
            side_effect=lambda **kw: call_order.append("drain")
        )

        butler_dir = _make_butler_toml(tmp_path, modules={"stub_ok": {}})
        daemon = await self._start_daemon(butler_dir, patches, registry=registry)

        for mod in daemon._modules:
            original = mod.on_shutdown

            async def make_tracker(name, orig=original):
                call_order.append(f"module_shutdown:{name}")
                await orig()

            mod.on_shutdown = lambda n=mod.name, o=original: make_tracker(n, o)

        patches["mock_db"].close = AsyncMock(side_effect=lambda: call_order.append("db_close"))
        await daemon.shutdown()

        assert call_order == ["stop_accepting", "drain", "module_shutdown:stub_ok", "db_close"]

    async def test_shutdown_without_spawner(self, tmp_path: Path) -> None:
        """shutdown() should work even if spawner was never created."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        # Manually set config so shutdown doesn't error on config access
        from butlers.config import ButlerConfig

        daemon.config = ButlerConfig(name="test", port=9100)
        # spawner is None — shutdown should still work
        await daemon.shutdown()
        assert daemon._accepting_connections is False


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

    async def test_module_startup_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """Failing module marked failed+startup; active modules not affected;
        failed module not shutdown; first-module failure also non-fatal."""
        # Part 1: stub_failing fails, stub_ok stays active; failed not shutdown
        registry = _make_registry(StubModuleOk, StubModuleFailing)
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_ok": {}, "stub_failing": {}})
        daemon = await self._start_daemon(butler_dir, registry=registry)

        stub_ok = next(m for m in daemon._modules if m.name == "stub_ok")
        assert stub_ok.started is True
        assert daemon._module_statuses["stub_failing"].status == "failed"
        assert daemon._module_statuses["stub_failing"].phase == "startup"
        assert daemon._module_statuses["stub_ok"].status == "active"

        await daemon.shutdown()
        assert stub_ok.shutdown_called is True
        stub_failing = next(m for m in daemon._modules if m.name == "stub_failing")
        assert stub_failing.shutdown_called is False

        # Part 2: first module failure also non-fatal; no on_shutdown for it
        class FirstModFails(Module):
            def __init__(self):
                self.shutdown_called = False

            @property
            def name(self) -> str:
                return "first_fail"

            @property
            def config_schema(self) -> type[BaseModel]:
                return StubConfig

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp, config, db) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None) -> None:
                raise RuntimeError("first module fails")

            async def on_shutdown(self) -> None:
                self.shutdown_called = True

        subdir = tmp_path / "ff"
        subdir.mkdir()
        daemon2 = await self._start_daemon(
            _make_butler_toml(subdir, modules={"first_fail": {}}),
            registry=_make_registry(FirstModFails),
        )
        assert daemon2._module_statuses["first_fail"].status == "failed"
        first_fail = next(m for m in daemon2._modules if m.name == "first_fail")
        assert first_fail.shutdown_called is False

    async def test_dependent_module_cascade_fails(self, tmp_path: Path) -> None:
        """Module depending on a failed module gets cascade-failed and never starts."""
        registry = _make_registry(StubModuleOk, StubModuleFailing, StubModuleAfterFailing)
        butler_dir = _make_butler_toml(
            tmp_path, modules={"stub_ok": {}, "stub_failing": {}, "stub_after": {}}
        )
        daemon = await self._start_daemon(butler_dir, registry=registry)

        assert daemon._module_statuses["stub_after"].status == "cascade_failed"
        stub_after = next(m for m in daemon._modules if m.name == "stub_after")
        assert stub_after.started is False
        assert daemon._module_statuses["stub_failing"].status == "failed"
        assert daemon._module_statuses["stub_ok"].status == "active"
