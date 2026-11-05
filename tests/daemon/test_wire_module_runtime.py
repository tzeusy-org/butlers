"""Tests for ButlerDaemon._wire_module_runtime() — AC coverage for bu-i0geq.

Covers:
- Core QA relay resolves the current daemon Switchboard client (AC 1)
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
from butlers.testing.fake_pool import fake_pool
from butlers.testing.tool_host import host_tools

_THIS_FILE = Path(__file__).resolve()

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


class _StubConfig(BaseModel):
    pass


class StubModuleCapturingButlerName(Module):
    """Stub module that captures butler_name from register_tools()."""

    def __init__(self) -> None:
        self.received_butler_name: str = ""

    name = "stub_capture_name"

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

    name = "stub_wire"

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

    name = "stub_no_wire"

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

    name = "stub_broken_wire"

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


async def _module_host(butler_dir: Path, registry: ModuleRegistry) -> ButlerDaemon:
    """Select and register *butler_dir*'s modules with the daemon's own phases, no boot.

    The phases run on a DB-free host: module selection, then tool registration
    (bu-rvgzmf.2). Callers run ``_wire_module_runtime`` themselves.
    """
    import butlers.lifecycle as lifecycle

    daemon = (await host_tools(butler_dir, pool=fake_pool())).daemon
    daemon._registry = registry
    state = lifecycle.StartupState()
    await lifecycle._startup_modules(daemon, state)
    await lifecycle._startup_module_config(daemon, state)
    await daemon._register_module_tools()
    daemon.spawner = MagicMock()
    return daemon


def _registry(*module_classes: type[Module]) -> ModuleRegistry:
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_real_boot_wires_runtime_without_a_switchboard(
    booted_daemon, tmp_path: Path
) -> None:
    """The real startup calls wire_runtime() with no client when no Switchboard is set (AC 4)."""
    from butlers.testing.daemon import write_butler_config

    config_dir = write_butler_config(tmp_path, "general", extra_toml="[modules.stub_wire]\n")
    booted = await booted_daemon(config_dir, registry=_registry(StubModuleWithWireRuntime))
    stub_mod = next(m for m in booted.daemon._modules if m.name == "stub_wire")

    assert booted.daemon._started_at is not None
    assert stub_mod.wire_runtime_called, "wire_runtime was not called"
    assert stub_mod.wire_runtime_kwargs.get("switchboard_client") is None


async def test_declared_only_loading_rejects_bad_declarations_before_database_work(
    tmp_path: Path,
) -> None:
    """Unknown names, undeclared dependencies and cycles fail module selection.

    Selection runs before the database phase, so a rejected config does no DB work,
    and no module outside the declared set is ever constructed.
    """
    import butlers.lifecycle as lifecycle
    from butlers.config import load_config

    omitted_constructions = []

    class OmittedNoSchema(StubModuleCapturingButlerName):
        name = "omitted_no_schema"

        def __init__(self):
            omitted_constructions.append(self.name)
            super().__init__()

        @property
        def config_schema(self):
            return None

    class NeedsDeclaration(StubModuleCapturingButlerName):
        name = "needs_declaration"

        @property
        def dependencies(self):
            return ["stub_capture_name"]

    class CycleA(StubModuleCapturingButlerName):
        name = "cycle_a"

        @property
        def dependencies(self):
            return ["cycle_b"]

    class CycleB(StubModuleCapturingButlerName):
        name = "cycle_b"

        @property
        def dependencies(self):
            return ["cycle_a"]

    registry = _registry(
        StubModuleCapturingButlerName, OmittedNoSchema, NeedsDeclaration, CycleA, CycleB
    )
    phases = lifecycle.STARTUP_PHASES
    assert phases.index(lifecycle._startup_modules) < phases.index(lifecycle._startup_database)

    butler_dir = _make_butler_toml(tmp_path, modules={"stub_capture_name": {}})
    toml = butler_dir / "butler.toml"
    prefix = toml.read_text().split("[modules.stub_capture_name]")[0]
    for declarations, error in (
        ("[modules.unknown_startup_sentinel]", "unknown_startup_sentinel"),
        ("[modules.needs_declaration]", "stub_capture_name.*enabled module set"),
        ("[modules.cycle_a]\n[modules.cycle_b]", "Circular dependency"),
    ):
        toml.write_text(prefix + declarations + "\n")
        rejected = ButlerDaemon(butler_dir, registry=registry)
        rejected.config = load_config(butler_dir)
        with pytest.raises(ValueError, match=error):
            await lifecycle._startup_modules(rejected, lifecycle.StartupState())
        assert not rejected._active_modules
        assert omitted_constructions == []
    # The explicit load_all utility still constructs everything; test its
    # no-registration-effect contract without introducing a deliberate cycle.
    utility = _registry(StubModuleCapturingButlerName)
    assert not any(m.received_butler_name for m in utility.load_all({}))


async def test_daemon_passes_butler_name_to_register_tools(tmp_path: Path) -> None:
    """Daemon passes self.config.name as butler_name to register_tools(). REQ-core-modules-002"""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_capture_name": {}})
    daemon = await _module_host(butler_dir, _registry(StubModuleCapturingButlerName))

    stub_mod = next(m for m in daemon._modules if m.name == "stub_capture_name")
    assert stub_mod.received_butler_name == "test-butler"


async def test_every_roster_butler_starts_and_registers_exactly_its_declared_modules() -> None:
    """Whole-roster discovery and selection: each butler starts and registers its modules.

    Provider hooks are synthetic; the daemon's own selection, config, startup and
    registration phases run on a DB-free host, with core tools registered for real.
    """
    import butlers.lifecycle as lifecycle
    from butlers.config import load_config
    from butlers.modules.registry import default_registry

    roster_root = _THIS_FILE.parents[2] / "roster"
    rosters = sorted(roster_root.glob("*/butler.toml"))
    assert len(rosters) == 13
    for toml_path in rosters:
        config = load_config(toml_path.parent)
        assert "self_healing" not in config.modules
        registry = default_registry()
        discovered = registry.load_from_config(config.modules)
        started, registered = [], []
        with ExitStack() as stack:
            for module in discovered:

                async def start_hook(self, *args, started=started, **kwargs):
                    started.append(self.name)

                async def registration(self, *args, registered=registered, **kwargs):
                    registered.append(self.name)

                stack.enter_context(patch.object(type(module), "on_startup", start_hook))
                stack.enter_context(patch.object(type(module), "register_tools", registration))
            daemon = (await host_tools(toml_path.parent, pool=fake_pool())).daemon
            daemon._registry = registry
            state = lifecycle.StartupState()
            await lifecycle._startup_modules(daemon, state)
            await lifecycle._startup_module_config(daemon, state)
            await lifecycle._startup_module_startup(daemon, state)
            await daemon._register_module_tools()
        expected = [m.name for m in discovered if m.name in config.modules]
        assert started == expected, (config.name, daemon._module_statuses)
        assert registered == expected, (config.name, daemon._module_statuses)
        assert {t.name for t in (await daemon.mcp.list_tools())} >= {
            "report_error",
            "get_healing_status",
        }
        assert daemon._tool_module_map["report_error"] == "core"


# ---------------------------------------------------------------------------
# Tests: _wire_module_runtime generic behaviour
# ---------------------------------------------------------------------------


async def test_wire_runtime_called_with_switchboard_client(tmp_path: Path) -> None:
    """wire_runtime() receives the daemon's switchboard_client when one is connected."""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_wire": {}})
    daemon = await _module_host(butler_dir, _registry(StubModuleWithWireRuntime))
    daemon.switchboard_client = AsyncMock()

    daemon._wire_module_runtime()

    stub_mod = next(m for m in daemon._modules if m.name == "stub_wire")
    assert stub_mod.wire_runtime_called, "wire_runtime was not called"
    assert stub_mod.wire_runtime_kwargs.get("switchboard_client") is daemon.switchboard_client


