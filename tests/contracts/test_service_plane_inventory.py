"""Every production ``route`` caller is classified (RFC 0038 D7, bu-2c1g7v).

The scan finds calls that reach another butler through the generic route
primitive: ``<client>.call_tool("route", ...)`` and direct calls of the route
helpers. It compares the enclosing (path, qualified function) pairs with
``butlers.core.service_plane.inventory.ROUTE_CALLERS`` in both directions.
"""

from __future__ import annotations

import ast
from pathlib import Path

from butlers.core.service_plane.inventory import ROUTE_CALLERS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTE_HELPERS = frozenset(
    {"route", "_route", "_switchboard_route", "dispatch_via_switchboard_route"}
)
# Files that RFC 0038's deletion inventory section 1.1 removes whole; their
# route calls die with them and are not survivors to classify.
_DELETED_WHOLE = frozenset(
    {
        "src/butlers/core_tools/_delegation.py",
        "src/butlers/core_tools/_switchboard_route_dispatch.py",
        "roster/switchboard/tools/routing/route.py",
    }
)


def _production_files() -> list[Path]:
    files = sorted((_REPO_ROOT / "src" / "butlers").rglob("*.py"))
    for path in sorted((_REPO_ROOT / "roster").rglob("*.py")):
        if "tests" not in path.relative_to(_REPO_ROOT).parts:
            files.append(path)
    return files


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_route_call(node: ast.Call) -> bool:
    callee = _callee(node)
    if callee == "call_tool":
        first = node.args[0] if node.args else None
        return isinstance(first, ast.Constant) and first.value == "route"
    # A bare ``registry.route()`` builds an HTTP route; every route helper takes arguments.
    return callee in _ROUTE_HELPERS and bool(node.args or node.keywords)


def _route_sites(tree: ast.AST) -> set[str]:
    sites: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Call) and _is_route_call(child) and scope:
                sites.add(".".join(scope))
            visit(child, scope)

    visit(tree, ())
    return sites


def _scanned_callers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in _production_files():
        relative = path.relative_to(_REPO_ROOT).as_posix()
        if relative in _DELETED_WHOLE:
            continue
        for function in _route_sites(ast.parse(path.read_text(encoding="utf-8"))):
            found.add((relative, function))
    return found


def test_every_route_caller_is_classified_and_every_entry_still_exists() -> None:
    inventoried = {(caller.path, caller.function) for caller in ROUTE_CALLERS}
    scanned = _scanned_callers()

    assert sorted(scanned - inventoried) == [], "unclassified route caller"
    assert sorted(inventoried - scanned) == [], "inventory names a site that no longer calls route"


def test_inventory_rows_are_unique_and_each_says_what_it_becomes() -> None:
    keys = [(caller.path, caller.function) for caller in ROUTE_CALLERS]

    assert len(keys) == len(set(keys))
    assert all(caller.becomes.strip() for caller in ROUTE_CALLERS)
