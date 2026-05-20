"""Contract: all SQL queries in the relationship butler that touch facts must filter by scope.

The ``facts`` table is shared across butler domains and namespaced via the ``scope``
column.  The relationship butler MUST filter ``scope = 'relationship'`` (or an
explicitly whitelisted multi-scope variant) on every read.  Omitting the filter
causes cross-scope contamination: relationship queries may silently return or
mutate facts owned by other butlers (health, finance, home, memory/global).

Rules enforced:
  1. Every SQL string literal in the scanned Python source that references
     ``facts`` in a FROM or JOIN clause MUST contain the word ``scope``
     (case-insensitive) somewhere in that SQL string.
  2. Exceptions are allowed via an inline ``# scope-ok: <reason>`` marker on
     any source line inside the same string's enclosing statement.  The test
     collects source lines for the string literal's line range and accepts the
     SQL if any of those lines contain the marker.

Scanned path:
  - ``roster/relationship/`` (all *.py files)

Note: ``src/butlers/modules/memory/`` is intentionally excluded.  The memory
module is a cross-butler store and many of its queries operate on ``scope='global'``
by design.  Those are not relationship-domain queries and are governed by the
memory module's own conventions.

Background: discovered across PR #1772 (bu-bnagl) and PR #1773 (bu-mgc4m) where
reviewer comments added the missing ``scope = 'relationship'`` filter.
Promoted to a static guardrail by bu-ki4a3.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Scanned directories (relative to repo root)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SCAN_ROOTS: list[Path] = [
    _REPO_ROOT / "roster" / "relationship",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# SQL verb heuristic: strings that begin with a DML/DDL keyword are SQL.
_SQL_START_RE = re.compile(
    r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b",
    re.IGNORECASE,
)

# Detect a FROM or JOIN referencing the facts table (bare or schema-qualified).
# Matches: FROM facts, JOIN facts, relationship.facts, etc.
_FACTS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?facts\b",
    re.IGNORECASE,
)

# The inline marker that whitelists a query for intentional cross-scope access.
_SCOPE_OK_MARKER = "# scope-ok:"


def _collect_fstring_constant_ids(tree: ast.AST) -> set[int]:
    """Return the set of id()s of ast.Constant nodes that are part of f-strings.

    Constants inside f-strings (ast.JoinedStr) are fragments of a dynamic SQL
    string where the WHERE clause may be assembled from a list of conditions.
    These fragments should NOT be checked independently because the 'scope'
    condition lives in the surrounding Python code (the conditions list), not
    inside the string fragment itself.
    """
    fstring_constants: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant):
                    fstring_constants.add(id(child))
    return fstring_constants


def _extract_sql_with_locations(
    source: str,
) -> list[tuple[str, int, int]]:
    """Return (sql_string, start_lineno, end_lineno) for SQL string literals in *source*.

    start_lineno and end_lineno are 1-based line numbers matching the AST node.

    Skips constants that are fragments of f-strings (JoinedStr nodes), because
    those dynamic queries assemble their WHERE clause via Python variables and
    cannot be statically verified the same way.
    """
    tree = ast.parse(source)
    fstring_ids = _collect_fstring_constant_ids(tree)
    results: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in fstring_ids:
                continue  # skip f-string fragments — scope is in the conditions list
            if _SQL_START_RE.match(node.value):
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                results.append((node.value, start, end))
    return results


_LOOKAHEAD_LINES = 5  # How many lines before the string start to search for the marker


def _has_scope_ok_marker(source_lines: list[str], start: int, end: int) -> bool:
    """Return True if any line in the vicinity of the string contains the scope-ok marker.

    Searches the string's own lines (start..end) plus up to _LOOKAHEAD_LINES preceding
    lines, so that a comment placed on the ``await pool.fetch(`` or ``#`` line before the
    opening triple-quote is also detected.
    """
    search_start = max(1, start - _LOOKAHEAD_LINES)
    for lineno in range(search_start, end + 1):
        idx = lineno - 1
        if 0 <= idx < len(source_lines):
            if _SCOPE_OK_MARKER in source_lines[idx]:
                return True
    return False


# Test files contain assertion queries that verify data without needing a scope filter.
# They are intentionally excluded from the scan.
_EXCLUDED_SUBDIRS: frozenset[str] = frozenset({"tests"})


def _collect_violations(path: Path) -> list[str]:
    """Return a list of violation descriptions for SQL strings in *path* that lack scope."""
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()

    violations: list[str] = []
    for sql, start, end in _extract_sql_with_locations(source):
        if not _FACTS_RE.search(sql):
            continue  # this SQL doesn't touch the facts table

        # Check for the scope-ok whitelist marker on the enclosing source lines.
        if _has_scope_ok_marker(source_lines, start, end):
            continue

        # The SQL references facts — it must mention 'scope'.
        if "scope" not in sql.lower():
            snippet = sql.strip()[:140].replace("\n", " ")
            rel = path.relative_to(_REPO_ROOT)
            violations.append(
                f"{rel}:{start}: SQL touches 'facts' but contains no 'scope' filter "
                f"[sql: {snippet!r}]"
            )

    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_relationship_facts_queries_always_filter_scope() -> None:
    """Every SQL string that queries the facts table must include a scope filter.

    To whitelist an intentional cross-scope read, add an inline comment on any
    line within the SQL string's enclosing source span:

        # scope-ok: <reason>

    Example::

        # scope-ok: intentional cross-scope read for contact enrichment display
        AND f.scope IN ('global', 'relationship')

    The test fails if any SQL referencing ``facts`` lacks the ``scope`` keyword
    and has no whitelist marker.
    """
    all_violations: list[str] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            # Skip excluded subdirectory trees (e.g. tests/ — assertion queries need no scope).
            rel_parts = py_file.relative_to(root).parts
            if any(part in _EXCLUDED_SUBDIRS for part in rel_parts):
                continue
            all_violations.extend(_collect_violations(py_file))

    assert not all_violations, (
        f"Found {len(all_violations)} SQL query(ies) against the facts table that lack a "
        f"'scope' filter.  Add AND scope = 'relationship' or mark intentional cross-scope "
        f"reads with '# scope-ok: <reason>':\n" + "\n".join(f"  {v}" for v in all_violations)
    )


def test_scope_ok_marker_detection_works() -> None:
    """Verify the scope-ok marker suppresses violations for a synthetic bad query."""
    synthetic_source = """\
async def fetch():
    # scope-ok: intentional cross-scope audit query
    await pool.fetch(
        "SELECT id FROM facts WHERE entity_id = $1",
        eid,
    )
"""
    source_lines = synthetic_source.splitlines()
    sql_items = _extract_sql_with_locations(synthetic_source)
    assert len(sql_items) == 1, "Extractor should find exactly one SQL string"
    sql, start, end = sql_items[0]
    assert _FACTS_RE.search(sql), "SQL should reference facts"
    assert "scope" not in sql.lower(), "SQL should lack scope keyword (for this test)"
    assert _has_scope_ok_marker(source_lines, start, end), (
        "scope-ok marker should be detected on lines around the SQL string"
    )


def test_missing_scope_filter_is_detected() -> None:
    """Verify the scan catches a synthetic query that touches facts without scope."""
    synthetic_source = """\
async def fetch():
    await pool.fetch(
        "SELECT id FROM facts WHERE entity_id = $1",
        eid,
    )
"""
    # Manually replicate the detection logic with the synthetic source.
    source_lines = synthetic_source.splitlines()
    sql_items = _extract_sql_with_locations(synthetic_source)
    found_violation = False
    for sql, start, end in sql_items:
        if not _FACTS_RE.search(sql):
            continue
        if _has_scope_ok_marker(source_lines, start, end):
            continue
        if "scope" not in sql.lower():
            found_violation = True

    assert found_violation, (
        "A SQL string touching facts without a scope filter should be flagged as a violation"
    )


def test_good_scope_filter_passes() -> None:
    """Verify a synthetic query with scope filter passes the check."""
    synthetic_source = """\
async def fetch():
    await pool.fetch(
        "SELECT id FROM facts WHERE entity_id = $1 AND scope = 'relationship'",
        eid,
    )
"""
    source_lines = synthetic_source.splitlines()
    sql_items = _extract_sql_with_locations(synthetic_source)
    assert len(sql_items) == 1

    sql, start, end = sql_items[0]
    assert _FACTS_RE.search(sql), "SQL should reference facts"
    assert "scope" in sql.lower(), "SQL contains scope keyword — should pass"

    # No violation should be raised.
    has_violation = False
    if _FACTS_RE.search(sql) and not _has_scope_ok_marker(source_lines, start, end):
        if "scope" not in sql.lower():
            has_violation = True

    assert not has_violation, "A scoped query must not be flagged as a violation"


def test_scan_roots_exist() -> None:
    """Sanity: scanned root directories must exist."""
    for root in _SCAN_ROOTS:
        assert root.exists(), (
            f"Scan root {root} does not exist.  Update _SCAN_ROOTS if the project layout changed."
        )


def test_scan_finds_relationship_tools() -> None:
    """Sanity: scan must find at least one SQL string in roster/relationship/tools/."""
    tools_dir = _REPO_ROOT / "roster" / "relationship" / "tools"
    assert tools_dir.exists()

    found_any_sql = False
    for py_file in sorted(tools_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if _extract_sql_with_locations(source):
            found_any_sql = True
            break

    assert found_any_sql, (
        "Expected at least one SQL string literal in roster/relationship/tools/. "
        "The extractor may be broken."
    )
