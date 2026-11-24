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

from butlers.testing.source_guard import parent_map, strip_docstrings

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


def _is_user_context_reference(node: ast.AST) -> bool:
    match node:
        case ast.Constant(value=str() as text):
            return _user_context_text(text)
        case ast.Import(names=aliases):
            return any(alias.name == _CONTEXT_BUS for alias in aliases)
        case ast.ImportFrom(module=module, names=aliases):
            return module == _CONTEXT_BUS or (
                module == "butlers" and any(alias.name == "context_bus" for alias in aliases)
            )
        case _:
            return False


def _is_owner_zone_reference(node: ast.AST) -> bool:
    match node:
        case ast.Constant(value=str() as text):
            return _owner_zone_text(text)
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name in _SGT_NAMES
        case ast.Call():
            return _is_fixed_offset(node)
        case ast.ImportFrom(names=aliases):
            return any(alias.name in _SGT_NAMES for alias in aliases)
        case _:
            return False


def _reference_scopes(tree: ast.Module, matches: Callable[[ast.AST], bool]) -> set[str]:
    """Collect the enclosing qualnames of matching code, excluding docstrings."""
    code = strip_docstrings(tree)
    parents = parent_map(code)
    found: set[str] = set()
    for node in ast.walk(code):
        if not matches(node):
            continue
        names = []
        parent = parents.get(id(node))
        while parent is not None:
            if isinstance(parent, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                names.append(parent.name)
            parent = parents.get(id(parent))
        found.add(".".join(reversed(names)) or "<module>")
    return found


def _references(matches: Callable[[ast.AST], bool], needles: tuple[str, ...]) -> set[str]:
    """Return matching path and qualname pairs from source outside tests and migrations."""
    found: set[str] = set()
    for path in _python_files(skip_tests_and_migrations=True):
        source = path.read_text(encoding="utf-8")
        if not any(needle in source for needle in needles):
            continue
        scopes = _reference_scopes(ast.parse(source, filename=str(path)), matches)
        relative = path.relative_to(_REPO_ROOT).as_posix()
        found.update(f"{relative}::{qualname}" for qualname in scopes)
    return found


def _user_context_references() -> set[str]:
    return _references(_is_user_context_reference, ("user_context", "context_bus"))


def _owner_zone_references() -> set[str]:
    return _references(
        _is_owner_zone_reference,
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
    assert _reference_scopes(ast.parse(source), _is_user_context_reference) == {
        "reader",
        "Holder.nested",
    }


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
    tree = ast.parse(f"def reader():\n    return {statement!r}\n")
    assert _reference_scopes(tree, _is_user_context_reference) == {"reader"}


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
    assert _reference_scopes(ast.parse(source), _is_owner_zone_reference) == {
        "<module>",
        "offset",
        "by_name",
        "by_module",
        "by_helper",
    }
    duration_only = ast.parse("from datetime import timedelta\nX = timedelta(hours=8)\n")
    assert _reference_scopes(duration_only, _is_owner_zone_reference) == set()
