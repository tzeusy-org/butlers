"""Contract tests: Module Composition (RFC 0002, Invariant 5).

Validates topological sort, cycle detection, and cascade failure behavior.
Modules are resolved in dependency order with cycle detection at startup.

Principle: Modules are resolved via topological sort; cycles are detected
at startup as fatal errors (RFC 0002, RFC 0001 Phase 3).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.contract


def _make_module(name: str, deps: list[str]):
    """Factory for minimal Module instances used in composition tests."""
    from butlers.modules.base import Module

    class _M(Module):
        @property
        def name(self) -> str:
            return name

        @property
        def config_schema(self) -> type[BaseModel]:
            return BaseModel

        @property
        def dependencies(self) -> list[str]:
            return deps

        async def register_tools(self, mcp, config, db) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return None

        async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
            pass

        async def on_shutdown(self) -> None:
            pass

    _M.__name__ = f"Module_{name}"
    return _M()


class TestTopologicalSort:
    """RFC 0002 + RFC 0001 Phase 3: Dependencies resolved in topological order."""

    def test_single_module_no_deps_returns_that_module(self):
        """RFC 0002: Single module with no deps is returned as-is."""
        from butlers.modules.registry import _topological_sort

        m = _make_module("standalone", [])
        result = _topological_sort({"standalone": m})
        assert len(result) == 1
        assert result[0].name == "standalone"

    def test_dependency_comes_before_dependent(self):
        """RFC 0002: Module B that depends on A must appear after A in sorted order."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("base_module", [])
        b = _make_module("ext_module", ["base_module"])
        result = _topological_sort({"base_module": a, "ext_module": b})
        names = [m.name for m in result]
        assert names.index("base_module") < names.index("ext_module"), (
            "Dependency must appear before dependent in topological order (RFC 0002)"
        )

    def test_multi_level_dependency_chain_sorted_correctly(self):
        """RFC 0002: Transitive dependencies are resolved in full topological order."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("level_a", [])
        b = _make_module("level_b", ["level_a"])
        c = _make_module("level_c", ["level_b"])
        result = _topological_sort({"level_a": a, "level_b": b, "level_c": c})
        names = [m.name for m in result]
        assert names.index("level_a") < names.index("level_b"), "A before B"
        assert names.index("level_b") < names.index("level_c"), "B before C"

    def test_independent_modules_both_present(self):
        """RFC 0002: Modules without shared dependencies are both included."""
        from butlers.modules.registry import _topological_sort

        x = _make_module("module_x", [])
        y = _make_module("module_y", [])
        result = _topological_sort({"module_x": x, "module_y": y})
        names = {m.name for m in result}
        assert "module_x" in names
        assert "module_y" in names

    def test_diamond_dependency_resolves_correctly(self):
        """RFC 0002: Diamond dependency (A<-B, A<-C, B+C<-D) resolves without duplication."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("diamond_a", [])
        b = _make_module("diamond_b", ["diamond_a"])
        c = _make_module("diamond_c", ["diamond_a"])
        d = _make_module("diamond_d", ["diamond_b", "diamond_c"])
        instances = {"diamond_a": a, "diamond_b": b, "diamond_c": c, "diamond_d": d}
        result = _topological_sort(instances)
        names = [m.name for m in result]
        # A must come before B, C, and D
        assert names.index("diamond_a") < names.index("diamond_b")
        assert names.index("diamond_a") < names.index("diamond_c")
        # B and C must come before D
        assert names.index("diamond_b") < names.index("diamond_d")
        assert names.index("diamond_c") < names.index("diamond_d")
        # No duplicates
        assert len(names) == len(set(names))


