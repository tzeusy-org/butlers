"""Tests for the Module abstract base class."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test fixtures — concrete subclasses
# ---------------------------------------------------------------------------


class EmptyConfig(BaseModel):
    """Minimal config schema for testing."""


class MinimalModule(Module):
    """Fully concrete implementation with the bare minimum logic."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class ModuleWithDeps(Module):
    """Module that declares dependencies."""

    @property
    def name(self) -> str:
        return "with_deps"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return ["minimal"]

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return "with_deps_branch"

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class MissingRegisterToolsModule(Module):
    """Missing the `register_tools` method."""

    @property
    def name(self) -> str:
        return "incomplete"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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
    # Should not raise
    await mod.register_tools(mcp=None, config=None, db=None)


async def test_on_startup_signature():
    """on_startup is callable with the expected arguments."""
    mod = MinimalModule()
    await mod.on_startup(config=None, db=None)


async def test_on_shutdown_signature():
    """on_shutdown is callable with no arguments (besides self)."""
    mod = MinimalModule()
    await mod.on_shutdown()
