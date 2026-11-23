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

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from butlers.daemon import (
    _MODULE_DISABLED_BY_KEY_SUFFIX,
    _MODULE_ENABLED_KEY_PREFIX,
    _MODULE_ENABLED_KEY_SUFFIX,
    ButlerDaemon,
    ModuleRuntimeState,
)
from butlers.module_state import ModuleStartupStatus
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry
from butlers.testing.daemon import write_butler_config
from butlers.testing.fake_pool import AsyncCtx
from butlers.testing.tool_host import host_tools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub modules
# ---------------------------------------------------------------------------


class StubModuleA(Module):
    name = "stub_a"

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
    name = "stub_b"

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
    """Return a mock pool whose fetchval respects a simple in-memory state store.

    Mirrors the asyncpg JSONB codec: writes pass Python objects directly
    (no pre-serialization) and reads return Python objects directly.
    """
    store = state_store if state_store is not None else {}
    versions: dict[str, int] = {}
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

    async def _fetchval(sql, *fargs):
        key = fargs[0] if fargs else None
        if "SELECT value FROM state" in sql:
            return store.get(key)
        if "INSERT INTO state" in sql:
            # state_set always passes value as the second positional after SQL.
            # Storing None is meaningful (clears the disabled_by marker).
            if len(fargs) > 1:
                store[key] = fargs[1]
            versions[key] = versions.get(key, 0) + 1
            return versions[key]
        if "SELECT version FROM state" in sql:
            return versions.get(key)
        return MagicMock()

    async def _execute(sql, *fargs):
        key = fargs[0] if fargs else None
        if "INSERT INTO state" in sql:
            if len(fargs) > 1:
                store[key] = fargs[1]
        elif "DELETE FROM state" in sql and key is not None:
            store.pop(key, None)
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    pool.fetch = AsyncMock(return_value=[])

    # pool.acquire() is used as `async with pool.acquire() as conn:` in some
    # daemon code paths (e.g. contact_info resolution). Provide a context
    # manager that yields a no-op connection so those paths don't crash.
    mock_conn = AsyncMock()
    transaction_depth = 0

    async def enter_transaction() -> None:
        nonlocal transaction_depth
        transaction_depth += 1

    async def exit_transaction(*_exc: object) -> bool:
        nonlocal transaction_depth
        transaction_depth -= 1
        return False

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(side_effect=enter_transaction)
    transaction.__aexit__ = AsyncMock(side_effect=exit_transaction)
    mock_conn.transaction = MagicMock(return_value=transaction)
    mock_conn.is_in_transaction = MagicMock(side_effect=lambda: transaction_depth > 0)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock(return_value=None)

    def _acquire():
        return AsyncCtx(mock_conn)

    pool.acquire = _acquire
    return pool


async def _init_states(
    butler_dir: Path,
    *modules: Module,
    state_store: dict | None = None,
    failed: dict[str, str] | None = None,
) -> ButlerDaemon:
    """Run the daemon's own ``_init_module_runtime_states`` phase on a DB-free host.

    *failed* maps a module to the startup error the startup phase recorded for it.
    The subject is how startup results and sticky state combine, not the boot; the
    first case in this file boots for real (bu-rvgzmf.2).
    """
    pool = _make_mock_pool(state_store)
    daemon = (await host_tools(butler_dir, pool=pool)).daemon
    daemon._modules = list(modules)
    daemon._module_statuses = {
        name: ModuleStartupStatus(status="failed", phase="startup", error=error)
        for name, error in (failed or {}).items()
    }
    await daemon._init_module_runtime_states(pool)
    return daemon


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_real_boot_initialises_and_persists_module_states(
    booted_daemon, tmp_path: Path
) -> None:
    """Startup enables healthy modules in the real state table; a toggle persists there."""
    from butlers.testing.db import open_pool

    config_dir = write_butler_config(
        tmp_path, "general", extra_toml="[modules.stub_a]\n\n[modules.stub_b]\n"
    )
    booted = await booted_daemon(config_dir, registry=_make_registry(StubModuleA, StubModuleB))
    daemon = booted.daemon

    states = daemon.get_module_states()
    assert {name: (s.health, s.enabled) for name, s in states.items()} == {
        "stub_a": ("active", True),
        "stub_b": ("active", True),
    }
    await daemon.set_module_enabled("stub_a", False)

    admin = await open_pool(booted.admin_url)
    try:
        rows = await admin.fetch(
            "SELECT key, value FROM general.state WHERE key LIKE 'module::stub_a::%'"
        )
    finally:
        await admin.close()
    assert {row["key"]: row["value"] for row in rows} == {
        "module::stub_a::enabled": False,
        "module::stub_a::disabled_by": "user",
    }


