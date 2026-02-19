"""Tests for module runtime enabled/disabled state management.

Covers:
- ModuleRuntimeState dataclass
- get_module_states() method
- set_module_enabled() method
- Startup initialization from state store (sticky toggles)
- Default enabled=True for healthy modules on first boot
- Failed/cascade_failed modules default to enabled=False
- Unavailable modules cannot be toggled to enabled
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import (
    _MODULE_ENABLED_KEY_PREFIX,
    _MODULE_ENABLED_KEY_SUFFIX,
    ButlerDaemon,
    ModuleRuntimeState,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub modules
# ---------------------------------------------------------------------------


class StubModuleA(Module):
    @property
    def name(self) -> str:
        return "stub_a"

    @property
    def config_schema(self) -> type[BaseModel]:
        return BaseModel

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class StubModuleB(Module):
    @property
    def name(self) -> str:
        return "stub_b"

    @property
    def config_schema(self) -> type[BaseModel]:
        return BaseModel

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
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
            return val  # None if not present
        return MagicMock()

    async def _execute(sql, key, *args):
        if "INSERT INTO state" in sql:
            # Extract value — third positional arg is the JSON string
            value_str = args[0] if args else None
            if value_str is not None:
                store[key] = json.loads(value_str)
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _patch_infra(mock_pool: AsyncMock | None = None):
    """Return a dict of patches for all infrastructure dependencies."""
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
            "butlers.daemon.validate_module_credentials", return_value={}
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
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
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
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    return daemon


# ---------------------------------------------------------------------------
# Unit tests: ModuleRuntimeState dataclass
# ---------------------------------------------------------------------------


class TestModuleRuntimeStateDataclass:
    def test_active_enabled_state(self) -> None:
        state = ModuleRuntimeState(health="active", enabled=True)
        assert state.health == "active"
        assert state.enabled is True
        assert state.failure_phase is None
        assert state.failure_error is None

    def test_failed_state_with_details(self) -> None:
        state = ModuleRuntimeState(
            health="failed",
            enabled=False,
            failure_phase="credentials",
            failure_error="Missing STUB_TOKEN",
        )
        assert state.health == "failed"
        assert state.enabled is False
        assert state.failure_phase == "credentials"
        assert state.failure_error == "Missing STUB_TOKEN"

    def test_cascade_failed_state(self) -> None:
        state = ModuleRuntimeState(
            health="cascade_failed",
            enabled=False,
            failure_phase="dependency",
            failure_error="Dependency 'stub_a' failed",
        )
        assert state.health == "cascade_failed"
        assert state.enabled is False

    def test_enabled_flag_is_mutable(self) -> None:
        state = ModuleRuntimeState(health="active", enabled=True)
        state.enabled = False
        assert state.enabled is False


# ---------------------------------------------------------------------------
# Unit tests: _init_module_runtime_states
# ---------------------------------------------------------------------------


class TestInitModuleRuntimeStates:
    """Tests for the startup initialization of runtime states."""

    async def test_healthy_module_defaults_to_enabled_on_first_boot(self, tmp_path: Path) -> None:
        """A healthy module with no prior state store entry should be enabled."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        assert "stub_a" in states
        assert states["stub_a"].health == "active"
        assert states["stub_a"].enabled is True

    async def test_failed_module_defaults_to_disabled(self, tmp_path: Path) -> None:
        """A module that failed startup should have enabled=False."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

        class FailingModuleA(StubModuleA):
            async def on_startup(self, config: Any, db: Any) -> None:
                raise RuntimeError("startup exploded")

        registry = _make_registry(FailingModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        assert states["stub_a"].health == "failed"
        assert states["stub_a"].enabled is False
        assert states["stub_a"].failure_phase == "startup"

    async def test_sticky_toggle_honored_on_restart(self, tmp_path: Path) -> None:
        """When state store has enabled=False for a healthy module, it stays disabled."""
        store = {"module::stub_a::enabled": False}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        states = daemon.get_module_states()
        assert states["stub_a"].enabled is False
        assert states["stub_a"].health == "active"

    async def test_sticky_enabled_honored_on_restart(self, tmp_path: Path) -> None:
        """When state store has enabled=True for a healthy module, it stays enabled."""
        store = {"module::stub_a::enabled": True}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        states = daemon.get_module_states()
        assert states["stub_a"].enabled is True

    async def test_multiple_modules_initialized(self, tmp_path: Path) -> None:
        """All modules appear in runtime states after startup."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}, "stub_b": {}})
        registry = _make_registry(StubModuleA, StubModuleB)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        assert set(states.keys()) == {"stub_a", "stub_b"}
        assert states["stub_a"].enabled is True
        assert states["stub_b"].enabled is True

    async def test_failed_module_persists_disabled_to_store(self, tmp_path: Path) -> None:
        """A failed module should have enabled=False written to the state store."""
        store: dict = {}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

        class FailingModuleA(StubModuleA):
            async def on_startup(self, config: Any, db: Any) -> None:
                raise RuntimeError("boom")

        registry = _make_registry(FailingModuleA)
        await _start_daemon(butler_dir, registry=registry, state_store=store)

        assert store.get("module::stub_a::enabled") is False