class TestCycleDetection:
    """RFC 0001 Phase 3: Dependency cycle detection is fatal."""

    def test_direct_cycle_raises_value_error(self):
        """RFC 0001 Phase 3: A->B->A cycle is a fatal startup error."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("cycle_a", ["cycle_b"])
        b = _make_module("cycle_b", ["cycle_a"])
        with pytest.raises(ValueError, match="[Cc]ircular|[Cc]ycle"):
            _topological_sort({"cycle_a": a, "cycle_b": b})

    def test_three_node_cycle_raises_value_error(self):
        """RFC 0001 Phase 3: A->B->C->A three-node cycle is detected."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("tri_a", ["tri_c"])
        b = _make_module("tri_b", ["tri_a"])
        c = _make_module("tri_c", ["tri_b"])
        with pytest.raises(ValueError):
            _topological_sort({"tri_a": a, "tri_b": b, "tri_c": c})

    def test_self_dependency_raises_value_error(self):
        """RFC 0001 Phase 3: A module cannot depend on itself."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("self_dep", ["self_dep"])
        with pytest.raises((ValueError, KeyError)):
            _topological_sort({"self_dep": a})

    def test_error_message_identifies_problematic_modules(self):
        """RFC 0001 Phase 3: Cycle detection error message names the involved modules."""
        from butlers.modules.registry import _topological_sort

        a = _make_module("cyclemod_x", ["cyclemod_y"])
        b = _make_module("cyclemod_y", ["cyclemod_x"])
        with pytest.raises(ValueError) as exc_info:
            _topological_sort({"cyclemod_x": a, "cyclemod_y": b})
        error_msg = str(exc_info.value)
        # Error must identify at least one of the cycled modules
        assert "cyclemod_x" in error_msg or "cyclemod_y" in error_msg, (
            "Cycle detection error must identify involved modules (RFC 0001)"
        )


class TestRegistryContracts:
    """RFC 0002: ModuleRegistry provides consistent module management."""

    def test_register_same_name_twice_raises_value_error(self):
        """RFC 0002: Duplicate module registration is rejected with ValueError."""
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        class DupModule(Module):
            @property
            def name(self) -> str:
                return "dup"

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

        registry = ModuleRegistry()
        registry.register(DupModule)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(DupModule)

    def test_available_modules_returns_sorted_list(self):
        """RFC 0002: available_modules returns deterministically sorted module names."""
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        registry = ModuleRegistry()
        for letter in ["charlie", "alpha", "bravo"]:
            m = _make_module(f"sort_{letter}", [])
            type(m).__name__ = f"Module_sort_{letter}"

            class _Reg(Module):
                _n = f"sort_{letter}"

                @property
                def name(self) -> str:
                    return self._n

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

                async def on_startup(
                    self, config, db, credential_store=None, blob_store=None
                ) -> None:
                    pass

                async def on_shutdown(self) -> None:
                    pass

            _Reg.__name__ = f"Module_sort_{letter}"
            registry.register(_Reg)

        names = registry.available_modules
        assert names == sorted(names), (
            "available_modules must return sorted list for determinism (RFC 0002)"
        )

    def test_load_from_config_with_unknown_module_raises(self):
        """RFC 0002: load_from_config raises ValueError for unknown module names."""
        from butlers.modules.registry import ModuleRegistry

        registry = ModuleRegistry()
        with pytest.raises(ValueError, match="[Uu]nknown"):
            registry.load_from_config({"nonexistent_module": {}})

    def test_load_from_config_with_missing_dependency_raises(self):
        """RFC 0002: load_from_config raises ValueError when dependency not in enabled set."""
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        class NeedsBase(Module):
            @property
            def name(self) -> str:
                return "needs_base_dep"

            @property
            def config_schema(self) -> type[BaseModel]:
                return BaseModel

            @property
            def dependencies(self) -> list[str]:
                return ["base_dep_missing"]

            async def register_tools(self, mcp, config, db) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
                pass

            async def on_shutdown(self) -> None:
                pass

        registry = ModuleRegistry()
        registry.register(NeedsBase)
        with pytest.raises(ValueError):
            registry.load_from_config({"needs_base_dep": {}})

    def test_load_all_includes_all_registered_modules(self):
        """RFC 0002: load_all() loads every registered module regardless of config."""
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        class AllA(Module):
            @property
            def name(self) -> str:
                return "all_module_a"

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

        class AllB(Module):
            @property
            def name(self) -> str:
                return "all_module_b"

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

        registry = ModuleRegistry()
        registry.register(AllA)
        registry.register(AllB)
        # load_all with empty config must still load both modules
        result = registry.load_all({})
        names = {m.name for m in result}
        assert "all_module_a" in names
        assert "all_module_b" in names

    def test_default_registry_discovers_built_in_modules(self):
        """RFC 0002: default_registry() auto-discovers all built-in Module subclasses."""
        from butlers.modules.registry import default_registry

        registry = default_registry()
        # Must discover at least the known built-in modules
        available = registry.available_modules
        assert len(available) >= 1, (
            "default_registry must discover at least one built-in module (RFC 0002)"
        )
