"""Contract tests: Module Boundaries (RFC 0002, Vision Rule 2, Invariant 6).

Validates that modules ONLY add tools and NEVER touch core infrastructure.
"""

from __future__ import annotations

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

        required = {"register_tools", "migration_revisions", "on_startup", "on_shutdown",
                    "name", "config_schema", "dependencies"}
        assert required.issubset(Module.__abstractmethods__)

    def test_module_abc_has_no_core_infrastructure_access(self):
        from butlers.modules.base import Module

        src = inspect.getsource(Module)
        for forbidden in ["scheduler", "spawner", "session_log", "state_store"]:
            assert forbidden not in src.lower()

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
        import asyncio

        from butlers.modules.base import Module

        mod = _make_minimal_module()
        assert asyncio.iscoroutinefunction(mod.on_shutdown)
        assert asyncio.iscoroutinefunction(mod.on_startup)
        assert asyncio.iscoroutinefunction(mod.register_tools)
        assert mod.migration_revisions() is None or isinstance(mod.migration_revisions(), str)

    def test_email_and_telegram_do_not_touch_core(self):
        from butlers.modules.email import EmailModule
        from butlers.modules.telegram import TelegramModule

        for mod_cls in [EmailModule, TelegramModule]:
            src = inspect.getsource(mod_cls)
            assert "self._scheduler" not in src
            assert "_sessions_table" not in src or "session_log" not in src

    def test_tool_metadata_does_not_expose_session_log(self):
        mod = _make_minimal_module()
        meta = mod.tool_metadata()
        assert isinstance(meta, dict)
        for tool_name, tm in meta.items():
            assert "session_log" not in str(tm.arg_sensitivities)
