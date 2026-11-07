#!/usr/bin/env python3
"""Ratchet patched daemon boots out of the suite (bu-rvgzmf.2).

A test whose subject is the boot uses ``booted_daemon``
(``src/butlers/testing/daemon.py``): the real lifecycle on a migrated clone with
only the LLM runtime faked. A test whose subject is one tool or one phase calls it
on a ``tool_host`` (``src/butlers/testing/tool_host.py``). Neither patches the
lifecycle, so a test file must not:

- patch a ``butlers.lifecycle.*`` target (``patch("butlers.lifecycle.X")``,
  ``monkeypatch.setattr("butlers.lifecycle.X", ...)``),
- patch an attribute of the lifecycle module object
  (``monkeypatch.setattr(lifecycle, "X", ...)``, ``patch.object(lifecycle, "X")``)
  where the name is bound, in the call's scope, by ``import butlers.lifecycle as
  <name>``, ``from butlers import lifecycle [as <name>]`` or ``import
  butlers.lifecycle`` (``butlers.lifecycle``), or
- patch a private ``ButlerDaemon`` method (``patch.object(ButlerDaemon, "_x")``,
  ``monkeypatch.setattr(ButlerDaemon, "_x", ...)``).

Existing patches are frozen per file, as counts, in
``scripts/test-boot-patches-baseline.json``. A file may hold at most its count;
files not listed hold none. ``--update-baseline`` lowers counts and never raises
them.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "scripts" / "test-boot-patches-baseline.json"
LIFECYCLE_PREFIX = "butlers.lifecycle."
PATCHERS = {"patch", "setattr"}


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    if not path.endswith(".py"):
        return False
    if parts[-1] == "conftest.py" and not path.startswith("src/"):
        return True
    return parts[0] == "tests" or (parts[0] == "roster" and "tests" in parts[1:-1])


LIFECYCLE_MODULE = "butlers.lifecycle"
OBJECT_PATCHERS = {"setattr", "object", "multiple"}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# What an import binds a name to: the lifecycle module, the ``butlers`` package
# (so ``butlers.lifecycle`` reaches it), or anything else.
_LIFECYCLE, _PACKAGE, _OTHER = "lifecycle", "package", "other"


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _str_arg(node: ast.Call, index: int) -> str | None:
    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
        value = node.args[index].value
        return value if isinstance(value, str) else None
    return None


def _own_nodes(scope: ast.AST):
    """Nodes in *scope*, not descending into nested functions, lambdas or classes."""
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _SCOPES):
            pending.extend(ast.iter_child_nodes(node))


def _bindings(scope: ast.AST) -> dict[str, str]:
    """Each name *scope* binds, and what to. A lifecycle import wins over others."""
    found: dict[str, set[str]] = {}

    def bind(name: str, kind: str) -> None:
        found.setdefault(name, set()).add(kind)

    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None:
                bind(arg.arg, _OTHER)
    for node in _own_nodes(scope):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bind(alias.asname, _LIFECYCLE if alias.name == LIFECYCLE_MODULE else _OTHER)
                else:
                    root = alias.name.split(".")[0]
                    bind(root, _PACKAGE if root == "butlers" else _OTHER)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                is_lifecycle = node.level == 0 and (
                    f"{node.module}.{alias.name}" == LIFECYCLE_MODULE
                )
                bind(alias.asname or alias.name, _LIFECYCLE if is_lifecycle else _OTHER)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bind(node.id, _OTHER)
        elif isinstance(node, _SCOPES) and not isinstance(node, ast.Lambda):
            bind(node.name, _OTHER)
    order = (_LIFECYCLE, _PACKAGE, _OTHER)
    return {name: next(kind for kind in order if kind in kinds) for name, kinds in found.items()}


def _resolve(name: str, chain: list[dict[str, str]]) -> str | None:
    for bindings in reversed(chain):
        if name in bindings:
            return bindings[name]
    return None


def _is_lifecycle(node: ast.expr, chain: list[dict[str, str]]) -> bool:
    if isinstance(node, ast.Name):
        return _resolve(node.id, chain) == _LIFECYCLE
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "lifecycle"
        and isinstance(node.value, ast.Name)
        and node.value.id == "butlers"
        and _resolve("butlers", chain) == _PACKAGE
    )


def _is_patch(node: ast.Call, chain: list[dict[str, str]]) -> bool:
    callee = _callee(node)
    if callee in PATCHERS and (_str_arg(node, 0) or "").startswith(LIFECYCLE_PREFIX):
        return True
    if callee not in OBJECT_PATCHERS or not node.args:
        return False
    target = node.args[0]
    if _is_lifecycle(target, chain):
        return True
    return (
        callee in {"object", "setattr"}
        and isinstance(target, ast.Name)
        and target.id == "ButlerDaemon"
        and (_str_arg(node, 1) or "").startswith("_")
    )


def boot_patches(source: str) -> int:
    """How many lifecycle-phase patches *source* holds."""

    def count(scope: ast.AST, enclosing: list[dict[str, str]]) -> int:
        chain = [*enclosing, _bindings(scope)]
        # A class body's names are not visible inside its methods.
        inner = enclosing if isinstance(scope, ast.ClassDef) else chain
        total = 0
        for node in _own_nodes(scope):
            if isinstance(node, ast.Call) and _is_patch(node, chain):
                total += 1
            elif isinstance(node, _SCOPES):
                total += count(node, inner)
        return total

    return count(ast.parse(source), [])


def tracked_test_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    return sorted(path for path in output.split("\0") if path and is_test_file(path))


def scan(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in tracked_test_files(root):
        file = root / path
        if file.is_file() and (count := boot_patches(file.read_text(encoding="utf-8"))):
            found[path] = count
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--update-baseline", action="store_true", help="lower frozen counts")
    args = parser.parse_args(argv)
    found = scan(args.root)
    baseline: dict[str, int] = (
        json.loads(args.baseline.read_text()) if args.baseline.is_file() else {}
    )
    violations = [
        f"{path}: {count} patched lifecycle target(s), {baseline.get(path, 0)} allowed"
        for path, count in found.items()
        if count > baseline.get(path, 0)
    ]
    stale = sum(
        count - found.get(path, 0) for path, count in baseline.items() if count > found.get(path, 0)
    )
    if args.update_baseline:
        kept = {
            path: min(count, found[path]) for path, count in baseline.items() if found.get(path)
        }
        args.baseline.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {len(kept)} file entr(ies) to {args.baseline}")
    if violations:
        print("New patched daemon boots (use booted_daemon, or a phase on tool_host):")
        for line in violations:
            print(f"  {line}")
        return 1
    if stale and not args.update_baseline:
        print(f"Ratchet can be tightened by {stale} patch(es): run with --update-baseline.")
    total = sum(found.values())
    print(f"No new patched boots ({total} frozen in {len(found)} file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
