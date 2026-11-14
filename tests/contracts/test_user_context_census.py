"""Contract: every legacy ``public.user_context`` reader/writer and owner-zone literal has a row.

bu-dhloi1.21 AC4: ``public.user_context`` retires only when every reader and
writer has a named successor or an explicitly deferred owner, and deletion needs
shadow/parity receipts, not an empty search. The census is
``docs/data_and_storage/user-context-census.md``; this guard keeps it honest in
both directions.

* ``user_context``: every function (or module) in ``src/`` and ``roster/`` that
  names ``public.user_context`` in code (docstrings excluded), names the table
  unqualified in SQL (``FROM``/``JOIN``/``INTO``/``UPDATE``/``TABLE``), carries
  the bare key ``"user_context"`` (the grants map, and the audit namesake that is
  not this table), or imports ``butlers.context_bus`` has exactly one row, and
  every row still has such a reference. The scan is lexical: ``getattr``,
  ``importlib`` and re-export bypasses need a deliberate search.
* ``owner_zone``: every non-docstring ``"Asia/Singapore"`` literal, fixed
  ``timezone(timedelta(hours=8))`` offset, and use of the briefing's ``SGT`` /
  ``today_sgt`` outside tests and migrations has a row, and every row still has
  one. The clock module's legacy fallback is listed through its row like any
  other site.
* ``owner_zone_frontend``: every ``frontend/src`` file (tests excluded) with a
  quoted ``"Asia/Singapore"`` outside comments has a row, and every row still has
  one.
* ``feeder``: every test a feeder row points at exists.
* ``switch``: the switch-time obligations, including the durable parity receipt
  store, are named rows.

A new reference fails until it is classified; a stale row fails too.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CENSUS = _REPO_ROOT / "docs" / "data_and_storage" / "user-context-census.md"
_SCAN_ROOTS = (_REPO_ROOT / "src", _REPO_ROOT / "roster")
_FRONTEND = _REPO_ROOT / "frontend" / "src"
#: An unqualified SQL reader or writer, e.g. ``SELECT ... FROM user_context``.
_SQL_TABLE_RE = re.compile(r"(?i)\b(from|join|into|update|table)\s+(public\.)?user_context\b")
#: The briefing's fixed-offset owner zone and its date helper, wherever named.
_SGT_NAMES = frozenset({"SGT", "today_sgt"})
_TS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_TS_ZONE_RE = re.compile(r"[\"'`]Asia/Singapore[\"'`]")
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


def _user_context_text(value: str) -> bool:
    return (
        "public.user_context" in value
        or value == "user_context"
        or _SQL_TABLE_RE.search(value) is not None
    )


def _owner_zone_text(value: str) -> bool:
    return "Asia/Singapore" in value


def _is_fixed_offset(node: ast.Call) -> bool:
    """``timezone(timedelta(hours=8))``: the owner zone as a fixed UTC+8 offset."""

    def named(func: ast.expr, name: str) -> bool:
        return (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        )

    return named(node.func, "timezone") and any(
        isinstance(arg, ast.Call)
        and named(arg.func, "timedelta")
        and any(
            kw.arg == "hours" and isinstance(kw.value, ast.Constant) and kw.value.value == 8
            for kw in arg.keywords
        )
        for arg in node.args
    )


class _Scanner(ast.NodeVisitor):
    """Collect enclosing qualnames of matching references, docstrings skipped."""

    def __init__(
        self,
        text: Callable[[str], bool],
        *,
        imports: bool = False,
        names: frozenset[str] = frozenset(),
        fixed_offset: bool = False,
    ) -> None:
        self.text = text
        self.imports = imports
        self.names = names
        self.fixed_offset = fixed_offset
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
            and self.text(node.value)
            and id(node) not in self.docstrings
        ):
            self._hit()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.names:
            self._hit()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.names:
            self._hit()
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.fixed_offset and _is_fixed_offset(node):
            self._hit()
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self.imports and any(alias.name == _CONTEXT_BUS for alias in node.names):
            self._hit()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any(alias.name in self.names for alias in node.names):
            self._hit()
        if self.imports and (
            node.module == _CONTEXT_BUS
            or (
                node.module == "butlers"
                and any(alias.name == "context_bus" for alias in node.names)
            )
        ):
            self._hit()


def _references(scanner: Callable[[], _Scanner], needles: tuple[str, ...]) -> set[str]:
    """Scanner hits as ``path::qualname`` over src/ and roster/, tests and migrations skipped."""
    found: set[str] = set()
    for path in _python_files(skip_tests_and_migrations=True):
        source = path.read_text(encoding="utf-8")
        if not any(needle in source for needle in needles):
            continue
        visitor = scanner()
        visitor.visit(ast.parse(source, filename=str(path)))
        relative = path.relative_to(_REPO_ROOT).as_posix()
        found.update(f"{relative}::{qualname}" for qualname in visitor.hits)
    return found


def _user_context_references() -> set[str]:
    return _references(
        lambda: _Scanner(_user_context_text, imports=True), ("user_context", "context_bus")
    )


def _owner_zone_references() -> set[str]:
    return _references(
        lambda: _Scanner(_owner_zone_text, names=_SGT_NAMES, fixed_offset=True),
        ("Asia/Singapore", "SGT", "today_sgt", "hours=8"),
    )


def _frontend_owner_zones() -> set[str]:
    """Frontend files (tests excluded) with a quoted ``Asia/Singapore`` outside comments."""
    found = set()
    for path in sorted(_FRONTEND.rglob("*.ts*")):
        parts = path.relative_to(_FRONTEND).parts
        if path.suffix not in {".ts", ".tsx"} or ".test." in path.name:
            continue
        if {"test", "tests", "__tests__"} & set(parts):
            continue
        source = path.read_text(encoding="utf-8")
        if "Asia/Singapore" not in source:
            continue
        code = _TS_BLOCK_COMMENT_RE.sub("", source)
        lines = [line for line in code.splitlines() if not line.lstrip().startswith("//")]
        if _TS_ZONE_RE.search("\n".join(lines)):
            found.add(path.relative_to(_REPO_ROOT).as_posix())
    return found


def test_the_census_has_its_tables() -> None:
    tables = _tables()
    assert {"user_context", "owner_zone", "owner_zone_frontend", "feeder", "switch"} <= set(tables)
    # The durable receipt store is a named obligation, not an implied one.
    assert {"parity-receipt-store", "deletion-needs-receipts"} <= tables["switch"]
    # Known feeder gaps stay visible rather than silently absent.
    assert {
        "timeline-400-day-bound",
        "owntracks-fix-expiry",
        "no-duplicate-life-record-source",
        "place-transition-retention",
        "expect-routine-replay",
    } <= tables["feeder_gaps"]


def test_every_user_context_reader_and_writer_has_exactly_one_row() -> None:
    found = _user_context_references()
    rows = _tables()["user_context"]
    assert sorted(found - rows) == [], "unclassified public.user_context reference: add a row"
    assert sorted(rows - found) == [], "census row with no reference left: remove it"


def test_every_hardcoded_owner_zone_has_a_row() -> None:
    found = _owner_zone_references()
    rows = _tables()["owner_zone"]
    assert sorted(found - rows) == [], "unclassified owner zone (literal, UTC+8 or SGT): add a row"
    assert sorted(rows - found) == [], "census row with no owner zone left: remove it"


def test_every_frontend_owner_zone_has_a_row() -> None:
    found = _frontend_owner_zones()
    rows = _tables()["owner_zone_frontend"]
    assert sorted(found - rows) == [], "unclassified frontend Asia/Singapore literal: add a row"
    assert sorted(rows - found) == [], "census row with no frontend literal left: remove it"


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
    scanner = _Scanner(_user_context_text, imports=True)
    scanner.visit(ast.parse(source))
    assert scanner.hits == {"reader", "Holder.nested"}


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM user_context WHERE signal = $1",
        "insert into public.user_context (signal) values ($1)",
        "UPDATE user_context SET active = false",
        "LEFT JOIN user_context uc ON true",
    ],
)
def test_an_unqualified_sql_reader_is_a_reference(statement: str) -> None:
    scanner = _Scanner(_user_context_text)
    scanner.visit(ast.parse(f"def reader():\n    return {statement!r}\n"))
    assert scanner.hits == {"reader"}


def test_an_audit_namesake_field_is_not_sql() -> None:
    assert not _SQL_TABLE_RE.search("request user_context field")
    assert not _SQL_TABLE_RE.search("user_context_id")


def test_a_new_fixed_offset_or_sgt_site_is_an_owner_zone() -> None:
    source = (
        "from datetime import timedelta, timezone\n"
        "from butlers.jobs.briefing import SGT\n"
        "from butlers.jobs import briefing\n"
        "SLEEP = timedelta(hours=8)\n"  # a duration, not a zone
        "def offset():\n"
        "    return timezone(timedelta(hours=8))\n"
        "def by_name(now):\n"
        "    return now.astimezone(SGT)\n"
        "def by_module(now):\n"
        "    return now.astimezone(briefing.SGT)\n"
        "def by_helper():\n"
        "    return briefing.today_sgt()\n"
    )
    scanner = _Scanner(_owner_zone_text, names=_SGT_NAMES, fixed_offset=True)
    scanner.visit(ast.parse(source))
    assert scanner.hits == {"<module>", "offset", "by_name", "by_module", "by_helper"}
    duration_only = _Scanner(_owner_zone_text, names=_SGT_NAMES, fixed_offset=True)
    duration_only.visit(ast.parse("from datetime import timedelta\nX = timedelta(hours=8)\n"))
    assert duration_only.hits == set()
