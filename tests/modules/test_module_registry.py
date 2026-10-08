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

        async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return _migration_label

        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
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
# Tests: registration and load_from_config
# ---------------------------------------------------------------------------


def test_register_and_available_modules():
    """Registering modules makes them available in sorted order."""
    reg = ModuleRegistry()
    reg.register(ModuleB)
    reg.register(ModuleA)
    reg.register(ModuleC)
    assert reg.available_modules == ["a", "b", "c"]


def test_register_duplicate_name_raises():
    reg = ModuleRegistry()
    reg.register(ModuleA)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_make_module("a"))


def test_load_single_module():
    reg = ModuleRegistry()
    reg.register(ModuleA)
    result = reg.load_from_config({"a": {}})
    assert len(result) == 1
    assert result[0].name == "a"


def test_load_linear_dependency_chain():
    """A -> B -> C becomes [A, B, C] (dependencies first)."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)
    reg.register(ModuleC)
    names = [m.name for m in reg.load_from_config({"a": {}, "b": {}, "c": {}})]
    assert names == ["a", "b", "c"]


def test_load_diamond_dependency():
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)
    reg.register(ModuleD)
    names = [m.name for m in reg.load_from_config({"a": {}, "b": {}, "d": {}})]
    assert names.index("a") < names.index("b")
    assert names.index("b") < names.index("d")


def test_load_independent_modules_sorted():
    reg = ModuleRegistry()
    reg.register(ModuleY)
    reg.register(ModuleX)
    reg.register(ModuleA)
    names = [m.name for m in reg.load_from_config({"x": {}, "y": {}, "a": {}})]
    assert names == ["a", "x", "y"]


@pytest.mark.parametrize(
    "modules,config,match",
    [
        ([ModuleCycleA, ModuleCycleB], {"cycle_a": {}, "cycle_b": {}}, "Circular dependency"),
        ([ModuleP, ModuleQ, ModuleR], {"p": {}, "q": {}, "r": {}}, "Circular dependency"),
        ([ModuleA, ModuleB], {"b": {}}, "not in the enabled module set"),
        ([], {"nonexistent": {}}, "Unknown module"),
    ],
    ids=["direct-cycle", "indirect-cycle", "missing-dep", "unknown-module"],
)
def test_load_error_cases(modules, config, match):
    reg = ModuleRegistry()
    for m in modules:
        reg.register(m)
    with pytest.raises(ValueError, match=match):
        reg.load_from_config(config)


def test_empty_config_returns_empty():
    reg = ModuleRegistry()
    reg.register(ModuleA)
    assert reg.load_from_config({}) == []


# ---------------------------------------------------------------------------
# Tests: load_all()
# ---------------------------------------------------------------------------


def test_load_all_loads_all_registered_with_dependency_order():
    """load_all() includes all modules, respects dependency order, handles config."""
    reg = ModuleRegistry()
    reg.register(ModuleA)
    reg.register(ModuleB)
    reg.register(ModuleC)
    result = reg.load_all({"b": {"setting": True}})
    names = [m.name for m in result]
    assert set(names) == {"a", "b", "c"}
    assert names.index("a") < names.index("b") < names.index("c")
    for mod in result:
        assert isinstance(mod, Module)


def test_load_all_circular_dependency_raises():
    reg = ModuleRegistry()
    reg.register(ModuleCycleA)
    reg.register(ModuleCycleB)
    with pytest.raises(ValueError, match="Circular dependency"):
        reg.load_all({})


def test_load_all_empty_registry_returns_empty():
    assert ModuleRegistry().load_all({}) == []


def test_load_all_returns_new_instances_each_call():
    reg = ModuleRegistry()
    reg.register(ModuleA)
    result1 = reg.load_all({})
    result2 = reg.load_all({})
    assert result1[0] is not result2[0]


def test_explicit_discovery_and_router_load_failures_are_visible(monkeypatch, tmp_path, caplog):
    """REQ-core-modules-004, REQ-dashboard-api-066: no partial cached success."""
    import importlib
    import sys

    import butlers.modules.registry as registry_module
    from butlers.api.router_discovery import _load_router_module, discover_butler_routers

    real_import = importlib.import_module
    monkeypatch.setattr(
        registry_module.pkgutil,
        "walk_packages",
        lambda *a, **k: [(None, "synthetic_broken", False)],
    )

    def broken(name, *args, **kwargs):
        if name == "synthetic_broken":
            raise RuntimeError("controlled discovery failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(registry_module.importlib, "import_module", broken)
    with pytest.raises(ImportError, match="synthetic_broken"):
        registry_module.default_registry()
    assert "synthetic_broken" not in sys.modules
    monkeypatch.setattr(registry_module.pkgutil, "walk_packages", lambda *a, **k: [])
    assert "relationship" in registry_module.default_registry().available_modules

    file = tmp_path / "router.py"
    key = "controlled_failed_router"
    monkeypatch.delitem(sys.modules, key, raising=False)
    file.write_text("raise RuntimeError('controlled body failure')\n")
    with pytest.raises(RuntimeError, match="controlled body failure"):
        _load_router_module(file, key)
    assert key not in sys.modules
    file.write_text("from fastapi import APIRouter\nrouter=APIRouter()\n")
    module = _load_router_module(file, key)
    assert module.router.routes == []
    assert _load_router_module(file, key) is module
    other = tmp_path / "other.py"
    other.write_text("from fastapi import APIRouter\nrouter=APIRouter()\n")
    with pytest.raises(ImportError, match="source differs"):
        _load_router_module(other, key)
    monkeypatch.delitem(sys.modules, key)

    # Bulk optional API policy is separate from strict direct-load policy.
    invalid = tmp_path / "optional" / "api" / "router.py"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("raise RuntimeError('controlled optional failure')\n")
    healthy = tmp_path / "healthy" / "api" / "router.py"
    healthy.parent.mkdir(parents=True)
    healthy.write_text("from fastapi import APIRouter\nrouter=APIRouter()\n")
    found = dict(discover_butler_routers(tmp_path))
    assert list(found) == ["healthy"]
    assert found["healthy"].router.routes == []
    assert "optional" in caplog.text and "controlled optional failure" in caplog.text
    for name in list(sys.modules):
        if name.startswith(("healthy_api_router_", "optional_api_router_")):
            monkeypatch.delitem(sys.modules, name)
