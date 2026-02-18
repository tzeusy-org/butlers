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

    async def on_startup(self, config: Any, db: Any) -> None:
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

    async def on_startup(self, config: Any, db: Any) -> None:
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

    async def on_startup(self, config: Any, db: Any) -> None:
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
        'name = "butler_test"',
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

    # Separate mock for the audit DB (butler_switchboard) so that its close()
    # does not interfere with call-order assertions on the main DB's close().
    mock_audit_db = MagicMock()
    mock_audit_db.connect = AsyncMock()
    mock_audit_db.close = AsyncMock()
    mock_audit_db.pool = AsyncMock()

    def _db_from_env_factory(db_name: str) -> MagicMock:
        if db_name == "butler_switchboard":
            return mock_audit_db
        return mock_db

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", side_effect=_db_from_env_factory),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials", return_value={}
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

    def test_default_timeout(self, tmp_path: Path) -> None:
        """Default shutdown_timeout_s should be 30.0 when not specified."""
        _make_butler_toml(tmp_path)
        config = load_config(tmp_path)
        assert config.shutdown_timeout_s == 30.0

    def test_custom_timeout(self, tmp_path: Path) -> None:
        """Custom shutdown_timeout_s should be parsed from [butler.shutdown]."""
        _make_butler_toml(tmp_path, shutdown_timeout_s=10.0)
        config = load_config(tmp_path)
        assert config.shutdown_timeout_s == 10.0

    def test_zero_timeout(self, tmp_path: Path) -> None:
        """Zero timeout should be accepted."""
        _make_butler_toml(tmp_path, shutdown_timeout_s=0)
        config = load_config(tmp_path)
        assert config.shutdown_timeout_s == 0.0


# ---------------------------------------------------------------------------
# Spawner draining tests
# ---------------------------------------------------------------------------


