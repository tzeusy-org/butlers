"""Tests for the Module abstract base class."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastmcp import FastMCP
from pydantic import BaseModel

from butlers.modules.base import (
    Module,
    ToolGroupMixin,
    ToolMeta,
    group_enabled,
    guarded_tool,
    tool_registrar,
)
from butlers.testing.mcp import registered_tools as snapshot_registered_tools

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test fixtures — concrete subclasses
# ---------------------------------------------------------------------------


class EmptyConfig(BaseModel):
    """Minimal config schema for testing."""


class MinimalModule(Module):
    """Fully concrete implementation with the bare minimum logic."""

    name = "minimal"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

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
        pass

    async def on_shutdown(self) -> None:
        pass


class ModuleWithDeps(Module):
    """Module that declares dependencies."""

    name = "with_deps"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return ["minimal"]

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return "with_deps_branch"

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Partial implementations used to verify TypeError on missing members
# ---------------------------------------------------------------------------


class MissingNameModule(Module):
    """Missing the `name` property."""

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

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
        pass

    async def on_shutdown(self) -> None:
        pass


class MissingRegisterToolsModule(Module):
    """Missing the `register_tools` method."""

    name = "incomplete"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cannot_instantiate_module_abc():
    """Module is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Module()  # type: ignore[abstract]


def test_missing_abstract_member_raises():
    """A subclass that omits any abstract member cannot be instantiated."""
    with pytest.raises(TypeError):
        MissingNameModule()  # type: ignore[abstract]

    with pytest.raises(TypeError):
        MissingRegisterToolsModule()  # type: ignore[abstract]


def test_minimal_concrete_module():
    """A fully concrete subclass instantiates and exposes the correct interface."""
    mod = MinimalModule()
    assert mod.name == "minimal"
    assert mod.config_schema is EmptyConfig
    assert mod.dependencies == []
    assert mod.migration_revisions() is None


def test_tool_meta_combines_sensitivity_and_presentation_metadata():
    meta = ToolMeta(
        arg_sensitivities={"recipient": True},
        canonical_name="notify",
        module_name="core",
        group_name="notifications",
        namespace="core.notifications",
        llm_presentable=True,
        load_posture="deferred",
    )

    assert meta.arg_sensitivities == {"recipient": True}
    assert meta.canonical_name == "notify"
    assert meta.module_name == "core"
    assert meta.group_name == "notifications"
    assert meta.namespace == "core.notifications"
    assert meta.llm_presentable is True
    assert meta.load_posture == "deferred"


def test_tool_meta_unclassified_default_preserves_legacy_compatibility():
    meta = ToolMeta(arg_sensitivities={"_write": True})

    assert meta.arg_sensitivities == {"_write": True}
    assert meta.canonical_name is None
    assert meta.module_name is None
    assert meta.llm_presentable is None
    assert meta.load_posture is None


def test_module_with_no_dependencies():
    """A module can declare an empty dependency list."""
    mod = MinimalModule()
    assert mod.dependencies == []


def test_module_with_no_migrations():
    """A module can return None for migration_revisions."""
    mod = MinimalModule()
    assert mod.migration_revisions() is None


def test_module_with_dependencies_and_migrations():
    """A module can declare dependencies and a migration branch label."""
    mod = ModuleWithDeps()
    assert mod.name == "with_deps"
    assert mod.dependencies == ["minimal"]
    assert mod.migration_revisions() == "with_deps_branch"


async def test_register_tools_signature():
    """register_tools is callable with the expected arguments."""
    mod = MinimalModule()
    # Should not raise — with and without butler_name
    await mod.register_tools(mcp=None, config=None, db=None, butler_name="test-butler")
    await mod.register_tools(mcp=None, config=None, db=None, butler_name="my-butler")


async def test_on_startup_signature():
    """on_startup is callable with the expected arguments."""
    mod = MinimalModule()
    await mod.on_startup(config=None, db=None)


async def test_on_shutdown_signature():
    """on_shutdown is callable with no arguments (besides self)."""
    mod = MinimalModule()
    await mod.on_shutdown()


async def test_extra_status_fields_default_returns_empty():
    """Module base class extra_status_fields() returns {} by default."""
    mod = MinimalModule()
    result = await mod.extra_status_fields()
    assert result == {}


# ---------------------------------------------------------------------------
# Tool group filtering
# ---------------------------------------------------------------------------


