"""Auto-discovery and loading for roster butler API routers.

Scans roster/{butler}/api/router.py files and dynamically loads them
via importlib. Each loaded router module must export a module-level
'router' variable (APIRouter instance).

Usage:
    from butlers.api.router_discovery import discover_butler_routers

    routers = discover_butler_routers()
    for butler_name, router_module in routers:
        app.include_router(router_module.router)
"""

import hashlib
import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Default roster location relative to the repository root
_ROUTER_LOAD_LOCK = threading.RLock()

_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[3] / "roster"


def _load_router_module(router_path: Path, module_name: str) -> ModuleType:
    """Load a Python module from a file path using importlib.

    If the module is already loaded in sys.modules, returns the existing
    module to ensure consistency across test and app initialization.

    Parameters
    ----------
    router_path:
        Path to the router.py file.
    module_name:
        Unique module name for sys.modules registration.

    Returns
    -------
    ModuleType
        The loaded module.

    Raises
    ------
    ValueError
        If the module spec cannot be loaded.
    """
    with _ROUTER_LOAD_LOCK:
        if module_name in sys.modules:
            module = sys.modules[module_name]
            if Path(module.__file__).resolve() != router_path.resolve():
                raise ImportError(f"Router cache source differs: {module_name}")
            return module
        spec = importlib.util.spec_from_file_location(module_name, router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if sys.modules.get(module_name) is module:
                del sys.modules[module_name]
            raise
        return module


def discover_butler_routers(
    roster_dir: Path | None = None,
) -> list[tuple[str, ModuleType]]:
    """Discover and load all roster/{butler}/api/router.py files.

    Scans the roster directory for butler subdirectories containing
    api/router.py files. Each file is loaded dynamically via importlib
    and validated to ensure it exports a 'router' variable that is an
    APIRouter instance.

    Butlers without api/ directories are silently skipped.
    Butlers with api/router.py but invalid router exports are logged
    as warnings and skipped.

    Parameters
    ----------
    roster_dir:
        Path to the roster directory. Defaults to <repo>/roster/.

    Returns
    -------
    list[tuple[str, ModuleType]]
        List of (butler_name, router_module) tuples, sorted by butler name.
        Each router_module has a 'router' attribute (APIRouter instance).
    """
    if roster_dir is None:
        roster_dir = _DEFAULT_ROSTER_DIR

    if not roster_dir.is_dir():
        logger.warning("Roster directory not found: %s", roster_dir)
        return []

    routers: list[tuple[str, ModuleType]] = []

    for butler_dir in sorted(roster_dir.iterdir()):
        if not butler_dir.is_dir():
            continue

        butler_name = butler_dir.name
        router_path = butler_dir / "api" / "router.py"

        if not router_path.exists():
            # Silently skip butlers without api/ directories
            continue

        module_name = f"{butler_name}_api_router"
        if roster_dir.resolve() != _DEFAULT_ROSTER_DIR.resolve():
            root_key = hashlib.sha256(str(roster_dir.resolve()).encode()).hexdigest()[:12]
            module_name += f"_{root_key}"

        try:
            module = _load_router_module(router_path, module_name)
        except Exception as exc:
            logger.warning(
                "Failed to load router module for butler '%s': %s",
                butler_name,
                exc,
                exc_info=True,
            )
            continue

        # Validate that the module exports a 'router' variable
        if not hasattr(module, "router"):
            logger.warning(
                "Router module %s does not export 'router' variable",
                router_path,
            )
            continue

        if not isinstance(module.router, APIRouter):
            logger.warning(
                "Router module %s exports 'router' but it is not an APIRouter instance (got %s)",
                router_path,
                type(module.router).__name__,
            )
            continue

        routers.append((butler_name, module))
        logger.info("Discovered butler router: %s from %s", butler_name, router_path)

    logger.info("Discovered %d butler router(s) from %s", len(routers), roster_dir)
    return routers