class TestSpawnerDraining:
    """Test Spawner session tracking and drain behavior."""

    def _make_spawner(self, sdk_query=None) -> Spawner:
        """Create a spawner with no pool (no DB logging)."""
        from butlers.config import ButlerConfig

        config = ButlerConfig(name="test", port=9100)
        return Spawner(
            config=config,
            config_dir=Path("/tmp/nonexistent"),
            pool=None,
            sdk_query=sdk_query,
        )

    async def test_stop_accepting_rejects_new_triggers(self) -> None:
        """After stop_accepting(), trigger() should raise RuntimeError."""
        spawner = self._make_spawner()
        spawner.stop_accepting()

        with pytest.raises(RuntimeError, match="not accepting new triggers"):
            await spawner.trigger(prompt="hello", trigger_source="trigger_tool")

    async def test_drain_no_sessions(self) -> None:
        """drain() with no in-flight sessions should return immediately."""
        spawner = self._make_spawner()
        # Should not raise or block
        await spawner.drain(timeout=1.0)

    async def test_in_flight_count_zero_initially(self) -> None:
        """in_flight_count should be 0 before any trigger."""
        spawner = self._make_spawner()
        assert spawner.in_flight_count == 0

    async def test_drain_waits_for_completion(self) -> None:
        """drain() should wait for an in-flight session to finish."""
        session_completed = asyncio.Event()
        drain_started = asyncio.Event()

        async def slow_sdk_query(prompt, options):
            # Signal that we're running, then wait to be released
            drain_started.set()
            await asyncio.sleep(0.3)
            session_completed.set()
            return
            yield  # Make it an async generator

        # Need a proper async generator
        async def slow_sdk(prompt, options):
            drain_started.set()
            await asyncio.sleep(0.3)
            session_completed.set()
            # Yield nothing — just complete
            return
            yield

        spawner = self._make_spawner(sdk_query=slow_sdk)

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

        async def hanging_sdk(prompt, options):
            session_started.set()
            # Hang forever — should be cancelled
            await asyncio.sleep(999)
            return
            yield

        spawner = self._make_spawner(sdk_query=hanging_sdk)

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

    async def test_shutdown_stops_accepting_connections(self, tmp_path: Path) -> None:
        """shutdown() should set _accepting_connections to False."""
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
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert daemon._accepting_connections is True

        await daemon.shutdown()

        assert daemon._accepting_connections is False

    async def test_shutdown_calls_spawner_stop_and_drain(self, tmp_path: Path) -> None:
        """shutdown() should call spawner.stop_accepting() and spawner.drain()."""
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
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        await daemon.shutdown()

        mock_spawner = patches["mock_spawner"]
        mock_spawner.stop_accepting.assert_called_once()
        mock_spawner.drain.assert_awaited_once()

    async def test_shutdown_uses_configured_timeout(self, tmp_path: Path) -> None:
        """shutdown() should pass the configured timeout to spawner.drain()."""
        butler_dir = _make_butler_toml(tmp_path, shutdown_timeout_s=15.0)
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
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        await daemon.shutdown()

        mock_spawner = patches["mock_spawner"]
        mock_spawner.drain.assert_awaited_once_with(timeout=15.0)

    async def test_shutdown_order(self, tmp_path: Path) -> None:
        """Shutdown should: stop accepting, drain, module shutdown, close DB."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_ok": {}},
        )
        registry = _make_registry(StubModuleOk)
        patches = _patch_infra()
        mock_db = patches["mock_db"]

        call_order: list[str] = []

        mock_spawner = patches["mock_spawner"]
        mock_spawner.stop_accepting = MagicMock(
            side_effect=lambda: call_order.append("stop_accepting")
        )
        mock_spawner.drain = AsyncMock(side_effect=lambda **kw: call_order.append("drain"))

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

        # Monkey-patch module on_shutdown to track order
        for mod in daemon._modules:
            original = mod.on_shutdown

            async def make_tracker(name, orig=original):
                call_order.append(f"module_shutdown:{name}")
                await orig()

            mod.on_shutdown = lambda n=mod.name, o=original: make_tracker(n, o)

        mock_db.close = AsyncMock(side_effect=lambda: call_order.append("db_close"))

        await daemon.shutdown()

        assert call_order == [
            "stop_accepting",
            "drain",
            "module_shutdown:stub_ok",
            "db_close",
        ]

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

    async def test_module_startup_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """If a module fails on_startup, the butler still starts; the module is marked failed."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_ok": {}, "stub_failing": {}},
        )
        registry = _make_registry(StubModuleOk, StubModuleFailing)
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
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()  # Should NOT raise

        # stub_ok started successfully and is active
        stub_ok = next(m for m in daemon._modules if m.name == "stub_ok")
        assert stub_ok.started is True

        # stub_failing is marked as failed in module statuses
        assert daemon._module_statuses["stub_failing"].status == "failed"
        assert daemon._module_statuses["stub_failing"].phase == "startup"

        # stub_ok is active
        assert daemon._module_statuses["stub_ok"].status == "active"

    async def test_dependent_module_cascade_fails(self, tmp_path: Path) -> None:
        """Module depending on a failed module gets cascade-failed."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_ok": {}, "stub_failing": {}, "stub_after": {}},
        )
        registry = _make_registry(StubModuleOk, StubModuleFailing, StubModuleAfterFailing)
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
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()  # Should NOT raise

        # stub_after depends on stub_failing, so it should cascade-fail
        assert daemon._module_statuses["stub_after"].status == "cascade_failed"

        # stub_after should NOT have started
        stub_after = next(m for m in daemon._modules if m.name == "stub_after")
        assert stub_after.started is False

        # stub_failing is recorded as failed
        assert daemon._module_statuses["stub_failing"].status == "failed"

        # stub_ok is active
        assert daemon._module_statuses["stub_ok"].status == "active"

    async def test_failed_module_not_shutdown(self, tmp_path: Path) -> None:
        """Failed modules do not get on_shutdown called during butler shutdown."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"stub_ok": {}, "stub_failing": {}},
        )
        registry = _make_registry(StubModuleOk, StubModuleFailing)
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
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        await daemon.shutdown()

        # stub_ok was active, so it should have on_shutdown called
        stub_ok = next(m for m in daemon._modules if m.name == "stub_ok")
        assert stub_ok.shutdown_called is True

        # stub_failing was never started successfully, so no on_shutdown
        stub_failing = next(m for m in daemon._modules if m.name == "stub_failing")
        assert stub_failing.shutdown_called is False

    async def test_first_module_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """If the first module fails, the butler still starts."""

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

            async def on_startup(self, config, db) -> None:
                raise RuntimeError("first module fails")

            async def on_shutdown(self) -> None:
                self.shutdown_called = True

        butler_dir = _make_butler_toml(tmp_path, modules={"first_fail": {}})
        registry = _make_registry(FirstModFails)
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
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()  # Should NOT raise

        # The failing module should be marked as failed
        assert daemon._module_statuses["first_fail"].status == "failed"

        # The failing module should NOT have on_shutdown called
        first_fail = next(m for m in daemon._modules if m.name == "first_fail")
        assert first_fail.shutdown_called is False
