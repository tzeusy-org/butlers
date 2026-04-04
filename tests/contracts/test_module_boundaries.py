"""Contract tests: Module Boundaries (RFC 0002, Vision Rule 2, Invariant 6).

Validates that modules ONLY add tools and NEVER touch core infrastructure:
the scheduler, spawner, session log, or state store.

Principle: Modules only add tools — they never touch core infrastructure.
A module registers MCP tools, declares database migrations, and hooks into
the daemon lifecycle. It must not modify the state store, the scheduler,
the spawner, or the session log (Vision.md Rule 2, RFC 0002).
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.contract


def _make_minimal_module(name: str = "test_module"):
    """Create a minimal concrete Module for boundary testing."""
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
            pass  # Only adds tools — does not touch core

        def migration_revisions(self) -> str | None:
            return None

        async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
            pass  # Sets up module state only

        async def on_shutdown(self) -> None:
            pass  # Tears down module state only

    _MinimalModule.__name__ = f"Module_{name}"
    return _MinimalModule()


class TestModuleAbcStructure:
    """RFC 0002: Module ABC defines the correct interface for tool-only extension."""

    def test_module_abc_defines_register_tools(self):
        """RFC 0002: Module must implement register_tools() to add MCP tools."""
        from butlers.modules.base import Module

        assert "register_tools" in Module.__abstractmethods__, (
            "register_tools must be an abstract method on Module (RFC 0002)"
        )

    def test_module_abc_defines_migration_revisions(self):
        """RFC 0002: Module must implement migration_revisions() for DB schema."""
        from butlers.modules.base import Module

        assert "migration_revisions" in Module.__abstractmethods__, (
            "migration_revisions must be abstract on Module (RFC 0002)"
        )

    def test_module_abc_defines_on_startup(self):
        """RFC 0002: Module must implement on_startup() for initialization."""
        from butlers.modules.base import Module

        assert "on_startup" in Module.__abstractmethods__, (
            "on_startup must be abstract on Module (RFC 0002)"
        )

    def test_module_abc_defines_on_shutdown(self):
        """RFC 0002: Module must implement on_shutdown() for cleanup."""
        from butlers.modules.base import Module

        assert "on_shutdown" in Module.__abstractmethods__, (
            "on_shutdown must be abstract on Module (RFC 0002)"
        )

    def test_module_abc_defines_name_property(self):
        """RFC 0002: Module must declare a unique name property."""
        from butlers.modules.base import Module

        assert "name" in Module.__abstractmethods__, (
            "name must be abstract property on Module (RFC 0002)"
        )

    def test_module_abc_defines_config_schema(self):
        """RFC 0002: Module must declare a Pydantic config schema."""
        from butlers.modules.base import Module

        assert "config_schema" in Module.__abstractmethods__, (
            "config_schema must be abstract on Module (RFC 0002)"
        )

    def test_module_abc_defines_dependencies(self):
        """RFC 0002: Module must declare its dependencies for topological sort."""
        from butlers.modules.base import Module

        assert "dependencies" in Module.__abstractmethods__, (
            "dependencies must be abstract on Module (RFC 0002)"
        )

    def test_module_abc_has_no_scheduler_access(self):
        """Vision Rule 2: Module ABC must not expose scheduler access.

        The scheduler is core infrastructure. Modules must not be able to
        modify cron schedules, trigger tasks, or inspect the scheduler state
        via the Module ABC interface.
        """
        from butlers.modules.base import Module

        src = inspect.getsource(Module)
        scheduler_keywords = ["scheduler", "cron", "scheduled_tasks", "tick_interval"]
        for kw in scheduler_keywords:
            # The ABC itself must not manipulate the scheduler
            assert f"self.{kw}" not in src.lower(), (
                f"Module ABC must not access scheduler attribute '{kw}' (Vision Rule 2)"
            )

    def test_module_abc_has_no_spawner_access(self):
        """Vision Rule 2: Module ABC must not expose spawner access.

        The spawner launches LLM sessions — core infrastructure. Modules
        cannot trigger sessions or modify concurrency controls.
        """
        from butlers.modules.base import Module

        src = inspect.getsource(Module)
        spawner_keywords = ["spawner", "spawn_session", "llm_session", "semaphore"]
        for kw in spawner_keywords:
            assert kw.lower() not in src.lower(), (
                f"Module ABC must not access spawner concept '{kw}' (Vision Rule 2)"
            )

    def test_module_abc_has_no_session_log_access(self):
        """Vision Rule 2: Module ABC must not expose session log access.

        Session logging is core infrastructure. Modules cannot write to the
        sessions table directly or inspect session history.
        """
        from butlers.modules.base import Module

        src = inspect.getsource(Module)
        session_log_keywords = ["session_log", "log_session", "sessions_table"]
        for kw in session_log_keywords:
            assert kw.lower() not in src.lower(), (
                f"Module ABC must not access session log '{kw}' (Vision Rule 2)"
            )

    def test_register_tools_does_not_return_infrastructure(self):
        """Vision Rule 2: register_tools() returns None — no infrastructure objects leaked.

        register_tools() only adds tools to the MCP server. It must not
        return core infrastructure objects (scheduler, spawner, etc.).
        """
        from butlers.modules.base import Module

        sig = inspect.signature(Module.register_tools)
        # Return annotation should be None or not annotated with infrastructure types
        return_annotation = sig.return_annotation
        if return_annotation != inspect.Parameter.empty:
            assert return_annotation is type(None) or str(return_annotation) == "None", (
                "register_tools must return None (Vision Rule 2)"
            )

    def test_on_startup_receives_only_own_butler_resources(self):
        """Vision Rule 2: on_startup() receives only own butler's config, db, credential_store.

        Modules cannot receive another butler's DB pool or credential store.
        The signature enforces this isolation.
        """
        from butlers.modules.base import Module

        sig = inspect.signature(Module.on_startup)
        params = list(sig.parameters.keys())
        # Expected: self, config, db, credential_store, blob_store
        assert "config" in params, "on_startup must accept config"
        assert "db" in params, "on_startup must accept db"
        # Must NOT have cross-butler params
        forbidden = ["other_pool", "health_db", "finance_db", "switchboard_pool"]
        for p in forbidden:
            assert p not in params, (
                f"on_startup must not have cross-butler param '{p}' (Vision Rule 2)"
            )


class TestConcreteModuleBoundaries:
    """RFC 0002: Concrete modules must not access core infrastructure objects."""

    def test_email_module_does_not_touch_scheduler(self):
        """Vision Rule 2: Email module must not modify the scheduler."""
        try:
            from butlers.modules.email import EmailModule
        except ImportError:
            pytest.skip("EmailModule not available in this environment")

        src = inspect.getsource(EmailModule)
        # Must not directly modify scheduled_tasks table
        assert "INSERT INTO scheduled_tasks" not in src, (
            "EmailModule must not insert into scheduled_tasks directly (Vision Rule 2)"
        )
        assert "UPDATE scheduled_tasks" not in src, (
            "EmailModule must not update scheduled_tasks directly (Vision Rule 2)"
        )

    def test_telegram_module_does_not_touch_sessions_table(self):
        """Vision Rule 2: Telegram module must not write to sessions table."""
        try:
            from butlers.modules.telegram import TelegramModule
        except ImportError:
            pytest.skip("TelegramModule not available in this environment")

        src = inspect.getsource(TelegramModule)
        assert "INSERT INTO sessions" not in src, (
            "TelegramModule must not insert into sessions table directly (Vision Rule 2)"
        )

    def test_migration_revisions_returns_string_or_none(self):
        """RFC 0002: migration_revisions() returns the Alembic branch label or None.

        None means the module has no DB tables. A string is the branch label
        (same as module name by convention).
        """
        m = _make_minimal_module("boundary_test_module")
        result = m.migration_revisions()
        assert result is None or isinstance(result, str), (
            "migration_revisions must return str or None (RFC 0002)"
        )

    def test_on_shutdown_is_async(self):
        """RFC 0002: on_shutdown() must be async for the reverse-order shutdown protocol."""
        import asyncio

        from butlers.modules.base import Module

        assert asyncio.iscoroutinefunction(Module.on_shutdown), (
            "on_shutdown must be async (RFC 0001: reverse-order shutdown protocol)"
        )

    def test_on_startup_is_async(self):
        """RFC 0002: on_startup() must be async for the startup phase protocol."""
        import asyncio

        from butlers.modules.base import Module

        assert asyncio.iscoroutinefunction(Module.on_startup), (
            "on_startup must be async (RFC 0001: Phase 9 startup protocol)"
        )

    def test_register_tools_is_async(self):
        """RFC 0002: register_tools() must be async for non-blocking tool registration."""
        import asyncio

        from butlers.modules.base import Module

        assert asyncio.iscoroutinefunction(Module.register_tools), (
            "register_tools must be async (RFC 0002)"
        )

    def test_concrete_module_respects_no_core_infrastructure_rule(self):
        """Vision Rule 2: A concrete module that only adds tools satisfies the rule."""
        m = _make_minimal_module("pure_tool_module")
        # Instantiation succeeds — the module is valid
        assert m.name == "pure_tool_module"
        assert m.dependencies == []
        assert m.migration_revisions() is None

    def test_tool_metadata_does_not_expose_session_log(self):
        """Vision Rule 2: tool_metadata() must not return session log entries.

        ToolMeta is only for argument sensitivity declarations. It cannot
        be used to access core infrastructure.
        """
        from butlers.modules.base import ToolMeta

        meta = ToolMeta(arg_sensitivities={"recipient": True, "body": False})
        # ToolMeta only has arg_sensitivities — no core infrastructure refs
        fields = (
            list(meta.__dataclass_fields__.keys()) if hasattr(meta, "__dataclass_fields__") else []
        )
        if fields:
            assert fields == ["arg_sensitivities"], (
                "ToolMeta must only have arg_sensitivities field (Vision Rule 2)"
            )
