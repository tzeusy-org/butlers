"""Contract tests: Module Boundaries (RFC 0002, Vision Rule 2, Invariant 6).

Validates that modules ONLY add tools and NEVER touch core infrastructure.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.contract


def _make_minimal_module(name: str = "test_module"):
    from butlers.modules.base import Module

    class _MinimalModule(Module):
        @property
        def name(self) -> str:
            return name

        @property
        def config_schema(self) -> type[BaseModel]:
            return BaseModel

        @property
        def dependencies(self) -> list[str]:
            return []

        async def register_tools(self, mcp, config, db) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return None

        async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
            pass

        async def on_shutdown(self) -> None:
            pass

    _MinimalModule.__name__ = f"Module_{name}"
    return _MinimalModule()


class TestModuleAbcStructure:
    """RFC 0002: Module ABC defines the correct interface for tool-only extension."""

    def test_abc_defines_required_abstract_methods(self):
        from butlers.modules.base import Module

        required = {
            "register_tools",
            "migration_revisions",
            "on_startup",
            "on_shutdown",
            "name",
            "config_schema",
            "dependencies",
        }
        assert required.issubset(Module.__abstractmethods__)

    def test_module_abc_has_no_core_infrastructure_access(self):
        """RFC 0002: Module ABC does not expose core infrastructure to subclasses.

        Behavioral assertion: the Module abstract class does not define any
        attributes or methods for scheduler, spawner, session_log, or state_store.
        These are never injected into modules — only mcp, config, and db are
        passed via register_tools() and on_startup().
        """
        from butlers.modules.base import Module

        # Module ABC must not define core infrastructure as attributes or methods
        for forbidden in ["scheduler", "spawner", "session_log", "state_store"]:
            assert not hasattr(Module, forbidden), (
                f"Module ABC must not define '{forbidden}' — modules never access core infra"
            )

        # register_tools return annotation is None (no infrastructure returned)
        sig = inspect.signature(Module.register_tools)
        assert sig.return_annotation is None or sig.return_annotation == "None"

        # on_startup receives only own-butler resources
        startup_params = list(inspect.signature(Module.on_startup).parameters.keys())
        assert "config" in startup_params and "db" in startup_params


class TestConcreteModuleBoundaries:
    """RFC 0002: Concrete modules respect the no-core-infrastructure rule."""

    def test_concrete_module_structural_properties(self):
        """Modules are async, return str|None for migrations, don't expose infra."""
        mod = _make_minimal_module()
        assert asyncio.iscoroutinefunction(mod.on_shutdown)
        assert asyncio.iscoroutinefunction(mod.on_startup)
        assert asyncio.iscoroutinefunction(mod.register_tools)
        assert mod.migration_revisions() is None or isinstance(mod.migration_revisions(), str)

    def test_email_and_telegram_do_not_expose_core_infra(self):
        """RFC 0002: EmailModule and TelegramModule instances have no core infra attributes.

        Behavioral assertion: instantiated modules do not carry scheduler,
        spawner, session_log, or state_store as instance attributes. The daemon
        never injects these — modules only receive mcp, config, and db.
        """
        from butlers.modules.email import EmailModule
        from butlers.modules.telegram import TelegramModule

        for mod_cls in [EmailModule, TelegramModule]:
            mod = mod_cls()
            # Core infrastructure must never be injected into or held by modules
            assert not hasattr(mod, "_scheduler"), (
                f"{mod_cls.__name__} must not hold a scheduler reference (RFC 0002)"
            )
            assert not hasattr(mod, "scheduler"), (
                f"{mod_cls.__name__} must not expose scheduler (RFC 0002)"
            )
            assert not hasattr(mod, "session_log"), (
                f"{mod_cls.__name__} must not hold a session_log reference (RFC 0002)"
            )
            assert not hasattr(mod, "spawner"), (
                f"{mod_cls.__name__} must not hold a spawner reference (RFC 0002)"
            )
            assert not hasattr(mod, "state_store"), (
                f"{mod_cls.__name__} must not hold a state_store reference (RFC 0002)"
            )

    def test_tool_metadata_does_not_expose_session_log(self):
        mod = _make_minimal_module()
        meta = mod.tool_metadata()
        assert isinstance(meta, dict)
        for tool_name, tm in meta.items():
            assert "session_log" not in str(tm.arg_sensitivities)
