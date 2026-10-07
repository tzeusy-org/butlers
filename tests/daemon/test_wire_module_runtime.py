"""Tests for ButlerDaemon._wire_module_runtime() — AC coverage for bu-i0geq.

Covers:
- SelfHealingModule._switchboard_client is not None after startup when
  switchboard is configured (AC 1)
- QaModule._switchboard_client is not None after startup when switchboard
  is configured (AC 2)
- _try_qa_relay() reaches Switchboard route() call (AC 3)
- Graceful degradation when switchboard is not configured (AC 4)
- wire_runtime() called on modules that define it; modules without it are
  silently skipped (generic behavior)
- wire_runtime() failure is non-fatal; butler startup continues (non-fatal)
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path, modules: dict | None = None) -> Path:
    """Write a minimal butler.toml and return the directory."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 18999",
        'description = "Wire runtime test butler"',
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
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {v!r}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    """Return patches dict for all infrastructure dependencies."""
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
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
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
    }


class _StubConfig(BaseModel):
    pass


class StubModuleCapturingButlerName(Module):
    """Stub module that captures butler_name from register_tools()."""

    def __init__(self) -> None:
        self.received_butler_name: str = ""

    @property
    def name(self) -> str:
        return "stub_capture_name"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self.received_butler_name = butler_name

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class StubModuleWithWireRuntime(Module):
    """Stub module that implements wire_runtime() for inspection."""

    def __init__(self) -> None:
        self.wire_runtime_called = False
        self.wire_runtime_args: tuple = ()
        self.wire_runtime_kwargs: dict = {}

    @property
    def name(self) -> str:
        return "stub_wire"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        pass

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def wire_runtime(self, *args, **kwargs) -> None:
        self.wire_runtime_called = True
        self.wire_runtime_args = args
        self.wire_runtime_kwargs = kwargs


class StubModuleWithoutWireRuntime(Module):
    """Stub module that does NOT implement wire_runtime()."""

    @property
    def name(self) -> str:
        return "stub_no_wire"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        pass

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class StubModuleWithBrokenWireRuntime(Module):
    """Stub module whose wire_runtime() raises an exception."""

    @property
    def name(self) -> str:
        return "stub_broken_wire"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        pass

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def wire_runtime(self, *args, **kwargs) -> None:
        raise RuntimeError("wire_runtime intentionally broken")


# ---------------------------------------------------------------------------
# Tests: daemon passes butler_name to register_tools
# ---------------------------------------------------------------------------


