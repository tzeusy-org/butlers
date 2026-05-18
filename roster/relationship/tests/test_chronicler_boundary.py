"""Chronicler-boundary guardrail for the relationship butler.

The relationship butler MUST NOT reach across the chronicler schema boundary by:

1. Embedding ``FROM chronicler.`` or ``JOIN chronicler.`` in SQL strings.
2. Importing ``chronicler.models`` (or any direct chronicler model module) —
   cross-butler data access must go through MCP, never through direct model
   imports.

This test is a static scan of the relationship butler source tree.  It needs no
database, no async fixtures, and no external dependencies.  It is fast and safe
to run in any environment.

Mirrors the invariant style from RFC 0014 §D5 (``rfcs/0014:178``) which
enforces the equivalent no-LLM constraint on Chronicler projection adapters.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Source roots to scan
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()

# Relationship butler roster directory
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
assert _ROSTER_ROOT.name == "relationship", (
    f"Unexpected roster root: {_ROSTER_ROOT}. "
    "This test must live at roster/relationship/tests/test_chronicler_boundary.py"
)


# Collect all Python source paths to scan, excluding this file itself.
def _relationship_source_files() -> list[Path]:
    """Return all .py files under roster/relationship/, excluding this file."""
    return [p for p in _ROSTER_ROOT.rglob("*.py") if p.resolve() != _HERE]


# ---------------------------------------------------------------------------
# Forbidden patterns
# ---------------------------------------------------------------------------

# SQL cross-schema references: any literal "FROM chronicler." or "JOIN chronicler."
# (case-insensitive so both SQL convention and mixed-case variants are caught).
_SQL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"FROM\s+chronicler\.", re.IGNORECASE),
    re.compile(r"JOIN\s+chronicler\.", re.IGNORECASE),
]

# Direct import of chronicler models: matches patterns such as
#   import chronicler.models
#   from chronicler.models import ...
#   from chronicler import models
# This is intentionally broad: any ``chronicler.models`` token in an import
# statement signals a direct model coupling that must not cross the boundary.
_IMPORT_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?:
        import \s+ chronicler\.models    # import chronicler.models[.something]
      | from   \s+ chronicler\.models \s+ import  # from chronicler.models import …
      | from   \s+ chronicler \s+ import \s+ models  # from chronicler import models
    )
    """,
    re.VERBOSE,
)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Scan *path* for forbidden patterns.

    Returns a list of ``(line_number, pattern_label, line_text)`` tuples for
    each violation found.
    """
    violations: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pat in _SQL_PATTERNS:
            if pat.search(line):
                violations.append((lineno, pat.pattern, line.strip()))
        if _IMPORT_PATTERN.search(line):
            violations.append((lineno, "import chronicler.models", line.strip()))
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_chronicler_sql_cross_schema_references() -> None:
    """Relationship butler source must contain no ``FROM chronicler.`` or
    ``JOIN chronicler.`` SQL fragments.

    The chronicler schema is owned exclusively by the chronicler butler.
    The relationship butler must not query it directly; all cross-butler
    data access goes through MCP tools (RFC 0014 §D6 boundary).
    """
    sources = _relationship_source_files()
    assert sources, "Expected at least one .py file under roster/relationship/"

    all_violations: list[str] = []
    for path in sorted(sources):
        for lineno, label, text in _scan_file(path):
            # Only report SQL-pattern violations here
            if label != "import chronicler.models":
                rel = path.relative_to(_ROSTER_ROOT.parent.parent)
                all_violations.append(f"  {rel}:{lineno}: [{label!r}] {text!r}")

    assert not all_violations, (
        "Relationship butler source contains forbidden chronicler schema SQL references.\n"
        "The relationship butler MUST NOT query chronicler.* tables directly; "
        "use MCP tools for cross-butler data access (RFC 0014 §D6).\n\n"
        "Violations:\n" + "\n".join(all_violations)
    )


def test_no_direct_chronicler_model_imports() -> None:
    """Relationship butler source must not import ``chronicler.models`` or
    any equivalent direct-model import from the chronicler butler.

    Cross-butler coupling through shared model imports violates the MCP-only
    inter-butler boundary defined in the Butlers architecture.  If the
    relationship butler needs chronicler data, it must call a chronicler MCP
    tool, not import a chronicler model directly.
    """
    sources = _relationship_source_files()
    assert sources, "Expected at least one .py file under roster/relationship/"

    all_violations: list[str] = []
    for path in sorted(sources):
        for lineno, label, text in _scan_file(path):
            if label == "import chronicler.models":
                rel = path.relative_to(_ROSTER_ROOT.parent.parent)
                all_violations.append(f"  {rel}:{lineno}: {text!r}")

    assert not all_violations, (
        "Relationship butler source contains forbidden chronicler model imports.\n"
        "Inter-butler data access MUST go through MCP, not direct Python imports.\n\n"
        "Violations:\n" + "\n".join(all_violations)
    )
