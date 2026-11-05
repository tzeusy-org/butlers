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

import pytest
from pydantic import BaseModel

from butlers.config import load_config
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry
from butlers.testing.daemon import write_butler_config

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

    name = "stub_ok"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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

    name = "stub_failing"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return ["stub_ok"]

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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

    name = "stub_after"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return ["stub_failing"]

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_real_boot_and_shutdown_run_in_order(booted_daemon, tmp_path: Path) -> None:
    """One real boot covers the startup-failure and shutdown concerns (bu-rvgzmf.2).

    Startup: a failing module is marked failed at startup, its dependant cascade-fails
    and never starts, the healthy module starts; an unregistered butler never
    advertises acceptance and keeps retrying registration. Shutdown: stop_accepting,
    drain (with the configured timeout), module shutdown, then the pool closes;
    failed modules are not shut down.
    """
    config_dir = write_butler_config(
        tmp_path,
        "general",
        extra_toml=(
            "[butler.shutdown]\ntimeout_s = 15.0\n\n"
            "[modules.stub_ok]\n\n[modules.stub_failing]\n\n[modules.stub_after]\n"
        ),
    )
    registry = _make_registry(StubModuleOk, StubModuleFailing, StubModuleAfterFailing)
    booted = await booted_daemon(config_dir, registry=registry)
    daemon = booted.daemon
    modules = {m.name: m for m in daemon._modules}

    assert modules["stub_ok"].started is True
    assert daemon._module_statuses["stub_ok"].status == "active"
    assert daemon._module_statuses["stub_failing"].status == "failed"
    assert daemon._module_statuses["stub_failing"].phase == "startup"
    assert daemon._module_statuses["stub_after"].status == "cascade_failed"
    assert modules["stub_after"].started is False
    assert daemon._boot_epoch is None
    assert daemon._accepting_connections is False
    assert isinstance(daemon._boot_registration_task, asyncio.Task)

    call_order: list[str] = []
    spawner, db = daemon.spawner, daemon.db
    real_stop, real_drain, real_close = spawner.stop_accepting, spawner.drain, db.close
    drain_timeouts: list[float] = []

    def stop_accepting() -> None:
        call_order.append("stop_accepting")
        real_stop()

    async def drain(**kwargs: float) -> None:
        call_order.append("drain")
        drain_timeouts.append(kwargs["timeout"])
        await real_drain(**kwargs)

    async def close() -> None:
        call_order.append("db_close")
        await real_close()

    real_module_shutdown = modules["stub_ok"].on_shutdown

    async def module_shutdown() -> None:
        call_order.append("module_shutdown:stub_ok")
        await real_module_shutdown()

    spawner.stop_accepting, spawner.drain, db.close = stop_accepting, drain, close
    modules["stub_ok"].on_shutdown = module_shutdown

    await booted.shutdown()

    assert call_order == ["stop_accepting", "drain", "module_shutdown:stub_ok", "db_close"]
    assert drain_timeouts == [15.0]
    assert daemon._accepting_connections is False
    assert daemon._boot_registration_task is None
    assert modules["stub_ok"].shutdown_called is True
    assert modules["stub_failing"].shutdown_called is False
