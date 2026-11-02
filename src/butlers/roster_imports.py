"""Own roster execution and canonical per-process identity.

Tools use ``butlers.tools.<b>[.<child>]``, modules use
``butlers.modules._roster_<b>[.<child>]``, and jobs use
``butlers.jobs._roster.<b>_jobs`` or ``butlers.jobs._roster.<b>.<job>``.
API routers retain ``<b>_api_router``; models retain ``<b>_api_models``
(``relationship_api_models_internal`` for Relationship). The API namespace
aliases those same objects. No second file-keyed identity cache exists.

Installing the finder executes no roster body or discovery. Dotted imports use
Python's module locks and failed-import cleanup; API file loads use one reentrant
lock. Only reviewed checkout roster code is admitted, never request/DB/cwd paths.
Explicit API discovery may load caller-supplied roots under distinct cache names.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import re
import sys
import threading
from pathlib import Path
from types import ModuleType

_ROSTER = Path(__file__).resolve().parents[2] / "roster"
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_SEGMENT = re.compile(r"[a-z_][a-z0-9_]*\Z")
_FILE_LOAD_LOCK = threading.RLock()


def _roster_path(butler: str, relative: str, *, root: Path | None = None) -> Path:
    root = (_ROSTER if root is None else root).resolve()
    folder = root / butler
    candidate = (folder / relative).resolve()
    if not candidate.is_relative_to(folder):
        raise ValueError("Roster source escapes its owning directory")
    return candidate


def _load_file_module(path: Path, name: str) -> ModuleType:
    """Load an explicit API file once, refusing cache/source mismatch and partial success."""
    with _FILE_LOAD_LOCK:
        if name in sys.modules:
            module = sys.modules[name]
            if Path(module.__file__).resolve() != path.resolve():
                raise ImportError(f"Router cache source differs: {name}")
            return module
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if sys.modules.get(name) is module:
                del sys.modules[name]
            raise
        return module


def roster_module(butler: str, relpath: str, *, api_source: str | None = None) -> ModuleType:
    """Resolve a closed roster family, validating grammar/root before loading code."""
    if not _NAME.fullmatch(butler):
        raise ValueError("Invalid roster name")
    parts = relpath.split("/")
    if not all(_SEGMENT.fullmatch(part) for part in parts):
        raise ValueError("Invalid roster module path")
    family, *children = parts
    if api_source is not None and relpath != "api/models":
        raise ValueError("API caller source is only supported for adjacent models")
    if family in {"tools", "modules"}:
        prefix = (
            f"butlers.tools.{butler}" if family == "tools" else f"butlers.modules._roster_{butler}"
        )
        name = ".".join([prefix, *children])
        relative = "/".join(parts)
        package = _roster_path(butler, relative + "/__init__.py")
        source = package if package.is_file() else _roster_path(butler, relative + ".py")
    elif family == "jobs" and len(children) == 1 and children[0] != "__init__":
        job = children[0]
        if butler.endswith("_jobs"):
            raise ValueError("Ambiguous roster job name")
        name = (
            f"butlers.jobs._roster.{butler}_jobs"
            if job == butler + "_jobs"
            else f"butlers.jobs._roster.{butler}.{job}"
        )
        source = _roster_path(butler, relpath + ".py")
    elif family == "api" and children in (["router"], ["models"]):
        kind = children[0]
        name = f"{butler}_api_{kind}"
        if butler == "relationship" and kind == "models":
            name += "_internal"
        root = _ROSTER
        if api_source is not None:
            # A copied router must retain its adjacent model definitions. The
            # trusted router supplies __file__; never infer ownership by name.
            caller = Path(api_source).absolute()
            root = caller.parents[2]
            if caller != root / butler / "api" / "router.py" or not caller.is_file():
                raise ValueError("API caller source differs from its owning router")
            if caller.resolve() != _roster_path(butler, "api/router.py", root=root):
                raise ValueError("API caller source escapes its owning directory")
        source = _roster_path(butler, relpath + ".py", root=root)
        if root.resolve() != _ROSTER.resolve():
            root_key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
            name += f"_{root_key}"
        if not source.is_file():
            raise FileNotFoundError(f"Roster module not found: {source}")
        return _load_file_module(source, name)
    else:
        raise ValueError("Unsupported roster module family")
    if not source.is_file():
        raise FileNotFoundError(f"Roster module not found: {source}")
    install_roster_imports()
    module = importlib.import_module(name)
    if Path(module.__file__).resolve() != source:
        raise ImportError(f"Roster cache source differs: {name}")
    return module


class _ApiAliasLoader(importlib.abc.Loader):
    def __init__(self, butler: str, kind: str):
        self.butler = butler
        self.kind = kind

    def create_module(self, spec):
        return roster_module(self.butler, f"api/{self.kind}")

    def exec_module(self, module):
        # The alias points to an already-executed owning module, never a copy.
        pass


def _package_spec(fullname: str):
    spec = importlib.util.spec_from_loader(fullname, loader=None, is_package=True)
    spec.submodule_search_locations = []
    return spec


def _file_spec(fullname: str, butler: str, relative: str, *, allow_package: bool = True):
    package = _roster_path(butler, relative + "/__init__.py")
    if allow_package and package.is_file():
        return importlib.util.spec_from_file_location(
            fullname, package, submodule_search_locations=[str(package.parent)]
        )
    source = _roster_path(butler, relative + ".py")
    if source.is_file():
        return importlib.util.spec_from_file_location(fullname, source)
    return None


class _RosterFinder(importlib.abc.MetaPathFinder):
    _butlers_roster_finder = True

    def find_spec(self, fullname, path=None, target=None):
        module_prefix = "butlers.modules._roster_"
        if fullname.startswith(module_prefix):
            name, *children = fullname[len(module_prefix) :].split(".")
            if _NAME.fullmatch(name) and all(_SEGMENT.fullmatch(c) for c in children):
                return _file_spec(fullname, name, "/".join(["modules", *children]))
            # A real roster package has __path__: returning None here would let
            # PathFinder execute invalid children such as "not-valid.py".
            raise ModuleNotFoundError(f"Invalid roster module name: {fullname}")
        tools_prefix = "butlers.tools."
        if fullname.startswith(tools_prefix):
            # Shared src tools always belong to the normal PathFinder.
            name, *children = fullname[len(tools_prefix) :].split(".")
            roster_parent = path and any(
                Path(entry).resolve().is_relative_to(_ROSTER.resolve()) for entry in path
            )
            if roster_parent and not all(_SEGMENT.fullmatch(child) for child in children):
                raise ModuleNotFoundError(f"Invalid roster tools name: {fullname}")
            existing = importlib.machinery.PathFinder.find_spec(fullname, path)
            if existing is not None:
                # Normal traversal must not bypass the roster source fence via symlinks.
                if roster_parent:
                    name = fullname[len(tools_prefix) :].split(".")[0]
                    if existing.origin is None or not Path(
                        existing.origin
                    ).resolve().is_relative_to(_ROSTER.resolve() / name):
                        raise ValueError("Roster source escapes its owning directory")
                return None
            name, *children = fullname[len(tools_prefix) :].split(".")
            if _NAME.fullmatch(name) and all(_SEGMENT.fullmatch(c) for c in children):
                return _file_spec(fullname, name, "/".join(["tools", *children]))
            return None
        for family in ("api", "jobs"):
            prefix = f"butlers.{family}._roster"
            if fullname == prefix:
                return _package_spec(fullname)
            if not fullname.startswith(prefix + "."):
                continue
            tail = fullname[len(prefix) + 1 :].split(".")
            if not _NAME.fullmatch(tail[0]):
                return None
            if family == "jobs":
                if len(tail) == 1 and tail[0].endswith("_jobs"):
                    butler = tail[0][:-5]
                    if _NAME.fullmatch(butler) and not butler.endswith("_jobs"):
                        return _file_spec(fullname, butler, f"jobs/{tail[0]}", allow_package=False)
                elif not tail[0].endswith("_jobs"):
                    if len(tail) == 1 and _roster_path(tail[0], "jobs").is_dir():
                        return _package_spec(fullname)
                    if (
                        len(tail) == 2
                        and _SEGMENT.fullmatch(tail[1])
                        and tail[1] not in {"__init__", tail[0] + "_jobs"}
                    ):
                        return _file_spec(fullname, tail[0], f"jobs/{tail[1]}", allow_package=False)
            else:
                if len(tail) == 1 and _roster_path(tail[0], "api").is_dir():
                    return _package_spec(fullname)
                if len(tail) == 2 and tail[1] in {"router", "models"}:
                    if _roster_path(tail[0], f"api/{tail[1]}.py").is_file():
                        return importlib.util.spec_from_loader(fullname, _ApiAliasLoader(*tail))
            return None
        return None


def install_roster_imports() -> None:
    """Install once without executing modules or depending on pytest setup."""
    if not any(getattr(f, "_butlers_roster_finder", False) for f in sys.meta_path):
        sys.meta_path.insert(0, _RosterFinder())
