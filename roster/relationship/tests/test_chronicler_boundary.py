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


# ---------------------------------------------------------------------------
# Binning code path coverage (entity v3 — Activity binning parameter)
# ---------------------------------------------------------------------------
#
# The static scan above already covers every relationship source file, so the
# new daily-binning code path in ``get_entity_activity`` is automatically inside
# the SQL/import guardrail's reach.  This test pins that coverage explicitly so a
# future refactor that moves binning into a helper which reaches across the
# chronicler boundary (direct SQL or a model import) is caught here by name,
# rather than relying on the reader to trust the glob.


def _activity_binning_source() -> tuple[Path, str]:
    """Return the router file path + text that hosts the activity binning logic."""
    router = _ROSTER_ROOT / "api" / "router.py"
    assert router.exists(), f"Expected the relationship router at {router}"
    return router, router.read_text(encoding="utf-8")


def test_binning_path_is_in_the_scanned_source_set() -> None:
    """The activity binning implementation MUST be one of the scanned sources.

    Guards against the binning code being relocated outside ``roster/relationship``
    (where the boundary scan would no longer see it).
    """
    router, _ = _activity_binning_source()
    scanned = {p.resolve() for p in _relationship_source_files()}
    assert router.resolve() in scanned, (
        "The activity binning code path lives in roster/relationship/api/router.py, "
        "which MUST be covered by the chronicler-boundary static scan."
    )


def test_binning_path_has_no_chronicler_boundary_violation() -> None:
    """The activity binning code path MUST NOT cross the chronicler boundary.

    Chronicler episodes feed the binning aggregation only via the
    ``chronicler_list_episodes`` MCP tool (entity v3 spec, "Activity binning
    parameter" → "Binning stays behind the MCP boundary").  This asserts the
    binning host file carries the MCP call and no direct chronicler SQL/import.
    """
    router, text = _activity_binning_source()

    # The binning path reuses the existing chronicler fetch, which calls the MCP
    # tool by name; assert that contract marker is present so the boundary the
    # binning relies on is real, not assumed.
    assert "chronicler_list_episodes" in text, (
        "The activity aggregator (which the binning path feeds from) MUST source "
        "chronicler rows via the chronicler_list_episodes MCP tool."
    )

    violations = [(lineno, label, line) for lineno, label, line in _scan_file(router)]
    assert not violations, (
        "The activity binning host file crosses the chronicler boundary:\n"
        + "\n".join(f"  router.py:{ln}: [{lbl}] {txt!r}" for ln, lbl, txt in violations)
    )
