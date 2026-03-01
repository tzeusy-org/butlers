"""Module registry with dependency resolution via topological sort."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
from collections import deque
from pathlib import Path

import butlers.modules
from butlers.modules.base import Module

logger = logging.getLogger(__name__)


def _register_roster_modules(registry: ModuleRegistry) -> None:
    """Scan ``roster/*/modules/__init__.py`` for Module subclasses.

    Also supports legacy ``roster/*/module.py`` and ``roster/*/module/__init__.py``
    for backwards compatibility.

    Roster modules are loaded under synthetic names like
    ``butlers.modules._roster_{butler}`` so they are accessible via
    ``sys.modules`` for test imports and framework introspection.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    roster_root = repo_root / "roster"
    if not roster_root.is_dir():
        return

    for entry in sorted(roster_root.iterdir()):
        if not entry.is_dir():
            continue

        # Preferred: roster/{butler}/modules/__init__.py (package)
        modules_pkg_init = entry / "modules" / "__init__.py"
        # Legacy: roster/{butler}/module.py or module/__init__.py
        module_file = entry / "module.py"
        module_pkg_init = entry / "module" / "__init__.py"

        if modules_pkg_init.is_file():
            target, is_package = modules_pkg_init, True
        elif module_file.is_file():
            target, is_package = module_file, False
        elif module_pkg_init.is_file():
            target, is_package = module_pkg_init, True
        else:
            continue

        butler_name = entry.name
        synthetic_name = f"butlers.modules._roster_{butler_name}"

        # Skip if already loaded (e.g. from a previous default_registry() call).
        if synthetic_name in sys.modules:
            mod = sys.modules[synthetic_name]
        else:
            try:
                if is_package:
                    spec = importlib.util.spec_from_file_location(
                        synthetic_name,
                        target,
                        submodule_search_locations=[str(target.parent)],
                    )
                else:
                    spec = importlib.util.spec_from_file_location(synthetic_name, target)

                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[synthetic_name] = mod
                spec.loader.exec_module(mod)
            except Exception:
                logger.warning("Failed to load roster module: %s", butler_name, exc_info=True)
                continue

        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Module) and obj is not Module and not inspect.isabstract(obj):
                try:
                    registry.register(obj)
                except ValueError:
                    pass  # already registered


def default_registry() -> ModuleRegistry:
    """Create a ModuleRegistry pre-populated with all built-in modules.

    Discovers all concrete ``Module`` subclasses in the ``butlers.modules``
    package by walking its sub-packages and inspecting their members,
    then scans ``roster/*/module.py`` for butler-specific modules.
    """
    registry = ModuleRegistry()
    package = butlers.modules
    for importer, modname, ispkg in pkgutil.walk_packages(
        package.__path__, prefix=package.__name__ + "."
    ):
        try:
            mod = importlib.import_module(modname)
        except Exception:
            logger.debug("Skipping unimportable module: %s", modname, exc_info=True)
            continue
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Module) and obj is not Module and not inspect.isabstract(obj):
                try:
                    registry.register(obj)
                except ValueError:
                    pass  # Already registered (e.g. re-exported from __init__)
    _register_roster_modules(registry)
    return registry


class ModuleRegistry:
    """Registry for butler modules with dependency resolution.

    Modules are registered by class, then instantiated and ordered when
    a butler's configuration is loaded.  Dependency ordering uses Kahn's
    algorithm (in-degree counting) to produce a topological sort so that
    every module is initialised only after its dependencies.
    """

    def __init__(self) -> None:
        self._modules: dict[str, type[Module]] = {}

    def register(self, module_cls: type[Module]) -> None:
        """Register a module class.

        Raises ``ValueError`` if a module with the same name is already
        registered.  The module name is obtained by instantiating the class
        temporarily — this is intentional to avoid relying on class-level
        attributes that could diverge from the instance property.
        """
        # We need the name without a full instantiation dance for concrete
        # subclasses; use a temporary instance to read the property.
        instance = module_cls()
        name = instance.name
        if name in self._modules:
            raise ValueError(f"Module '{name}' is already registered")
        self._modules[name] = module_cls

    @property
    def available_modules(self) -> list[str]:
        """List all registered module names (sorted for determinism)."""
        return sorted(self._modules.keys())

    def load_from_config(self, modules_config: dict[str, dict]) -> list[Module]:
        """Instantiate and order modules from config.

        Parameters
        ----------
        modules_config:
            Mapping of module name to per-module config dict.  Only modules
            present in this mapping are enabled for the butler.

        Returns
        -------
        list[Module]
            Module instances in dependency order (dependencies first).

        Raises
        ------
        ValueError
            If *modules_config* references an unknown module, a module
            depends on a module not in the enabled set, or the dependency
            graph contains a cycle.
        """
        # 1. Validate all requested modules are registered.
        for name in modules_config:
            if name not in self._modules:
                raise ValueError(f"Unknown module: '{name}'")

        # 2. Instantiate each module.
        instances: dict[str, Module] = {}
        for name in modules_config:
            instances[name] = self._modules[name]()

        # 3. Validate all dependencies exist in the enabled set.
        for name, instance in instances.items():
            for dep in instance.dependencies:
                if dep not in instances:
                    raise ValueError(
                        f"Module '{name}' depends on '{dep}', "
                        f"which is not in the enabled module set"
                    )

        # 4. Topological sort.
        return _topological_sort(instances)

    def load_all(self, modules_config: dict[str, dict]) -> list[Module]:
        """Instantiate and order ALL registered modules.

        Every registered module is loaded regardless of whether it appears in
        *modules_config*.  Modules listed in *modules_config* receive their
        explicit config dict; modules absent from *modules_config* receive an
        empty ``{}`` dict (config validation downstream is non-fatal).

        This is the preferred startup path as of butlers-949.  It ensures that
        all modules are always present so that runtime enable/disable state can
        be managed independently of the static ``butler.toml`` config.

        Parameters
        ----------
        modules_config:
            Mapping of module name to per-module config dict, as parsed from
            ``[modules.*]`` sections in ``butler.toml``.  Acts as an optional
            config provider — its presence or absence no longer gates which
            modules are loaded.

        Returns
        -------
        list[Module]
            All registered module instances in dependency order (dependencies
            first).

        Raises
        ------
        ValueError
            If the dependency graph contains a cycle.
        """
        # Instantiate every registered module.
        instances: dict[str, Module] = {name: cls() for name, cls in self._modules.items()}

        # Log modules that have explicit config vs. those getting empty dict.
        configured = set(modules_config) & set(instances)
        unconfigured = set(instances) - set(modules_config)
        if unconfigured:
            logger.debug(
                "load_all: %d module(s) loaded without explicit config (will use {}): %s",
                len(unconfigured),
                sorted(unconfigured),
            )
        if configured:
            logger.debug(
                "load_all: %d module(s) loaded with explicit config: %s",
                len(configured),
                sorted(configured),
            )

        # Topological sort across the full registered set.
        return _topological_sort(instances)


def _topological_sort(instances: dict[str, Module]) -> list[Module]:
    """Return *instances* in dependency order using Kahn's algorithm.

    Parameters
    ----------
    instances:
        All module instances that should be sorted.  Every dependency name
        declared by any instance must appear as a key in *instances*.

    Returns
    -------
    list[Module]
        Modules ordered so that each module's dependencies appear before it.

    Raises
    ------
    ValueError
        If a cycle is detected or a dependency is missing from *instances*.
    """
    # Validate all dependency references resolve within the instance set.
    for name, instance in instances.items():
        for dep in instance.dependencies:
            if dep not in instances:
                raise ValueError(
                    f"Module '{name}' depends on '{dep}', which is not in the loaded module set"
                )

    in_degree: dict[str, int] = {name: 0 for name in instances}
    # Build adjacency: edge from dep -> dependent (dep must come first).
    adjacency: dict[str, list[str]] = {name: [] for name in instances}
    for name, instance in instances.items():
        for dep in instance.dependencies:
            adjacency[dep].append(name)
            in_degree[name] += 1

    queue: deque[str] = deque()
    for name, degree in in_degree.items():
        if degree == 0:
            queue.append(name)

    sorted_names: list[str] = []
    while queue:
        # Sort the current zero-degree batch for deterministic output.
        batch = sorted(queue)
        queue.clear()
        for node in batch:
            sorted_names.append(node)
            for neighbour in adjacency[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

    if len(sorted_names) != len(instances):
        # Some modules remain with non-zero in-degree — cycle detected.
        remaining = set(instances) - set(sorted_names)
        raise ValueError(f"Circular dependency detected among modules: {sorted(remaining)}")

    return [instances[name] for name in sorted_names]
