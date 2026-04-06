"""Contract tests: Module Composition (RFC 0002, Invariant 5).

Validates topological sort, cycle detection, and registry contracts.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestTopologicalSort:
    """RFC 0002: Modules resolved in dependency order."""

    def test_dependency_order_and_diamond(self):
        from butlers.modules.registry import ModuleRegistry
        from tests.modules.test_module_registry import (
            ModuleA,
            ModuleB,
            ModuleC,
            ModuleD,
        )

        reg = ModuleRegistry()
        reg.register(ModuleA)
        reg.register(ModuleB)
        reg.register(ModuleC)
        result = reg.load_from_config({"a": {}, "b": {}, "c": {}})
        names = [m.name for m in result]
        assert names.index("a") < names.index("b") < names.index("c")

        # Diamond dependency
        reg2 = ModuleRegistry()
        reg2.register(ModuleA)
        reg2.register(ModuleB)
        reg2.register(ModuleD)
        names2 = [m.name for m in reg2.load_from_config({"a": {}, "b": {}, "d": {}})]
        assert names2.index("a") < names2.index("b")
        assert names2.index("a") < names2.index("d")


class TestCycleDetection:
    """RFC 0002: Circular dependencies detected and rejected."""

    def test_cycles_detected(self):
        from butlers.modules.registry import ModuleRegistry
        from tests.modules.test_module_registry import _make_module

        for deps_pairs in [
            [("ca", ["cb"]), ("cb", ["ca"])],
            [("p", ["q"]), ("q", ["r"]), ("r", ["p"])],
        ]:
            reg = ModuleRegistry()
            for name, deps in deps_pairs:
                reg.register(_make_module(name, deps=deps))
            config = {name: {} for name, _ in deps_pairs}
            with pytest.raises(ValueError, match="Circular dependency"):
                reg.load_from_config(config)


class TestRegistryContracts:
    """RFC 0002: Registry enforces uniqueness and discovers built-in modules."""

    def test_registry_error_cases_and_load_all(self):
        from butlers.modules.registry import ModuleRegistry
        from tests.modules.test_module_registry import ModuleA, ModuleB, _make_module

        reg = ModuleRegistry()
        reg.register(ModuleA)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_module("a"))

        with pytest.raises(ValueError, match="Unknown module"):
            ModuleRegistry().load_from_config({"nonexistent": {}})

        reg2 = ModuleRegistry()
        reg2.register(ModuleA)
        reg2.register(ModuleB)
        assert {m.name for m in reg2.load_all({})} == {"a", "b"}

    def test_default_registry_discovers_built_in_modules(self):
        from butlers.modules.registry import default_registry

        assert len(default_registry().available_modules) > 0
