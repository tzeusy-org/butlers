"""Reusable AST primitives for guard tests that scan the repository's own source.

Some defects are invisible to a normal test because the wrong code *runs
correctly* — a migration head assertion that spells today's revision, a test
that hands the wall clock to a function whose answer depends on the wall clock.
Nothing fails until the world moves. The only detector that works is a scan of
the source for the shape itself.

Every such guard needs the same four things, and they are subtle enough that
two copies would drift:

- walk a scope without crossing into nested functions, so a name means what it
  means *here* (:func:`scopes`, :func:`scope_nodes`),
- resolve one level of local binding, so the two-statement form
  (``now = datetime.now(UTC)`` then ``f(now=now)``) is seen as the one-statement
  form (:func:`local_bindings`),
- find the statement an expression belongs to, because that is where a reader
  looks and where a comment can live (:func:`parent_map`,
  :func:`enclosing_statement`),
- read a declared-exception pragma off that statement, with the reason
  mandatory (:func:`pragma_declaration`).

The mandatory reason is the point of the escape hatch, not a formality: these
guards fire on shapes that are *sometimes* correct, and the author is the only
one who knows which case this is. A bare marker would let the guard be silenced
without anyone articulating that.
"""

from __future__ import annotations

import ast
import re
from functools import cache


def scopes(tree: ast.Module) -> list[ast.AST]:
    """The module plus every function body, so local bindings stay scoped."""
    return [tree] + [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node belonging to *scope*, without descending into nested functions.

    Nested functions are separate scopes with their own bindings, and visiting
    them once each (rather than again for every enclosing scope) keeps a
    whole-repo sweep linear in the size of the tree.
    """
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def local_bindings(nodes: list[ast.AST]) -> dict[str, ast.AST]:
    """Map simple ``name = <expr>`` assignments among *nodes* to their value nodes."""
    bindings: dict[str, ast.AST] = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
    return bindings


def parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Child-node id to parent node, for walking back up to a statement."""
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def enclosing_statement(node: ast.AST, parents: dict[int, ast.AST]) -> ast.stmt:
    """The statement *node* belongs to — where a reader looks, and comments."""
    current: ast.AST | None = node
    while current is not None and not isinstance(current, ast.stmt):
        current = parents.get(id(current))
    assert isinstance(current, ast.stmt)
    return current


def string_constants(node: ast.AST) -> list[str]:
    """Every string constant anywhere inside *node*, f-string parts included."""
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


@cache
def _declaration_pattern(pragma: str) -> re.Pattern[str]:
    return re.compile(rf"#.*{re.escape(pragma)}\s*(?P<reason>\S.*)")


def pragma_declaration(
    statement: ast.stmt, lines: list[str], pragma: str
) -> tuple[bool, str | None]:
    """Find a declared exception on, or in the comment block above, *statement*.

    Returns ``(marker_present, reason)``. A marker with no reason after the
    colon does not excuse the shape, and is reported differently so the author
    is told what is missing rather than just that the guard fired.
    """
    start = statement.lineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    end = statement.end_lineno or statement.lineno
    span = lines[start:end]
    for line in span:
        match = _declaration_pattern(pragma).search(line)
        if match is not None:
            return True, match.group("reason").strip()
    return any(pragma in line for line in span), None
