#!/usr/bin/env python3
"""
check_for_update_joins.py

Scan *.py and *.sql files for unsafe `FOR UPDATE` + outer-join combinations.

PostgreSQL raises "FOR UPDATE cannot be applied to the nullable side of an
outer join" at runtime.  Mock-based tests bypass this, so the bug is invisible
until a real-DB integration test runs.

Safe pattern:
    SELECT ... FROM a LEFT JOIN b ON ... FOR UPDATE OF a SKIP LOCKED
    (the 'OF <table>' qualifier explicitly excludes the nullable side)

Unsafe pattern:
    SELECT ... FROM a LEFT JOIN b ON ... FOR UPDATE [SKIP LOCKED]
    (unqualified lock attempts to lock both sides — Postgres refuses)

Algorithm
---------
For every occurrence of FOR UPDATE in a file, look at a sliding window of
CONTEXT_LINES lines before it.  If that window contains LEFT JOIN, RIGHT JOIN,
or FULL JOIN, and the FOR UPDATE is NOT followed by 'OF' (the qualifier) within
a small lookahead, flag it.

Window size is generous (30 lines) to handle multi-line SQL strings while
staying well within a single query block.  Queries longer than 30 lines before
the FOR UPDATE clause are unusual and are documented as a known limitation.

False positives
---------------
Comments and docstrings that mention 'FOR UPDATE' near 'LEFT JOIN' can produce
false positives.  The current codebase has no such cases; if one is added,
annotate the line with  # check-for-update-joins: ignore  to suppress it.

Exit codes:
  0  No violations found.
  1  One or more unsafe patterns found.

Usage:
  python3 scripts/check_for_update_joins.py            # scan from repo root
  python3 scripts/check_for_update_joins.py src/ tests/ roster/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
CONTEXT_LINES = 30  # lines above FOR UPDATE to inspect for outer joins
IGNORE_LOOKAHEAD = 3  # lines after FOR UPDATE to check for ignore marker or OF qualifier
IGNORE_MARKER = "check-for-update-joins: ignore"

_FOR_UPDATE_RE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)
_QUALIFIED_RE = re.compile(r"\bFOR\s+UPDATE\s+OF\b", re.IGNORECASE)
_OUTER_JOIN_RE = re.compile(r"\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN\b", re.IGNORECASE)

# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------


def find_violations(filepath: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, line_text) for unsafe FOR UPDATE occurrences."""
    try:
        lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    violations: list[tuple[int, str]] = []

    for lineno, line in enumerate(lines, start=1):
        # Fast skip: line must contain FOR UPDATE
        if not _FOR_UPDATE_RE.search(line):
            continue

        # Honour the ignore marker (on the same line or within the context window)
        if IGNORE_MARKER in line:
            continue

        # A FOR UPDATE OF qualifier on this line or immediately following lines is safe.
        # This handles cases where auto-formatters split the clause across lines.
        lookahead_text = " ".join(lines[lineno - 1 : lineno + IGNORE_LOOKAHEAD])
        if _QUALIFIED_RE.search(lookahead_text):
            continue

        # Look at the preceding context window for outer join keywords
        window_start = max(0, lineno - 1 - CONTEXT_LINES)
        window = lines[window_start : lineno - 1]  # lines *before* this one

        # Check if any ignore marker appears in the window or in a small lookahead
        # window after the FOR UPDATE line.  This tolerates auto-formatters (e.g.
        # ruff) splitting a long call onto the next line where the marker lives.
        lookahead = lines[lineno : lineno + IGNORE_LOOKAHEAD]
        if any(IGNORE_MARKER in w for w in (*window, *lookahead)):
            continue

        if _OUTER_JOIN_RE.search("\n".join(window)):
            violations.append((lineno, line.rstrip()))

    return violations


def scan_paths(roots: list[Path]) -> list[tuple[Path, int, str]]:
    """Walk roots and return all (filepath, lineno, line) violations."""
    all_violations: list[tuple[Path, int, str]] = []
    extensions = {".py", ".sql"}

    for root in roots:
        if root.is_file():
            if root.suffix in extensions:
                for lineno, line in find_violations(root):
                    all_violations.append((root, lineno, line))
        else:
            for ext in extensions:
                for path in sorted(root.rglob(f"*{ext}")):
                    if path.is_file():
                        for lineno, line in find_violations(path):
                            all_violations.append((path, lineno, line))

    return all_violations


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> int:
    if len(sys.argv) > 1:
        roots = [Path(arg) for arg in sys.argv[1:]]
    else:
        roots = [Path("src"), Path("tests"), Path("roster")]

    # Filter to roots that actually exist (tolerates partial invocations)
    roots = [r for r in roots if r.exists()]
    if not roots:
        print("check_for_update_joins: no paths to scan", file=sys.stderr)
        return 0

    violations = scan_paths(roots)

    if not violations:
        print("check_for_update_joins: OK — no unsafe FOR UPDATE + outer join patterns found")
        return 0

    print(
        "check_for_update_joins: FAIL — unsafe FOR UPDATE without OF qualifier near outer join\n",
        file=sys.stderr,
    )
    for filepath, lineno, line in violations:
        print(f"  {filepath}:{lineno}: {line.strip()}", file=sys.stderr)

    print(
        "\nFix: use  FOR UPDATE OF <table>  to lock only the non-nullable side,",
        file=sys.stderr,
    )
    print(
        "or add  # check-for-update-joins: ignore  to suppress a known-safe case.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
