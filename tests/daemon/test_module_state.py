"""Tests for module runtime enabled/disabled state management — condensed.

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
    _MODULE_DISABLED_BY_KEY_SUFFIX,
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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
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


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


def _make_mock_pool(state_store: dict | None = None) -> AsyncMock:
    """Return a mock pool whose fetchval respects a simple in-memory state store."""
    store = state_store if state_store is not None else {}
    versions: dict[str, int] = {}
    pool = AsyncMock()

    async def _fetchval(sql, key, *args):
        if "SELECT value FROM state" in sql:
            if key not in store:
                return None
            return json.dumps(store[key])
        if "INSERT INTO state" in sql:
            value_str = args[0] if args else None
            if value_str is not None:
                store[key] = json.loads(value_str)
            versions[key] = versions.get(key, 0) + 1
            return versions[key]
        if "SELECT version FROM state" in sql:
            return versions.get(key)
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
    mock_db.db_name = "butlers"

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
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(
            ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock
        ),
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
# ModuleRuntimeState dataclass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "health,enabled,failure_phase,failure_error",
    [
        ("active", True, None, None),
        ("failed", False, "credentials", "Missing STUB_TOKEN"),
        ("cascade_failed", False, "dependency", "Dependency 'stub_a' failed"),
    ],
)
def test_module_runtime_state_dataclass(health, enabled, failure_phase, failure_error):
    state = ModuleRuntimeState(
        health=health,
        enabled=enabled,
        failure_phase=failure_phase,
        failure_error=failure_error,
    )
    assert state.health == health
    assert state.enabled == enabled
    assert state.failure_phase == failure_phase
    assert state.failure_error == failure_error

    # enabled flag is mutable
    state.enabled = not enabled
    assert state.enabled != enabled


# ---------------------------------------------------------------------------
# Startup initialization and self-healing
# ---------------------------------------------------------------------------


async def test_init_module_states_startup_behavior(tmp_path: Path) -> None:
    """Healthy defaults enabled=True; failed defaults False+persists; sticky state honored; multiple modules initialized."""
    # Healthy first boot
    d1 = tmp_path / "d1"
    d1.mkdir()
    butler_dir = _make_butler_toml(d1, modules={"stub_a": {}})
    registry = _make_registry(StubModuleA)
    daemon = await _start_daemon(butler_dir, registry=registry, state_store={})
    states = daemon.get_module_states()
    assert states["stub_a"].health == "active"
    assert states["stub_a"].enabled is True

    class FailingModuleA(StubModuleA):
        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
            raise RuntimeError("startup exploded")

    # Failed startup: disabled + persisted
    store: dict = {}
    registry2 = _make_registry(FailingModuleA)
    daemon2 = await _start_daemon(butler_dir, registry=registry2, state_store=store)
    states2 = daemon2.get_module_states()
    assert states2["stub_a"].health == "failed"
    assert states2["stub_a"].enabled is False
    assert states2["stub_a"].failure_phase == "startup"
    assert store.get("module::stub_a::enabled") is False
    assert store.get("module::stub_a::disabled_by") == "failure"

    # Sticky user-disabled honored; two modules both initialized
    d2 = tmp_path / "d2"
    d2.mkdir()
    butler_dir2 = _make_butler_toml(d2, modules={"stub_a": {}, "stub_b": {}})
    store_disabled = {"module::stub_a::enabled": False, "module::stub_a::disabled_by": "user"}
    registry3 = _make_registry(StubModuleA, StubModuleB)
    daemon3 = await _start_daemon(butler_dir2, registry=registry3, state_store=store_disabled)
    states3 = daemon3.get_module_states()
    assert set(states3.keys()) == {"stub_a", "stub_b"}
    assert states3["stub_a"].enabled is False
    assert states3["stub_b"].enabled is True


@pytest.mark.parametrize(
    "disabled_by,module_fails,expect_enabled",
    [
        ("failure", False, True),   # failure-disabled auto-heals on healthy restart
        ("user", False, False),     # user-disabled stays disabled
        (None, False, True),        # legacy entry (no disabled_by) auto-heals
        ("failure", True, False),   # repeated failure stays disabled
    ],
)
async def test_self_healing_behavior(tmp_path, disabled_by, module_fails, expect_enabled):
    """Modules disabled by failure auto-heal on healthy restart; user-disabled do not."""
    store: dict = {"module::stub_a::enabled": False}
    if disabled_by is not None:
        store["module::stub_a::disabled_by"] = disabled_by

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

    class FailingModuleA(StubModuleA):
        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
            raise RuntimeError("still broken")

    registry = _make_registry(FailingModuleA if module_fails else StubModuleA)
    daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)
    assert daemon.get_module_states()["stub_a"].enabled is expect_enabled


# ---------------------------------------------------------------------------
# get_module_states()
# ---------------------------------------------------------------------------


async def test_get_module_states_behavior(tmp_path: Path) -> None:
    """Empty before startup; returns copy; failure details included."""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})

    # Empty before startup
    assert ButlerDaemon(butler_dir).get_module_states() == {}

    class FailingModuleA(StubModuleA):
        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
            raise RuntimeError("db connection refused")

    registry = _make_registry(FailingModuleA)
    daemon = await _start_daemon(butler_dir, registry=registry, state_store={})

    states = daemon.get_module_states()
    # Returns copy — mutations don't affect daemon
    states.clear()
    assert len(daemon.get_module_states()) == 1

    # Failure details present
    states2 = daemon.get_module_states()
    assert states2["stub_a"].failure_phase == "startup"
    assert "db connection refused" in (states2["stub_a"].failure_error or "")


# ---------------------------------------------------------------------------
# set_module_enabled()
# ---------------------------------------------------------------------------


async def test_set_module_enabled_lifecycle_and_errors(tmp_path: Path) -> None:
    """Disable/enable cycle; persists to store; idempotent; unknown/failed modules raise."""
    # Lifecycle: disable → re-enable → idempotent double-disable
    store: dict = {}
    (tmp_path / "a").mkdir()
    butler_dir = _make_butler_toml(tmp_path / "a", modules={"stub_a": {}})
    registry = _make_registry(StubModuleA)
    daemon = await _start_daemon(butler_dir, registry=registry, state_store=store)

    assert await daemon.set_module_enabled("stub_a", False) is True
    assert daemon.get_module_states()["stub_a"].enabled is False
    assert store.get("module::stub_a::enabled") is False
    assert store.get("module::stub_a::disabled_by") == "user"

    assert await daemon.set_module_enabled("stub_a", True) is True
    assert daemon.get_module_states()["stub_a"].enabled is True
    assert store.get("module::stub_a::enabled") is True
    assert store.get("module::stub_a::disabled_by") is None

    await daemon.set_module_enabled("stub_a", False)
    assert await daemon.set_module_enabled("stub_a", False) is True

    enabled_key = f"{_MODULE_ENABLED_KEY_PREFIX}stub_a{_MODULE_ENABLED_KEY_SUFFIX}"
    disabled_by_key = f"{_MODULE_ENABLED_KEY_PREFIX}stub_a{_MODULE_DISABLED_BY_KEY_SUFFIX}"
    assert enabled_key in store and disabled_by_key in store

    # Error cases: unknown/failed/cascade_failed raise ValueError
    class FailingModuleA(StubModuleA):
        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
            raise RuntimeError("startup failed")

    (tmp_path / "b").mkdir()
    butler_dir2 = _make_butler_toml(tmp_path / "b", modules={"stub_a": {}})
    registry2 = _make_registry(FailingModuleA)
    daemon2 = await _start_daemon(butler_dir2, registry=registry2, state_store={})

    with pytest.raises(ValueError, match="Unknown module"):
        await daemon2.set_module_enabled("nonexistent", True)

    with pytest.raises(ValueError, match="unavailable"):
        await daemon2.set_module_enabled("stub_a", True)

    daemon2._module_runtime_states["fake_cascade"] = ModuleRuntimeState(
        health="cascade_failed", enabled=False, failure_phase="dependency"
    )
    with pytest.raises(ValueError, match="unavailable"):
        await daemon2.set_module_enabled("fake_cascade", True)