async def test_wire_runtime_skipped_for_modules_without_method(tmp_path: Path) -> None:
    """Modules that do not define wire_runtime() are silently skipped."""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_no_wire": {}})
    daemon = await _module_host(butler_dir, _registry(StubModuleWithoutWireRuntime))

    daemon._wire_module_runtime()  # must not raise


async def test_wire_runtime_failure_is_non_fatal(tmp_path: Path) -> None:
    """wire_runtime() failure is logged but does not abort the phase."""
    butler_dir = _make_butler_toml(tmp_path, modules={"stub_broken_wire": {}})
    daemon = await _module_host(butler_dir, _registry(StubModuleWithBrokenWireRuntime))

    daemon._wire_module_runtime()  # must not raise


# ---------------------------------------------------------------------------
# Tests: Core QA relay wiring (AC 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_import, module_key",
    [
        ("butlers.modules.qa:QaModule", "qa"),  # AC 2
    ],
)
async def test_concrete_module_switchboard_client_wired_on_startup(
    tmp_path: Path, module_import: str, module_key: str
) -> None:
    """Concrete modules (qa) receive _switchboard_client from the wiring phase."""
    import importlib

    mod_path, cls_name = module_import.split(":")
    module_cls = getattr(importlib.import_module(mod_path), cls_name)

    butler_dir = _make_butler_toml(tmp_path, modules={module_key: {}})
    daemon = await _module_host(butler_dir, _registry(module_cls))
    daemon.switchboard_client = AsyncMock()

    daemon._wire_module_runtime()

    mod = next(m for m in daemon._modules if m.name == module_key)
    assert mod._switchboard_client is not None


# ---------------------------------------------------------------------------
# Tests: _try_qa_relay reaches Switchboard route() (AC 3)
# ---------------------------------------------------------------------------


async def test_try_qa_relay_reaches_switchboard_route() -> None:
    """_try_qa_relay() reaches Switchboard route() when client is wired (AC 3).

    This test exercises the core QA relay path end-to-end, confirming
    that with a real switchboard_client the route() tool is actually invoked
    (not short-circuited by an early None return).
    """
    from butlers.core.qa.relay import QaRelay

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

    mod = QaRelay("test-butler", None, lambda: client)
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


def test_wire_runtime_getter_follows_switchboard_client_replacement(tmp_path: Path) -> None:
    """A module that accepts a getter sees heartbeat reconnects without re-wiring."""
    captured: dict[str, Any] = {}

    class GetterModule:
        name = "getter_mod"

        def wire_runtime(
            self,
            spawner,
            repo_root,
            *,
            switchboard_client=None,
            switchboard_client_getter=None,
        ) -> None:
            captured["static"] = switchboard_client
            captured["getter"] = switchboard_client_getter

    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.spawner = MagicMock()
    daemon.config_dir = tmp_path
    daemon.config = MagicMock()
    daemon.config.name = "messenger"
    daemon._modules = [GetterModule()]
    daemon._module_statuses = {}
    daemon.switchboard_client = None
    with patch("butlers.core.spawn_hooks.register_spawner"):
        daemon._wire_module_runtime()

    assert captured["static"] is None
    getter = captured["getter"]
    assert getter() is None
    replacement = object()
    daemon.switchboard_client = replacement
    assert getter() is replacement
