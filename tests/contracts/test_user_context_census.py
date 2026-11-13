"""Contract: every legacy ``public.user_context`` reader/writer and owner-zone literal has a row.

bu-dhloi1.21 AC4: ``public.user_context`` retires only when every reader and
writer has a named successor or an explicitly deferred owner, and deletion needs
shadow/parity receipts, not an empty search. The census is
``docs/data_and_storage/user-context-census.md``; this guard keeps it honest in
both directions.

* ``user_context``: every function (or module) in ``src/`` and ``roster/`` that
  names ``public.user_context`` in code (docstrings excluded), carries the bare
  key ``"user_context"`` (the grants map, and the audit namesake that is not this
  table), or imports ``butlers.context_bus`` has exactly one row, and every row
  still has such a reference.
* ``owner_zone``: every non-docstring ``"Asia/Singapore"`` literal outside tests
  and migrations has a row, and every row still has one. The clock module's
  legacy fallback is listed through its row like any other site.
* ``feeder``: every test a feeder row points at exists.
* ``switch``: the switch-time obligations, including the durable parity receipt
  store, are named rows.

A new reference fails until it is classified; a stale row fails too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CENSUS = _REPO_ROOT / "docs" / "data_and_storage" / "user-context-census.md"
_SCAN_ROOTS = (_REPO_ROOT / "src", _REPO_ROOT / "roster")
_TABLE_RE = re.compile(r"<!-- census:(?P<name>[a-z_]+) -->\n(?P<body>.*?)<!-- /census -->", re.S)
_KEY_RE = re.compile(r"^\|\s*`(?P<key>[^`]+)`\s*\|")
_CONTEXT_BUS = "butlers.context_bus"


def _tables() -> dict[str, set[str]]:
    text = _CENSUS.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in _TABLE_RE.finditer(text):
        keys = [m["key"] for line in match["body"].splitlines() if (m := _KEY_RE.match(line))]
        assert len(keys) == len(set(keys)), f"duplicate rows in census table {match['name']}"
        tables[match["name"]] = set(keys)
    return tables


def _python_files(*, skip_tests_and_migrations: bool) -> list[Path]:
    files = []
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(_REPO_ROOT).parts
            if skip_tests_and_migrations and (
                "tests" in parts or "migrations" in parts or path.name.startswith("test_")
            ):
                continue
            files.append(path)
    return files


class _Scanner(ast.NodeVisitor):
    """Collect enclosing qualnames of matching references, docstrings skipped."""

    def __init__(self, literal: str, *, imports: bool, exact: str | None = None) -> None:
        self.literal = literal
        self.imports = imports
        self.exact = exact
        self.stack: list[str] = []
        self.docstrings: set[int] = set()
        self.hits: set[str] = set()

    def _hit(self) -> None:
        self.hits.add(".".join(self.stack) or "<module>")

    def _scope(self, node: ast.AST, name: str | None) -> None:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.docstrings.add(id(body[0].value))
        if name is not None:
            self.stack.append(name)
        self.generic_visit(node)
        if name is not None:
            self.stack.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._scope(node, None)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope(node, node.name)

    def visit_Constant(self, node: ast.Constant) -> None:
        if (
            isinstance(node.value, str)
            and (self.literal in node.value or node.value == self.exact)
            and id(node) not in self.docstrings
        ):
            self._hit()

    def visit_Import(self, node: ast.Import) -> None:
        if self.imports and any(alias.name == _CONTEXT_BUS for alias in node.names):
            self._hit()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self.imports:
            return
        if node.module == _CONTEXT_BUS or (
            node.module == "butlers" and any(alias.name == "context_bus" for alias in node.names)
        ):
            self._hit()


def _references(
    literal: str, *, imports: bool, skip_tests_and_migrations: bool, exact: str | None = None
) -> set[str]:
    found: set[str] = set()
    for path in _python_files(skip_tests_and_migrations=skip_tests_and_migrations):
        source = path.read_text(encoding="utf-8")
        if (
            literal not in source
            and not (exact and exact in source)
            and not (imports and "context_bus" in source)
        ):
            continue
        scanner = _Scanner(literal, imports=imports, exact=exact)
        scanner.visit(ast.parse(source, filename=str(path)))
        relative = path.relative_to(_REPO_ROOT).as_posix()
        found.update(f"{relative}::{qualname}" for qualname in scanner.hits)
    return found


def test_the_census_has_its_tables() -> None:
    tables = _tables()
    assert {"user_context", "owner_zone", "feeder", "switch"} <= set(tables)
    # The durable receipt store is a named obligation, not an implied one.
    assert {"parity-receipt-store", "deletion-needs-receipts"} <= tables["switch"]


def test_every_user_context_reader_and_writer_has_exactly_one_row() -> None:
    found = _references(
        "public.user_context", imports=True, skip_tests_and_migrations=True, exact="user_context"
    )
    rows = _tables()["user_context"]
    assert sorted(found - rows) == [], "unclassified public.user_context reference: add a row"
    assert sorted(rows - found) == [], "census row with no reference left: remove it"


def test_every_hardcoded_owner_zone_has_a_row() -> None:
    found = _references("Asia/Singapore", imports=False, skip_tests_and_migrations=True)
    rows = _tables()["owner_zone"]
    assert sorted(found - rows) == [], "unclassified Asia/Singapore literal: add a row"
    assert sorted(rows - found) == [], "census row with no literal left: remove it"


def test_every_feeder_pointer_exists() -> None:
    for key in _tables()["feeder"]:
        path, name = key.split("::", 1)
        target = _REPO_ROOT / path
        assert target.is_file(), f"feeder pointer {path} does not exist"
        if name != "<module>":
            assert re.search(rf"def {re.escape(name)}\b", target.read_text(encoding="utf-8")), (
                f"feeder pointer {key} names no test"
            )


def test_the_scanner_skips_docstrings_and_names_the_enclosing_function() -> None:
    source = (
        '"""public.user_context in a module docstring."""\n'
        "def reader():\n"
        '    """public.user_context in a docstring."""\n'
        '    return "SELECT * FROM public.user_context"\n'
        "class Holder:\n"
        "    def nested(self):\n"
        "        from butlers import context_bus\n"
        "        return context_bus\n"
    )
    scanner = _Scanner("public.user_context", imports=True)
    scanner.visit(ast.parse(source))
    assert scanner.hits == {"reader", "Holder.nested"}
