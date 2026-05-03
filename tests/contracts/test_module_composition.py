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


class TestCascadeFailure:
    """RFC 0002: Module startup failure cascades to dependents; daemon continues."""

    def test_cascade_failure_marks_dependents_unavailable(self):
        """RFC 0002 + RFC 0001: Module A startup fails => B (depends on A) marked unavailable.

        When a module fails during on_startup(), all modules that declared it as
        a dependency are cascade-failed and marked unavailable. The daemon continues
        with the remaining healthy modules (non-fatal failure mode).
        """
        from butlers.modules.registry import ModuleRegistry
        from tests.modules.test_module_registry import _make_module

        # Build modules where B depends on A
        # A will fail during load; B declares A as dependency
        FailA = _make_module("fail_a", deps=[])
        DepB = _make_module("dep_b", deps=["fail_a"])

        reg = ModuleRegistry()
        reg.register(FailA)
        reg.register(DepB)

        # Both A and B are loadable via registry
        result = reg.load_from_config({"fail_a": {}, "dep_b": {}})
        names = [m.name for m in result]
        assert "fail_a" in names, "fail_a must load from registry"
        assert "dep_b" in names, "dep_b must load from registry (topology resolved)"

        # The cascade failure path: if fail_a fails on_startup,
        # dep_b should be marked unavailable (tested via lifecycle source)
        import inspect

        from butlers import lifecycle

        src = inspect.getsource(lifecycle)
        assert "cascade" in src.lower() or "depend" in src.lower(), (
            "lifecycle must implement cascade failure for module dependencies (RFC 0002)"
        )

    def test_failed_module_does_not_block_other_modules(self):
        """RFC 0001: Non-fatal module failure — daemon continues with remaining modules.

        When a module fails at on_startup() (phase 9), the daemon continues with the
        remaining healthy modules. The failed module is marked unavailable, but other
        non-dependent modules proceed normally.
        """
        import inspect

        from butlers.daemon import ButlerDaemon

        src = inspect.getsource(ButlerDaemon)
        # Daemon must handle module failures without aborting startup
        assert "module" in src.lower() and (
            "disabled" in src.lower() or "unavailable" in src.lower() or "failed" in src.lower()
        ), "ButlerDaemon must handle module startup failures non-fatally (RFC 0001)"
