"""Tests for the ModuleRegistry with topological sort."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class EmptyConfig(BaseModel):
    """Minimal config schema for testing."""


# ---------------------------------------------------------------------------
# Concrete module fixtures
# ---------------------------------------------------------------------------


def _make_module(
    name: str,
    deps: list[str] | None = None,
    migration_label: str | None = None,
) -> type[Module]:
    """Dynamically create a concrete Module subclass for testing."""
    _name = name
    _deps = deps or []
    _migration_label = migration_label

    class DynamicModule(Module):
        @property
        def name(self) -> str:
            return _name

        @property
        def config_schema(self) -> type[BaseModel]:
            return EmptyConfig

        @property
        def dependencies(self) -> list[str]:
            return list(_deps)

        async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return _migration_label

        async def on_startup(self, config: Any, db: Any) -> None:
            pass

        async def on_shutdown(self) -> None:
            pass

    DynamicModule.__qualname__ = f"DynamicModule_{_name}"
    DynamicModule.__name__ = f"DynamicModule_{_name}"
    return DynamicModule


# Convenience pre-built module classes
ModuleA = _make_module("a")
ModuleB = _make_module("b", deps=["a"])
ModuleC = _make_module("c", deps=["b"])
ModuleD = _make_module("d", deps=["a", "b"])  # diamond dep
ModuleX = _make_module("x")
ModuleY = _make_module("y")

# Circular dependency modules
ModuleCycleA = _make_module("cycle_a", deps=["cycle_b"])
ModuleCycleB = _make_module("cycle_b", deps=["cycle_a"])

# Indirect circular dependency: p -> q -> r -> p
ModuleP = _make_module("p", deps=["r"])
ModuleQ = _make_module("q", deps=["p"])
ModuleR = _make_module("r", deps=["q"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_module():
    """Registering a module makes it available."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    assert "a" in reg.available_modules


def test_register_duplicate_name_raises():
    """Registering two modules with the same name raises ValueError."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    # Create a second class that also returns name "a"
    AnotherA = _make_module("a")
    with pytest.raises(ValueError, match="already registered"):
        reg.register(AnotherA)


def test_available_modules():
    """available_modules returns a sorted list of registered names."""
    reg = ModuleRegistry()
    reg.register(ModuleB)
    reg.register(ModuleA)
    reg.register(ModuleC)
    assert reg.available_modules == ["a", "b", "c"]


def test_load_single_module():
    """Loading a single module with no dependencies returns it alone."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    result = reg.load_from_config({"a": {}})
    assert len(result) == 1
    assert result[0].name == "a"


def test_load_linear_dependency_chain():
    """A -> B -> C becomes [C, B, A] (dependencies first)."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)
    reg.register(ModuleC)
    result = reg.load_from_config({"a": {}, "b": {}, "c": {}})
    names = [m.name for m in result]
    # a has no deps, b depends on a, c depends on b
    assert names == ["a", "b", "c"]


def test_load_diamond_dependency():
    """Diamond: D depends on both A and B; B depends on A.

    Valid order: A, B, D  (A must come before both B and D).
    """
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)
    reg.register(ModuleD)
    result = reg.load_from_config({"a": {}, "b": {}, "d": {}})
    names = [m.name for m in result]
    # a must come before b and d; b must come before d
    assert names.index("a") < names.index("b")
    assert names.index("a") < names.index("d")
    assert names.index("b") < names.index("d")


def test_load_independent_modules():
    """Independent modules (no deps) are returned in sorted order."""
    reg = ModuleRegistry()
    reg.register(ModuleY)
    reg.register(ModuleX)
    reg.register(ModuleA)
    result = reg.load_from_config({"x": {}, "y": {}, "a": {}})
    names = [m.name for m in result]
    # All independent — sorted alphabetically by name
    assert names == ["a", "x", "y"]


def test_circular_dependency_raises():
    """Direct circular dependency (A <-> B) raises ValueError."""
    reg = ModuleRegistry()
    reg.register(ModuleCycleA)
    reg.register(ModuleCycleB)
    with pytest.raises(ValueError, match="Circular dependency"):
        reg.load_from_config({"cycle_a": {}, "cycle_b": {}})


def test_indirect_circular_dependency_raises():
    """Indirect circular dependency (P -> Q -> R -> P) raises ValueError."""
    reg = ModuleRegistry()
    reg.register(ModuleP)
    reg.register(ModuleQ)
    reg.register(ModuleR)
    with pytest.raises(ValueError, match="Circular dependency"):
        reg.load_from_config({"p": {}, "q": {}, "r": {}})


def test_missing_dependency_raises():
    """A module depending on a module not in the enabled set raises ValueError."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)  # B depends on A
    # Enable B but not A
    with pytest.raises(ValueError, match="not in the enabled module set"):
        reg.load_from_config({"b": {}})