class TestGroupEnabled:
    def test_none_groups_enables_all(self):
        """No groups config = all groups enabled (backwards compatible)."""
        cfg = ToolGroupMixin(groups=None)
        assert group_enabled(cfg, "core")
        assert group_enabled(cfg, "anything")

    def test_empty_groups_enables_all(self):
        """Empty groups list = all groups enabled."""
        cfg = ToolGroupMixin(groups=[])
        assert group_enabled(cfg, "core")

    def test_specific_groups(self):
        """Only listed groups are enabled."""
        cfg = ToolGroupMixin(groups=["core", "entity"])
        assert group_enabled(cfg, "core")
        assert group_enabled(cfg, "entity")
        assert not group_enabled(cfg, "admin")
        assert not group_enabled(cfg, "feedback")

    def test_no_groups_attr(self):
        """Config without groups attr = all enabled."""
        assert group_enabled(object(), "core")

    def test_mixin_in_config(self):
        """ToolGroupMixin works as a Pydantic mixin."""

        class MyConfig(ToolGroupMixin, BaseModel):
            foo: str = "bar"

        cfg = MyConfig(groups=["core"])
        assert group_enabled(cfg, "core")
        assert not group_enabled(cfg, "admin")
        assert cfg.foo == "bar"


# ---------------------------------------------------------------------------
# ABC defaults: only name, config_schema and register_tools are required
# ---------------------------------------------------------------------------


class BareModule(Module):
    """Implements only the three abstract members."""

    name = "bare"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        pass


async def test_bare_module_gets_formal_hook_defaults():
    mod = BareModule()

    assert mod.dependencies == []
    assert mod.migration_revisions() is None
    assert mod.credentials_env == []
    assert await mod.on_startup(config=None, db=None) is None
    assert await mod.on_shutdown() is None
    assert mod.on_all_modules_ready({"bare": mod}) is None


# ---------------------------------------------------------------------------
# tool_registrar and guarded_tool
# ---------------------------------------------------------------------------


class TestToolRegistrar:
    def test_registers_only_enabled_groups(self):
        mcp = FastMCP("module-base-registration-test-tools")
        tool = tool_registrar(mcp, ["core"])

        @tool("core")
        async def in_core() -> None: ...

        @tool("admin")
        async def in_admin() -> None: ...

        assert list(asyncio.run(snapshot_registered_tools(mcp))) == ["in_core"]

    def test_disabled_tool_function_is_still_defined(self):
        tool = tool_registrar(FastMCP("module-base-registration-test-tools"), ["core"])

        @tool("admin")
        async def in_admin() -> str:
            return "defined"

        assert in_admin.__name__ == "in_admin"

    @pytest.mark.parametrize("groups", [None, []])
    def test_no_groups_registers_everything(self, groups):
        mcp = FastMCP("module-base-registration-test-tools")
        tool = tool_registrar(mcp, groups)

        @tool("anything")
        async def any_tool() -> None: ...

        assert list(asyncio.run(snapshot_registered_tools(mcp))) == ["any_tool"]

    def test_accepts_a_module_config_carrying_groups(self):
        mcp = FastMCP("module-base-registration-test-tools")
        tool = tool_registrar(mcp, ToolGroupMixin(groups=["core"]))

        @tool("core")
        async def core_tool() -> None: ...

        @tool("entity")
        async def entity_tool() -> None: ...

        assert list(asyncio.run(snapshot_registered_tools(mcp))) == ["core_tool"]


class TestGuardedTool:
    @staticmethod
    def _guard(ready: bool, **kwargs):
        return guarded_tool(
            credential=lambda: ready,
            missing=lambda: {"error": "no credentials"},
            **kwargs,
        )

    async def test_missing_credential_short_circuits_without_running_the_tool(self):
        ran = []

        @self._guard(False)
        async def tool() -> dict:
            ran.append(True)
            return {"ok": True}

        assert await tool() == {"error": "no credentials"}
        assert ran == []

    async def test_ready_credential_runs_the_tool(self):
        @self._guard(True)
        async def tool(x: int = 1) -> dict:
            return {"x": x}

        assert await tool(x=3) == {"x": 3}

    async def test_errors_are_mapped_when_a_mapper_is_given(self):
        @self._guard(True, errors=lambda exc: {"error": f"mapped {exc}"})
        async def tool() -> dict:
            raise ValueError("boom")

        assert await tool() == {"error": "mapped boom"}

    async def test_a_tool_needing_no_credential_still_gets_error_mapping(self):
        @guarded_tool(errors=lambda exc: {"error": str(exc)})
        async def tool() -> dict:
            raise ValueError("boom")

        assert await tool() == {"error": "boom"}

    def test_credential_and_missing_must_be_given_together(self):
        with pytest.raises(TypeError):
            guarded_tool(credential=lambda: True)

    async def test_errors_propagate_without_a_mapper(self):
        @self._guard(True)
        async def tool() -> dict:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await tool()

    def test_wrapper_keeps_name_doc_and_signature_for_fastmcp(self):
        import inspect

        @self._guard(True)
        async def tool(query: str, limit: int = 10) -> dict:
            """Search things."""
            return {}

        assert tool.__name__ == "tool"
        assert tool.__doc__ == "Search things."
        assert list(inspect.signature(tool).parameters) == ["query", "limit"]
