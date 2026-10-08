"""Resolve supported roster namespaces on demand from this checkout.

Installing the finder executes no roster body and performs no discovery or
registration. Python's import locks and ordinary package loaders own requested
module identity, execution and failed-import cleanup.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

_ROSTER = Path(__file__).resolve().parents[2] / "roster"
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


class _ApiAliasLoader(importlib.abc.Loader):
    def __init__(self, butler: str, kind: str):
        self.butler = butler
        self.kind = kind

    def create_module(self, spec):
        from butlers.api.router_discovery import _load_router_module

        router = _load_router_module(
            _ROSTER / self.butler / "api" / "router.py", f"{self.butler}_api_router"
        )
        if self.kind == "router":
            return router
        models = getattr(router, "_models_module", None) or getattr(router, "_models", None)
        if not isinstance(models, ModuleType):
            raise ImportError(f"Roster API has no local models module: {self.butler}")
        return models

    def exec_module(self, module):
        # create_module returns the already-executed owning module, not a copy.
        pass


class _RosterFinder(importlib.abc.MetaPathFinder):
    _butlers_roster_finder = True

    def find_spec(self, fullname, path=None, target=None):
        module_prefix = "butlers.modules._roster_"
        if fullname.startswith(module_prefix):
            # Child imports are handled by the real package's __path__.
            name = fullname[len(module_prefix) :]
            if not _NAME.fullmatch(name):
                return None
            init = _ROSTER / name / "modules" / "__init__.py"
            if init.is_file():
                return importlib.util.spec_from_file_location(
                    fullname, init, submodule_search_locations=[str(init.parent)]
                )
            return None
        for family in ("api", "jobs"):
            prefix = f"butlers.{family}._roster"
            if fullname == prefix:
                spec = importlib.util.spec_from_loader(fullname, loader=None, is_package=True)
                spec.submodule_search_locations = []
                return spec
            if not fullname.startswith(prefix + "."):
                continue
            tail = fullname[len(prefix) + 1 :].split(".")
            if family == "jobs" and len(tail) == 1 and tail[0].endswith("_jobs"):
                name = tail[0][:-5]
                if _NAME.fullmatch(name):
                    file = _ROSTER / name / "jobs" / f"{name}_jobs.py"
                    if file.is_file():
                        return importlib.util.spec_from_file_location(fullname, file)
            elif family == "api" and _NAME.fullmatch(tail[0]):
                folder = _ROSTER / tail[0] / "api"
                if len(tail) == 1 and folder.is_dir():
                    spec = importlib.util.spec_from_loader(fullname, loader=None, is_package=True)
                    # No unrestricted PathFinder fallback: supported children are fixed below.
                    spec.submodule_search_locations = []
                    return spec
                if len(tail) == 2 and tail[1] in {"router", "models"}:
                    if (folder / "router.py").is_file():
                        return importlib.util.spec_from_loader(
                            fullname, _ApiAliasLoader(tail[0], tail[1])
                        )
            return None
        return None


def install_roster_imports() -> None:
    """Install once without executing modules or depending on pytest setup."""
    if not any(getattr(f, "_butlers_roster_finder", False) for f in sys.meta_path):
        sys.meta_path.insert(0, _RosterFinder())
