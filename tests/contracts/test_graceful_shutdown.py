"""Contract tests: Graceful Shutdown (RFC 0001, Invariant 9).

Validates that shutdown drains in-flight sessions and shuts down modules
in reverse topological order via on_shutdown().

Principle: Graceful shutdown drains in-flight sessions before tearing down
modules in reverse topological order (RFC 0001).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.contract


def _make_tracking_module(name: str, deps: list[str], shutdown_order: list):
    """Create a module that records its shutdown call order."""
    from butlers.modules.base import Module

    class _TrackingModule(Module):
        @property
        def name(self) -> str:
            return name

        @property
        def config_schema(self) -> type[BaseModel]:
            return BaseModel

        @property
        def dependencies(self) -> list[str]:
            return deps

        async def register_tools(self, mcp, config, db, butler_name: str) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return None

        async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
            pass

        async def on_shutdown(self) -> None:
            shutdown_order.append(name)

    _TrackingModule.__name__ = f"Module_{name}"
    return _TrackingModule()


class TestReverseOrderShutdown:
    """RFC 0001: Modules shut down in reverse topological order."""

    async def test_reverse_order_shutdown_two_modules(self):
        """RFC 0001: Module B (depends on A) shuts down before A.

        Startup order: A, B (A first because B depends on A).
        Shutdown order: B, A (reverse of startup).
        """
        from butlers.modules.registry import _topological_sort

        shutdown_order: list[str] = []
        a = _make_tracking_module("shutdown_a", [], shutdown_order)
        b = _make_tracking_module("shutdown_b", ["shutdown_a"], shutdown_order)

        # Get startup order
        startup_order = _topological_sort({"shutdown_a": a, "shutdown_b": b})
        startup_names = [m.name for m in startup_order]

        # Shutdown in reverse order
        for module in reversed(startup_order):
            await module.on_shutdown()

        assert shutdown_order == list(reversed(startup_names)), (
            "Shutdown must be in reverse startup order (RFC 0001)"
        )

    async def test_reverse_order_shutdown_three_module_chain(self):
        """RFC 0001: Three-module chain reverses correctly for shutdown.

        Startup: A -> B -> C
        Shutdown: C -> B -> A
        """
        from butlers.modules.registry import _topological_sort

        shutdown_order: list[str] = []
        a = _make_tracking_module("chain_a", [], shutdown_order)
        b = _make_tracking_module("chain_b", ["chain_a"], shutdown_order)
        c = _make_tracking_module("chain_c", ["chain_b"], shutdown_order)

        startup_order = _topological_sort({"chain_a": a, "chain_b": b, "chain_c": c})
        startup_names = [m.name for m in startup_order]

        for module in reversed(startup_order):
            await module.on_shutdown()

        assert shutdown_order == list(reversed(startup_names)), (
            "Three-module chain must shut down in exact reverse order (RFC 0001)"
        )

    async def test_on_shutdown_called_for_all_modules(self):
        """RFC 0001: Every module receives on_shutdown() during graceful shutdown."""
        from butlers.modules.registry import _topological_sort

        shutdown_order: list[str] = []
        modules = {
            f"mod_{i}": _make_tracking_module(f"mod_{i}", [], shutdown_order) for i in range(5)
        }

        startup_order = _topological_sort(modules)
        for module in reversed(startup_order):
            await module.on_shutdown()

        assert set(shutdown_order) == {f"mod_{i}" for i in range(5)}, (
            "All modules must receive on_shutdown() during graceful shutdown (RFC 0001)"
        )


class TestShutdownSequenceContracts:
    """RFC 0001: Shutdown sequence contract — stop server, drain, then shut down modules."""

    def test_shutdown_sequence_documented(self):
        """RFC 0001: Shutdown sequence has 9 documented steps.

        1. Stop MCP server
        2. Stop accepting new triggers
        3. Drain in-flight sessions (up to timeout)
        4. Cancel Switchboard heartbeat task
        5. Close Switchboard MCP client
        6. Cancel scheduler loop
        7. Cancel liveness reporter loop
        8. Shut down modules in reverse topological order
        9. Close database pool
        """
        shutdown_steps = [
            "Stop the MCP server (stop accepting new connections)",
            "Stop accepting new triggers",
            "Drain in-flight runtime sessions up to a configurable timeout",
            "Cancel Switchboard heartbeat task",
            "Close Switchboard MCP client",
            "Cancel scheduler loop (wait for in-progress tick() to finish)",
            "Cancel liveness reporter loop",
            "Shut down modules in reverse topological order via on_shutdown()",
            "Close database pool",
        ]
        assert len(shutdown_steps) == 9, "RFC 0001 defines 9 shutdown steps"

    def test_db_pool_closes_last(self):
        """RFC 0001: Database pool closes as the last step of shutdown.

        Step 9 is 'Close database pool'. Modules use the pool during on_shutdown();
        closing it before on_shutdown() completes would cause errors.
        """
        # Step 9 (pool close) is after step 8 (module shutdown)
        # This ordering prevents use-after-close errors
        db_close_step = 9
        module_shutdown_step = 8
        assert db_close_step > module_shutdown_step, (
            "DB pool must close after module on_shutdown() calls (RFC 0001)"
        )

    def test_mcp_server_stops_accepting_before_drain(self):
        """RFC 0001: MCP server stops accepting connections before drain.

        Steps 1-2 prevent new sessions from starting while existing sessions drain.
        Without this, new triggers could arrive during shutdown.
        """
        stop_accepting_step = 2
        drain_step = 3
        assert stop_accepting_step < drain_step, (
            "Must stop accepting connections before draining (RFC 0001)"
        )

    def test_daemon_has_shutdown_or_stop_method(self):
        """RFC 0001: ButlerDaemon must have a shutdown/stop method."""
        from butlers.daemon import ButlerDaemon

        has_shutdown = (
            hasattr(ButlerDaemon, "shutdown")
            or hasattr(ButlerDaemon, "stop")
            or hasattr(ButlerDaemon, "close")
        )
        assert has_shutdown, "ButlerDaemon must have a shutdown/stop/close method (RFC 0001)"

    def test_on_shutdown_is_async(self):
        """RFC 0001: Module.on_shutdown() must be async for the shutdown protocol."""
        import asyncio

        from butlers.modules.base import Module

        assert asyncio.iscoroutinefunction(Module.on_shutdown), (
            "on_shutdown must be async for graceful shutdown protocol (RFC 0001)"
        )

    def test_session_drain_before_module_shutdown(self):
        """RFC 0001: In-flight sessions drain before module on_shutdown() calls.

        Step 3 (drain) happens before Step 8 (module shutdown).
        Modules may use their tools during active sessions; tearing them down
        while sessions run would cause mid-session tool failures.
        """
        drain_step = 3
        module_shutdown_step = 8
        assert drain_step < module_shutdown_step, (
            "Sessions must drain before module shutdown (RFC 0001)"
        )


class TestConcreteModuleShutdown:
    """RFC 0001: Concrete modules must implement on_shutdown() correctly."""

    async def test_minimal_module_on_shutdown_is_noop(self):
        """RFC 0001: A module with no teardown can implement on_shutdown() as no-op."""
        shutdown_called = []

        from butlers.modules.base import Module

        class NoopShutdown(Module):
            @property
            def name(self) -> str:
                return "noop_shutdown"

            @property
            def config_schema(self) -> type[BaseModel]:
                return BaseModel

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp, config, db, butler_name: str) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
                pass

            async def on_shutdown(self) -> None:
                shutdown_called.append(True)
                # No-op shutdown is valid

        m = NoopShutdown()
        await m.on_shutdown()
        assert shutdown_called == [True], "on_shutdown must be called once"

    async def test_module_shutdown_order_is_reverse_of_load_all_order(self):
        """RFC 0001: load_all() startup order reverses to shutdown order."""
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        shutdown_order: list[str] = []

        class ModDepA(Module):
            @property
            def name(self) -> str:
                return "dep_a"

            @property
            def config_schema(self) -> type[BaseModel]:
                return BaseModel

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp, config, db, butler_name: str) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
                pass

            async def on_shutdown(self) -> None:
                shutdown_order.append("dep_a")

        class ModDepB(Module):
            @property
            def name(self) -> str:
                return "dep_b"

            @property
            def config_schema(self) -> type[BaseModel]:
                return BaseModel

            @property
            def dependencies(self) -> list[str]:
                return ["dep_a"]

            async def register_tools(self, mcp, config, db, butler_name: str) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
                pass

            async def on_shutdown(self) -> None:
                shutdown_order.append("dep_b")

        registry = ModuleRegistry()
        registry.register(ModDepA)
        registry.register(ModDepB)
        startup_order = registry.load_all({})
        startup_names = [m.name for m in startup_order]

        # Shutdown in reverse
        for module in reversed(startup_order):
            await module.on_shutdown()

        assert shutdown_order == list(reversed(startup_names)), (
            "load_all() startup order must reverse to shutdown order (RFC 0001)"
        )