# ---------------------------------------------------------------------------
# ModuleRuntimeState dataclass
# ---------------------------------------------------------------------------


def test_module_runtime_state_dataclass():
    """ModuleRuntimeState holds health/enabled/failure fields; enabled is mutable."""
    state = ModuleRuntimeState(
        health="active", enabled=True, failure_phase=None, failure_error=None
    )
    assert state.health == "active"
    assert state.enabled is True
    state.enabled = False
    assert state.enabled is False

    # failed state has failure details
    state2 = ModuleRuntimeState(
        health="failed", enabled=False, failure_phase="credentials", failure_error="Missing TOKEN"
    )
    assert state2.failure_phase == "credentials"
    assert state2.failure_error == "Missing TOKEN"


# ---------------------------------------------------------------------------
# Startup initialization and self-healing
# ---------------------------------------------------------------------------


async def test_init_module_states_startup_behavior(tmp_path: Path) -> None:
    """Healthy defaults enabled=True; failed defaults False+persists; sticky state honored; multiple modules initialized."""
    # Healthy first boot
    d1 = tmp_path / "d1"
    d1.mkdir()
    butler_dir = _make_butler_toml(d1, modules={"stub_a": {}})
    daemon = await _init_states(butler_dir, StubModuleA(), state_store={})
    states = daemon.get_module_states()
    assert states["stub_a"].health == "active"
    assert states["stub_a"].enabled is True

    # Failed startup: disabled + persisted
    store: dict = {}
    daemon2 = await _init_states(
        butler_dir, StubModuleA(), state_store=store, failed={"stub_a": "startup exploded"}
    )
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
    daemon3 = await _init_states(
        butler_dir2, StubModuleA(), StubModuleB(), state_store=store_disabled
    )
    states3 = daemon3.get_module_states()
    assert set(states3.keys()) == {"stub_a", "stub_b"}
    assert states3["stub_a"].enabled is False
    assert states3["stub_b"].enabled is True

    # Self-healing: failure-disabled auto-heals on healthy restart; user-disabled stays disabled
    d3 = tmp_path / "d3"
    d3.mkdir()
    butler_dir3 = _make_butler_toml(d3, modules={"stub_a": {}})
    store_failure = {"module::stub_a::enabled": False, "module::stub_a::disabled_by": "failure"}
    daemon4 = await _init_states(butler_dir3, StubModuleA(), state_store=store_failure)
    assert daemon4.get_module_states()["stub_a"].enabled is True  # heals

    d4 = tmp_path / "d4"
    d4.mkdir()
    butler_dir4 = _make_butler_toml(d4, modules={"stub_a": {}})
    store_user = {"module::stub_a::enabled": False, "module::stub_a::disabled_by": "user"}
    daemon5 = await _init_states(butler_dir4, StubModuleA(), state_store=store_user)
    assert daemon5.get_module_states()["stub_a"].enabled is False  # stays disabled


# ---------------------------------------------------------------------------
# get_module_states()
# ---------------------------------------------------------------------------


async def test_get_module_states_behavior(tmp_path: Path) -> None:
    """Empty before startup; returns copy; failure details included."""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_a": {}})
    assert ButlerDaemon(butler_dir).get_module_states() == {}

    daemon = await _init_states(
        butler_dir, StubModuleA(), state_store={}, failed={"stub_a": "db connection refused"}
    )
    states = daemon.get_module_states()
    states.clear()
    assert len(daemon.get_module_states()) == 1
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
    daemon = await _init_states(butler_dir, StubModuleA(), state_store=store)

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
    (tmp_path / "b").mkdir()
    butler_dir2 = _make_butler_toml(tmp_path / "b", modules={"stub_a": {}})
    daemon2 = await _init_states(
        butler_dir2, StubModuleA(), state_store={}, failed={"stub_a": "startup failed"}
    )

    with pytest.raises(ValueError, match="Unknown module"):
        await daemon2.set_module_enabled("nonexistent", True)

    with pytest.raises(ValueError, match="unavailable"):
        await daemon2.set_module_enabled("stub_a", True)

    daemon2._module_runtime_states["fake_cascade"] = ModuleRuntimeState(
        health="cascade_failed", enabled=False, failure_phase="dependency"
    )
    with pytest.raises(ValueError, match="unavailable"):
        await daemon2.set_module_enabled("fake_cascade", True)
