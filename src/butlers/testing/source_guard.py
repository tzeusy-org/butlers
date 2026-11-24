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
import copy
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path


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


def python_files(
    globs: Iterable[str], exclude: Iterable[str] = (), *, root: Path = Path(".")
) -> tuple[Path, ...]:
    """Resolve a deterministic Python scope; an empty effective scope is an error."""
    omitted = {path for pattern in exclude for path in root.glob(pattern)}
    paths = {
        path
        for pattern in globs
        for path in root.glob(pattern)
        if path.is_file() and path.suffix == ".py" and path not in omitted
    }
    if not paths:
        raise ValueError("empty Python source scope")
    return tuple(sorted(paths))


@lru_cache(maxsize=128)
def _parse_source(path: str, source: str) -> ast.Module:
    return ast.parse(source, filename=path)


def parse_file(path: Path) -> ast.Module:
    """Cache by path and content. Treat the result as read-only.

    Transformations such as :func:`strip_docstrings` take their own copy.
    Including content in the key keeps scratch-file mutations visible.
    """
    return _parse_source(str(path), path.read_text(encoding="utf-8"))


def strip_docstrings(tree: ast.Module) -> ast.Module:
    """Copy a tree without module, class or function docstrings. Never mutate input."""
    result = copy.deepcopy(tree)
    for node in ast.walk(result):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return result


def calls_with_owner(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    """Yield calls with lexical class/function owners, including async/nested bodies.

    Decorators, bases, defaults and annotations belong to the enclosing owner.
    A module-level call has owner ``<module>``.
    """

    def walk(node: ast.AST, owner: str) -> Iterator[tuple[str, ast.Call]]:
        if isinstance(node, ast.Call):
            yield owner, node
        declaration = isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        child_owner = f"{owner}.{node.name}" if declaration and owner else ""
        if declaration and not owner:
            child_owner = node.name
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, ast.AST):
                    yield from walk(
                        child, child_owner if declaration and field == "body" else owner
                    )

    for owner, call in walk(tree, ""):
        yield owner or "<module>", call


def outermost_matches[T](
    node: ast.AST, extract: Callable[[ast.AST], tuple[T, ...] | None]
) -> Iterator[T]:
    """Yield matches in AST preorder, pruning handled subtrees.

    An extractor returns None to visit children, an empty tuple to skip the
    subtree, or the matches for a subtree already handled as a whole.
    """
    matches = extract(node)
    if matches is not None:
        yield from matches
        return
    for child in ast.iter_child_nodes(node):
        yield from outermost_matches(child, extract)


def sql_literals(tree: ast.Module) -> Iterator[str]:
    """Yield maximal SQL-verb literals, folding ``+`` and f-string static parts.

    Dynamic expressions become ``{...}``; they are never evaluated. Comments
    and docstrings are excluded. This is extraction, not SQL validation.
    """

    def fold(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(fold(value) or "{...}" for value in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = fold(node.left), fold(node.right)
            if left is not None or right is not None:
                return (left or "{...}") + (right or "{...}")
        return None

    def extract(node: ast.AST) -> tuple[str, ...] | None:
        value = fold(node)
        if value is None:
            return None
        if re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b", value, re.I):
            return (value,)
        return ()

    yield from outermost_matches(strip_docstrings(tree), extract)


def imported_module_names(tree: ast.Module) -> Iterator[str]:
    """Yield module names from explicit imports in AST walk order.

    Ignore aliases and relative levels. A from-import with no module name
    yields nothing.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def import_edges(tree: ast.Module) -> Iterator[tuple[int, str, bool]]:
    """Yield import sites and TYPE_CHECKING context, preserving relative prefixes.

    Recognizes ``typing`` and ``TYPE_CHECKING`` import aliases. Only the true
    arm is type-only; else imports remain runtime edges.
    """
    typing_names = {"typing"}
    checking_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            typing_names.update(a.asname or a.name for a in node.names if a.name == "typing")
        elif isinstance(node, ast.ImportFrom) and node.module == "typing":
            checking_names.update(
                a.asname or a.name for a in node.names if a.name == "TYPE_CHECKING"
            )

    def is_check(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id in checking_names
            or isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id in typing_names
        )

    def walk(node: ast.AST, type_only: bool = False) -> Iterator[tuple[int, str, bool]]:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, type_only
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, "." * node.level + (node.module or ""), type_only
        if isinstance(node, ast.If):
            for child in node.body:
                yield from walk(child, type_only or is_check(node.test))
            for child in node.orelse:
                yield from walk(child, type_only)
        else:
            for child in ast.iter_child_nodes(node):
                yield from walk(child, type_only)

    yield from walk(tree)


@dataclass(frozen=True, order=True)
class Violation:
    path: Path
    line: int
    rule: str
    detail: str


def format_violations(violations: Iterable[Violation]) -> str:
    """Render stable, editor-clickable diagnostics."""
    return "\n".join(f"{v.path}:{v.line}: [{v.rule}] {v.detail}" for v in sorted(violations))