def test_unknown_module_raises():
    """Requesting an unregistered module raises ValueError."""
    reg = ModuleRegistry()
    with pytest.raises(ValueError, match="Unknown module"):
        reg.load_from_config({"nonexistent": {}})


def test_empty_config_returns_empty():
    """An empty config dict returns an empty list."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    result = reg.load_from_config({})
    assert result == []


# ---------------------------------------------------------------------------
# Tests for load_all()
# ---------------------------------------------------------------------------


def test_load_all_no_config_loads_all_registered():
    """load_all() with empty config loads every registered module."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleX)
    result = reg.load_all({})
    names = {m.name for m in result}
    assert names == {"a", "x"}


def test_load_all_with_config_loads_all_registered():
    """load_all() includes all modules even when only some appear in config."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleX)
    # Only "a" is in config; "x" should still be loaded with implicit empty dict.
    result = reg.load_all({"a": {"some_setting": 1}})
    names = {m.name for m in result}
    assert names == {"a", "x"}


def test_load_all_dependency_order_respected():
    """load_all() still produces dependency-first ordering."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)  # B depends on A
    reg.register(ModuleC)  # C depends on B
    result = reg.load_all({})
    names = [m.name for m in result]
    assert names.index("a") < names.index("b")
    assert names.index("b") < names.index("c")


def test_load_all_diamond_dependency_order():
    """load_all() handles diamond dependencies correctly."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)  # B depends on A
    reg.register(ModuleD)  # D depends on A and B
    result = reg.load_all({})
    names = [m.name for m in result]
    assert names.index("a") < names.index("b")
    assert names.index("a") < names.index("d")
    assert names.index("b") < names.index("d")


def test_load_all_independent_modules_sorted():
    """load_all() with no deps returns modules in sorted name order."""
    reg = ModuleRegistry()
    reg.register(ModuleY)
    reg.register(ModuleX)
    reg.register(ModuleA)
    result = reg.load_all({})
    names = [m.name for m in result]
    assert names == ["a", "x", "y"]


def test_load_all_circular_dependency_raises():
    """load_all() still detects and raises on circular dependencies."""
    reg = ModuleRegistry()
    reg.register(ModuleCycleA)
    reg.register(ModuleCycleB)
    with pytest.raises(ValueError, match="Circular dependency"):
        reg.load_all({})


def test_load_all_empty_registry_returns_empty():
    """load_all() with no registered modules returns an empty list."""
    reg = ModuleRegistry()
    result = reg.load_all({})
    assert result == []


def test_load_all_configured_module_gets_its_config_passed_through():
    """Modules with explicit config get that config available (structural test).

    load_all() doesn't inject config into module instances directly —
    the config dict is consumed later during _validate_module_configs().
    This test verifies the method returns instances for ALL registered modules
    including those with and without config, which is the behavioral contract.
    """
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleX)
    # Config for "a" only
    result = reg.load_all({"a": {"key": "value"}})
    # Both modules are returned
    returned_names = {m.name for m in result}
    assert returned_names == {"a", "x"}
    # The module instances themselves are plain — config is consumed downstream.
    for mod in result:
        assert isinstance(mod, Module)


def test_load_all_returns_new_instances_each_call():
    """Each load_all() call creates fresh module instances."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    result1 = reg.load_all({})
    result2 = reg.load_all({})
    assert result1[0] is not result2[0]


def test_load_all_with_full_dependency_chain_and_mixed_config():
    """load_all() handles a linear chain where only the middle module has config."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)  # B depends on A
    reg.register(ModuleC)  # C depends on B
    # Only "b" configured; "a" and "c" get empty dict.
    result = reg.load_all({"b": {"setting": True}})
    names = [m.name for m in result]
    # All three loaded, dependency order preserved.
    assert set(names) == {"a", "b", "c"}
    assert names.index("a") < names.index("b")
    assert names.index("b") < names.index("c")
