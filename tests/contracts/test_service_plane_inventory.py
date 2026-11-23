"""Every production ``route`` caller is classified (RFC 0038 D7, bu-2c1g7v).

The scan finds every place that can reach another butler through the generic
route primitive and compares the enclosing (path, qualified function) pairs
with ``butlers.core.service_plane.inventory.ROUTE_CALLERS`` in both directions.

A site is:

- ``<client>.call_tool("route", ...)``, positionally or as ``name=``/``tool_name=``;
- any reference (a call, ``call_fn=route``, ``partial(route, ...)``, a stored
  alias) to a name bound to a route function in that file: an ``import`` of
  ``route``/``dispatch_via_switchboard_route`` from a routing module under any
  ``as`` alias, and every name assigned from such a name or a ``partial`` of it;
- a call with arguments to one of the conventional helper names, for files
  that reach route() without importing it by name.

Files that RFC 0038's deletion inventory section 1.1 removes whole are read
from that table, not listed here: their sites die with them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from butlers.core.service_plane.inventory import ROUTE_CALLERS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DELETION_INVENTORY = (
    _REPO_ROOT / "openspec/changes/generalist-lead-focused-delegation/deletion-inventory.md"
)
_ROUTE_HELPERS = frozenset(
    {"route", "_route", "_switchboard_route", "dispatch_via_switchboard_route"}
)
_ROUTE_EXPORTS = frozenset({"route", "dispatch_via_switchboard_route"})
_ROUTE_MODULES = re.compile(r"(^|\.)(routing\.route|routing|_switchboard_route_dispatch)$")
_PARTIALS = frozenset({"partial"})


def _deleted_whole() -> frozenset[str]:
    """Paths in the deletion inventory's "1.1 Whole modules (REMOVE)" table."""
    text = _DELETION_INVENTORY.read_text(encoding="utf-8")
    section = text.split("### 1.1", 1)[1].split("\n### ", 1)[0]
    return frozenset(re.findall(r"^\| `([^`]+\.py)` \|", section, flags=re.MULTILINE))


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


def _is_partial_of(node: ast.expr, names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and _callee(node) in _PARTIALS
        and bool(node.args)
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in names
    )


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own_nodes(scope: ast.AST):
    """Nodes in this scope, not descending into nested functions or classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _stored_names(node: ast.AST) -> set[str]:
    return {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }


def _route_names(scope: ast.AST, inherited: frozenset[str] = frozenset()) -> set[str]:
    """Names bound to a route function in this scope, following aliases to a fixed point.

    A scope inherits its enclosing scope's route names unless it rebinds them
    to something else (a parameter, a loop variable, a plain assignment).
    """
    names = set(inherited)
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        arguments = scope.args
        params = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        params += [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
        names -= {a.arg for a in params}
    own = list(_own_nodes(scope))
    for node in own:
        if isinstance(node, ast.ImportFrom) and _ROUTE_MODULES.search(node.module or ""):
            names.update(a.asname or a.name for a in node.names if a.name in _ROUTE_EXPORTS)
    rebound: set[str] = set()
    for node in own:
        if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            rebound |= _stored_names(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            rebound |= _stored_names(node.optional_vars)
    while True:
        before = len(names)
        for node in own:
            value = getattr(node, "value", None)
            if not isinstance(node, ast.Assign | ast.AnnAssign) or value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            stored = set().union(*(_stored_names(t) for t in targets))
            if (isinstance(value, ast.Name) and value.id in names) or _is_partial_of(value, names):
                names |= stored
            else:
                rebound |= stored
        if len(names) == before:
            break
    return names - (rebound - _aliases_only(own, names))


def _aliases_only(own: list[ast.AST], names: set[str]) -> set[str]:
    """Names that some assignment in this scope binds to a route function."""
    bound: set[str] = set()
    for node in own:
        value = getattr(node, "value", None)
        if isinstance(node, ast.Assign | ast.AnnAssign) and value is not None:
            if (isinstance(value, ast.Name) and value.id in names) or _is_partial_of(value, names):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bound |= set().union(*(_stored_names(t) for t in targets))
    return bound


def _is_route_call_tool(node: ast.Call) -> bool:
    if _callee(node) != "call_tool":
        return False
    first = node.args[0] if node.args else None
    named = [k.value for k in node.keywords if k.arg in {"name", "tool_name"}]
    return any(isinstance(v, ast.Constant) and v.value == "route" for v in [first, *named])


def _route_sites(tree: ast.AST) -> set[str]:
    sites: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...], names: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, (*scope, child.name), frozenset(_route_names(child, names)))
                continue
            if isinstance(child, ast.Lambda):
                visit(child, scope, frozenset(_route_names(child, names)))
                continue
            here = ".".join(scope) or "<module>"
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id in names:
                    sites.add(here)
            elif isinstance(child, ast.Call) and (
                _is_route_call_tool(child)
                # A bare ``registry.route()`` builds an HTTP route; every helper takes arguments.
                or (_callee(child) in _ROUTE_HELPERS and bool(child.args or child.keywords))
            ):
                sites.add(here)
            visit(child, scope, names)

    visit(tree, (), frozenset(_route_names(tree)))
    return sites


def _scanned_callers() -> set[tuple[str, str]]:
    deleted = _deleted_whole()
    found: set[tuple[str, str]] = set()
    for path in _production_files():
        relative = path.relative_to(_REPO_ROOT).as_posix()
        if relative in deleted:
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


def test_the_deleted_whole_set_comes_from_the_deletion_inventory() -> None:
    deleted = _deleted_whole()

    assert "src/butlers/modules/pipeline.py" in deleted
    assert "roster/switchboard/tools/routing/correct_route.py" in deleted
    assert "src/butlers/core_tools/_domain_events.py" not in deleted


_ALIASED = """
import functools
from butlers.tools.switchboard.routing.route import route as _fresh_alias
from butlers.core_tools._switchboard_route_dispatch import dispatch_via_switchboard_route as d

bound = functools.partial(_fresh_alias, None)
again = bound

async def direct():
    await _fresh_alias(None, "x", "t", {})

async def through_partial():
    await again("x", "t", {})

async def passed_as_call_fn(owner):
    await owner.run(call_fn=_fresh_alias)

async def dispatch_alias(client):
    await d(client, None, "b", target_butler="x", tool_name="t", args={})

async def keyword_call_tool(client):
    await client.call_tool(name="route", arguments={})

async def unrelated(app):
    for route in app.routes:
        print(route)
    return app.router.route()

async def imports_inside_a_function():
    from butlers.tools.switchboard.routing.route import route

    async def nested():
        await route(None, "x", "t", {})

    return nested

async def shadowed_by_a_parameter(_fresh_alias):
    return _fresh_alias

async def shadowed_by_a_loop(app):
    for _fresh_alias in app.routes:
        print(_fresh_alias)
"""


def test_aliases_partials_call_fn_and_keyword_call_tool_are_all_caught() -> None:
    sites = _route_sites(ast.parse(_ALIASED))

    assert sites == {
        "<module>",
        "direct",
        "through_partial",
        "passed_as_call_fn",
        "dispatch_alias",
        "keyword_call_tool",
        "imports_inside_a_function.nested",
    }