async def test_daemon_passes_butler_name_to_register_tools(tmp_path: Path) -> None:
    """Daemon passes self.config.name as butler_name to register_tools(). REQ-core-modules-002"""
    registry = ModuleRegistry()
    registry.register(StubModuleCapturingButlerName)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_capture_name": {}})
    patches = _patch_infra()

    with (
        patches["db_from_env"] as db_factory,
        patches["run_migrations"] as migrations,
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch.object(ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock),
    ):
        # Exercise production load_all + run_startup selection, before DB work.
        toml = butler_dir / "butler.toml"
        valid_toml = toml.read_text()
        toml.write_text(valid_toml + "\n[modules.unknown_startup_sentinel]\n")
        rejected = ButlerDaemon(butler_dir, registry=registry)
        with pytest.raises(ValueError, match="unknown_startup_sentinel"):
            await rejected.start()
        db_factory.assert_not_called()
        migrations.assert_not_awaited()
        assert not rejected._active_modules
        assert not any(m.received_butler_name for m in rejected._registry.load_all({}))

        toml.write_text(valid_toml)
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()
        db_factory.assert_called()
        migrations.assert_awaited()

    stub_mod = next(m for m in daemon._modules if m.name == "stub_capture_name")
    # Daemon must pass self.config.name ("test-butler" from the toml) as butler_name
    assert stub_mod.received_butler_name == "test-butler"
    # Whole-roster production discovery/selection/lifecycle. External provider
    # hooks are synthetic; SelfHealingModule hooks and registration execute.
    from fastmcp import FastMCP

    from butlers.config import load_config
    from butlers.modules.registry import default_registry
    from butlers.modules.self_healing import SelfHealingModule

    class RegisteredBoundary(Exception):
        pass

    roster_root = Path(__file__).resolve().parents[2] / "roster"
    rosters = sorted(roster_root.glob("*/butler.toml"))
    assert len(rosters) == 13
    for toml_path in rosters:
        config = load_config(toml_path.parent)
        assert "self_healing" in config.modules
        registry = default_registry()
        discovered = registry.load_all(config.modules)
        started, registered = [], []
        patches = _patch_infra()
        with ExitStack() as stack:
            for key, factory in patches.items():
                if not key.startswith("mock_") and key != "FastMCP":
                    stack.enter_context(factory)
            stack.enter_context(patch("butlers.lifecycle.FastMCP", FastMCP))
            stack.enter_context(patch("butlers.cli_auth.persistence.restore_tokens", AsyncMock()))
            stack.enter_context(patch("butlers.lifecycle._ensure_owner_entity", AsyncMock()))
            stack.enter_context(patch.object(ButlerDaemon, "_connect_switchboard", AsyncMock()))
            stack.enter_context(patch.object(ButlerDaemon, "_register_core_tools"))
            # Provider tool bodies are synthetic; their egress approval config is not this assertion seam.
            stack.enter_context(
                patch.object(ButlerDaemon, "_apply_approval_gates", AsyncMock(return_value={}))
            )
            stack.enter_context(
                patch.object(
                    ButlerDaemon, "_start_mcp_server", AsyncMock(side_effect=RegisteredBoundary)
                )
            )
            for module in discovered:
                original_start = type(module).on_startup
                original_register = type(module).register_tools

                async def start_hook(self, *args, _original=original_start, **kwargs):
                    started.append(self.name)
                    if isinstance(self, SelfHealingModule):
                        await _original(self, *args, **kwargs)

                async def registration(self, *args, _original=original_register, **kwargs):
                    registered.append(self.name)
                    if isinstance(self, SelfHealingModule):
                        await _original(self, *args, **kwargs)

                stack.enter_context(patch.object(type(module), "on_startup", start_hook))
                stack.enter_context(patch.object(type(module), "register_tools", registration))
                if not isinstance(module, SelfHealingModule):
                    stack.enter_context(patch.object(type(module), "on_shutdown", AsyncMock()))
            daemon = ButlerDaemon(tmp_path / config.name, registry=registry)
            daemon.config = config
            with pytest.raises(RegisteredBoundary):
                await daemon.start()
            expected = [m.name for m in discovered if m.name in config.modules]
            assert started == expected, (config.name, daemon._module_statuses)
            assert registered == expected, (config.name, daemon._module_statuses)
            assert {t.name for t in (await daemon.mcp.list_tools())} == {
                "report_error",
                "get_healing_status",
            }
            healing = next(m for m in daemon._modules if m.name == "self_healing")
            assert healing._butler_name == config.name
            assert healing._pool is patches["mock_pool"]


# ---------------------------------------------------------------------------
# Tests: _wire_module_runtime generic behaviour
# ---------------------------------------------------------------------------


async def test_wire_runtime_called_with_switchboard_client(tmp_path: Path) -> None:
    """wire_runtime() receives switchboard_client when Switchboard is connected."""
    registry = ModuleRegistry()
    registry.register(StubModuleWithWireRuntime)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_wire": {}})
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch("butlers.switchboard_wiring.MCPClient", return_value=mock_client),
    ):
        # Inject switchboard_url so _connect_switchboard is attempted
        daemon = ButlerDaemon(butler_dir, registry=registry)
        daemon.config = None  # Force reload so toml is re-read
        await daemon.start()

    stub_mod = next(m for m in daemon._modules if m.name == "stub_wire")
    assert stub_mod.wire_runtime_called, "wire_runtime was not called"
    assert stub_mod.wire_runtime_kwargs.get("switchboard_client") is not None


async def test_wire_runtime_graceful_when_no_switchboard(tmp_path: Path) -> None:
    """wire_runtime() receives switchboard_client=None when Switchboard not configured (AC 4)."""
    registry = ModuleRegistry()
    registry.register(StubModuleWithWireRuntime)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_wire": {}})
    patches = _patch_infra()

    # Simulate connect failure — switchboard_client stays None
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(side_effect=ConnectionError("refused"))

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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch("butlers.switchboard_wiring.MCPClient", return_value=mock_client),
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    # Butler started despite connect failure
    assert daemon._started_at is not None

    stub_mod = next(m for m in daemon._modules if m.name == "stub_wire")
    assert stub_mod.wire_runtime_called, "wire_runtime was not called"
    # Graceful degradation: client is None but wire_runtime was still called
    assert stub_mod.wire_runtime_kwargs.get("switchboard_client") is None


