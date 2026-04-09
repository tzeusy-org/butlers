"""Dynamic loader for butler-specific job modules from the roster directory.

Roster job modules live at ``roster/<butler>/jobs/<butler>_jobs.py`` and are
not installed Python packages.  This module provides ``load_roster_jobs()``
which uses ``importlib`` to load those files so that scheduled job handlers
in ``daemon.py`` can import functions from them regardless of the working
directory or ``sys.path`` configuration (e.g. inside Docker containers).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)

# Cache: module_name -> loaded module
_MODULE_CACHE: dict[str, ModuleType] = {}


def _roster_root() -> Path:
    """Return the path to the ``roster/`` directory.

    Auto-detects from this file's location:
    ``src/butlers/jobs/_roster_loader.py`` -> 4 levels up -> repo root.
    """
    return Path(__file__).resolve().parent.parent.parent.parent / "roster"


def load_roster_jobs(butler_name: str) -> ModuleType:
    """Load and return the job module for *butler_name*.

    Loads ``roster/<butler>/jobs/<butler>_jobs.py`` and registers it in
    ``sys.modules`` as ``butlers.jobs._roster.<butler>_jobs`` so subsequent
    imports are cached.

    Parameters
    ----------
    butler_name:
        The butler identifier (e.g. ``"travel"``, ``"health"``).

    Returns
    -------
    ModuleType
        The loaded module containing job handler functions.

    Raises
    ------
    FileNotFoundError
        If the job module file does not exist.
    ImportError
        If the module cannot be loaded.
    """
    module_name = f"butlers.jobs._roster.{butler_name}_jobs"

    # Return from cache if already loaded.
    if module_name in _MODULE_CACHE:
        return _MODULE_CACHE[module_name]
    if module_name in sys.modules:
        _MODULE_CACHE[module_name] = sys.modules[module_name]
        return sys.modules[module_name]

    jobs_path = _roster_root() / butler_name / "jobs" / f"{butler_name}_jobs.py"
    if not jobs_path.exists():
        raise FileNotFoundError(f"Roster job module not found: {jobs_path}")

    spec = importlib.util.spec_from_file_location(module_name, jobs_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {jobs_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE[module_name] = module
    logger.debug("Loaded roster job module: %s from %s", module_name, jobs_path)
    return module