# ---------------------------------------------------------------------------
# Unit tests: get_module_states()
# ---------------------------------------------------------------------------


class TestGetModuleStates:
    async def test_returns_empty_dict_before_startup(self, tmp_path: Path) -> None:
        """Before start(), _module_runtime_states is empty."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        assert daemon.get_module_states() == {}

    async def test_returns_copy_not_reference(self, tmp_path: Path) -> None:
        """get_module_states() returns a copy; mutating it doesn't affect daemon."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        states.clear()
        # Original state on daemon is unaffected
        assert len(daemon.get_module_states()) == 1

    async def test_includes_failure_details_for_failed_modules(self, tmp_path: Path) -> None:
        """Failed modules include failure_phase and failure_error in their state."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

        class FailingModuleA(StubModuleA):
            async def on_startup(self, config: Any, db: Any) -> None:
                raise RuntimeError("db connection refused")

        registry = _make_registry(FailingModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        states = daemon.get_module_states()
        assert states["stub_a"].failure_phase == "startup"
        assert "db connection refused" in (states["stub_a"].failure_error or "")


# ---------------------------------------------------------------------------
# Unit tests: set_module_enabled()
# ---------------------------------------------------------------------------


class TestSetModuleEnabled:
    async def test_disable_active_module(self, tmp_path: Path) -> None:
        """A healthy module can be disabled via set_module_enabled()."""
        store: dict = {}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        result = await daemon.set_module_enabled("stub_a", False)
        assert result is True
        assert daemon.get_module_states()["stub_a"].enabled is False

    async def test_re_enable_disabled_module(self, tmp_path: Path) -> None:
        """A healthy module that was disabled can be re-enabled."""
        store = {"module::stub_a::enabled": False}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        result = await daemon.set_module_enabled("stub_a", True)
        assert result is True
        assert daemon.get_module_states()["stub_a"].enabled is True

    async def test_persists_change_to_state_store(self, tmp_path: Path) -> None:
        """set_module_enabled() writes the new value to the state store."""
        store: dict = {}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        await daemon.set_module_enabled("stub_a", False)
        assert store.get("module::stub_a::enabled") is False

        await daemon.set_module_enabled("stub_a", True)
        assert store.get("module::stub_a::enabled") is True

    async def test_raises_for_unknown_module(self, tmp_path: Path) -> None:
        """set_module_enabled() raises ValueError for an unknown module name."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon = await _start_daemon(butler_dir, state_store={})

        with pytest.raises(ValueError, match="Unknown module"):
            await daemon.set_module_enabled("nonexistent", True)

    async def test_raises_for_failed_module_enable_attempt(self, tmp_path: Path) -> None:
        """Attempting to enable a failed module raises ValueError."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

        class FailingModuleA(StubModuleA):
            async def on_startup(self, config: Any, db: Any) -> None:
                raise RuntimeError("startup failed")

        registry = _make_registry(FailingModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

        with pytest.raises(ValueError, match="unavailable"):
            await daemon.set_module_enabled("stub_a", True)

    async def test_raises_for_cascade_failed_module_enable_attempt(self, tmp_path: Path) -> None:
        """Attempting to enable a cascade_failed module raises ValueError."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

        daemon = await _start_daemon(butler_dir, state_store={})
        # Manually inject a cascade_failed state to simulate
        daemon._module_runtime_states["fake_cascade"] = ModuleRuntimeState(
            health="cascade_failed", enabled=False, failure_phase="dependency"
        )

        with pytest.raises(ValueError, match="unavailable"):
            await daemon.set_module_enabled("fake_cascade", True)

    async def test_disable_already_disabled_module_is_idempotent(self, tmp_path: Path) -> None:
        """set_module_enabled(False) on an already-disabled module succeeds."""
        store = {"module::stub_a::enabled": False}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        result = await daemon.set_module_enabled("stub_a", False)
        assert result is True
        assert daemon.get_module_states()["stub_a"].enabled is False

    async def test_state_store_key_format(self, tmp_path: Path) -> None:
        """The state store key follows the module::{name}::enabled convention."""
        store: dict = {}
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
        registry = _make_registry(StubModuleA)
        daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

        await daemon.set_module_enabled("stub_a", False)
        expected_key = f"{_MODULE_ENABLED_KEY_PREFIX}stub_a{_MODULE_ENABLED_KEY_SUFFIX}"
        assert expected_key in store
        assert store[expected_key] is False