async def test_wire_runtime_skipped_for_modules_without_method(tmp_path: Path) -> None:
    """Modules that do not define wire_runtime() are silently skipped."""
    registry = ModuleRegistry()
    registry.register(StubModuleWithoutWireRuntime)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_no_wire": {}})
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch.object(ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock),
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    assert daemon._started_at is not None  # Butler started fine


async def test_wire_runtime_failure_is_non_fatal(tmp_path: Path) -> None:
    """wire_runtime() failure is logged but does not abort butler startup."""
    registry = ModuleRegistry()
    registry.register(StubModuleWithBrokenWireRuntime)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_broken_wire": {}})
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch.object(ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock),
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    assert daemon._started_at is not None  # Butler started despite wire_runtime failure


# ---------------------------------------------------------------------------
# Tests: SelfHealingModule wiring (AC 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_import, module_key",
    [
        ("butlers.modules.self_healing:SelfHealingModule", "self_healing"),  # AC 1
        ("butlers.modules.qa:QaModule", "qa"),  # AC 2
    ],
)
async def test_concrete_module_switchboard_client_wired_on_startup(
    tmp_path: Path, module_import: str, module_key: str
) -> None:
    """Concrete modules (self_healing, qa) receive _switchboard_client after startup."""
    import importlib

    mod_path, cls_name = module_import.split(":")
    module_cls = getattr(importlib.import_module(mod_path), cls_name)

    registry = ModuleRegistry()
    registry.register(module_cls)

    butler_dir = _make_butler_toml(tmp_path, modules={module_key: {}})
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
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patch("butlers.switchboard_wiring.MCPClient", return_value=mock_client),
    ):
        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    mod = next(m for m in daemon._modules if m.name == module_key)
    # switchboard_client is not None after startup (real-module wiring guard).
    assert mod._switchboard_client is not None


# ---------------------------------------------------------------------------
# Tests: _try_qa_relay reaches Switchboard route() (AC 3)
# ---------------------------------------------------------------------------


async def test_try_qa_relay_reaches_switchboard_route() -> None:
    """_try_qa_relay() reaches Switchboard route() when client is wired (AC 3).

    This test exercises the SelfHealingModule relay path end-to-end, confirming
    that with a real switchboard_client the route() tool is actually invoked
    (not short-circuited by an early None return).
    """
    from butlers.modules.self_healing import SelfHealingModule

    route_calls: list[dict] = []

    async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
        if tool_name == "list_butlers":
            return [{"name": "qa"}]
        if tool_name == "route":
            route_calls.append(args or {})
            return {"accepted": True}
        return {}

    client = MagicMock()
    client.call_tool = mock_call_tool

    mod = SelfHealingModule()
    mod.wire_runtime(MagicMock(), "/repo", switchboard_client=client)
    mod._pool = None  # No DB in this unit test

    result = await mod._handle_report_error(
        error_type="ValueError",
        error_message="something broke",
        traceback_str=None,
        call_site="module.py:func",
        context="agent context",
        tool_name=None,
        severity_hint="high",
    )

    # AC 3: route() was actually called — relay reached Switchboard
    assert len(route_calls) == 1, f"Expected 1 route() call, got {len(route_calls)}"
    assert route_calls[0]["target_butler"] == "qa"
    assert route_calls[0]["tool_name"] == "report_finding"
    assert result["accepted"] is True


# ---------------------------------------------------------------------------
# Tests: QaModule wire_runtime() accepts switchboard_client kwarg
# ---------------------------------------------------------------------------


def test_qa_wire_runtime_stores_switchboard_client() -> None:
    """QaModule.wire_runtime() stores the switchboard_client (AC 2 unit)."""
    from butlers.modules.qa import QaModule

    mod = QaModule()
    client = MagicMock()
    mod.wire_runtime(MagicMock(), "/repo", switchboard_client=client)

    assert mod._switchboard_client is client


def test_qa_wire_runtime_without_switchboard_client() -> None:
    """QaModule.wire_runtime() defaults switchboard_client to None (graceful degradation)."""
    from butlers.modules.qa import QaModule

    mod = QaModule()
    mod.wire_runtime(MagicMock(), "/repo")

    assert mod._switchboard_client is None
