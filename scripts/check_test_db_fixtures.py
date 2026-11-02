#!/usr/bin/env python3
"""Ratchet hand-built test databases out of the suite (bu-rvgzmf.1).

DB-backed tests get their database from one factory, ``butlers.testing.db``
(root ``conftest.py`` fixtures ``db``, ``db_pool`` and ``fresh_db``), which
clones a template migrated by the real Alembic chains. Two older patterns drift
from the real schema and repeat setup in every file, so this guard refuses new
instances of either:

* a per-file fixture named ``pool`` or ``migrated_db_url``;
* hand-written ``CREATE TABLE`` DDL in a string literal of a test file
  (``tests/**``, ``roster/*/tests/**`` or a ``conftest.py``).

Existing instances are frozen per file in ``scripts/test-db-fixtures-baseline.json``.
A file may only hold as many as its baseline entry; files not listed hold none.
When a file drops below its entry the run prints the tightening, and
``--update-baseline`` rewrites the file with the current (never larger) counts.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "scripts" / "test-db-fixtures-baseline.json"
FIXTURE_NAMES = frozenset({"pool", "migrated_db_url"})
CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP|TEMPORARY|UNLOGGED)?\s*TABLE\b", re.I
)
KINDS = ("fixtures", "create_table")


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    if not path.endswith(".py"):
        return False
    if parts[-1] == "conftest.py":
        return True
    return parts[0] == "tests" or (parts[0] == "roster" and "tests" in parts[1:-1])


def _is_fixture(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr == "fixture"
    return isinstance(target, ast.Name) and target.id == "fixture"


def scan_source(source: str) -> dict[str, int]:
    """Count banned fixtures and ``CREATE TABLE`` string literals in one file."""
    tree = ast.parse(source)
    counts = dict.fromkeys(KINDS, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in FIXTURE_NAMES and any(map(_is_fixture, node.decorator_list)):
                counts["fixtures"] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            counts["create_table"] += len(CREATE_TABLE.findall(node.value))
    return counts


def tracked_test_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    return sorted(path for path in output.split("\0") if path and is_test_file(path))


def scan(root: Path) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = {}
    for path in tracked_test_files(root):
        file = root / path
        if not file.is_file():
            continue
        counts = scan_source(file.read_text(encoding="utf-8"))
        if any(counts.values()):
            found[path] = counts
    return found


def compare(
    found: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> tuple[list[str], list[str]]:
    """Return (violations, tightenings) of *found* against *baseline*."""
    violations: list[str] = []
    tightenings: list[str] = []
    for path in sorted(set(found) | set(baseline)):
        have = found.get(path, {})
        allowed = baseline.get(path, {})
        for kind in KINDS:
            n, limit = have.get(kind, 0), allowed.get(kind, 0)
            if n > limit:
                violations.append(f"{path}: {n} {kind} (baseline {limit})")
            elif n < limit:
                tightenings.append(f"{path}: {kind} {limit} -> {n}")
    return violations, tightenings


def tightened(
    found: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """The baseline lowered to *found*, never raised."""
    result: dict[str, dict[str, int]] = {}
    for path, allowed in baseline.items():
        have = found.get(path, {})
        counts = {kind: min(allowed.get(kind, 0), have.get(kind, 0)) for kind in KINDS}
        if any(counts.values()):
            result[path] = counts
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--update-baseline", action="store_true", help="lower the baseline to current counts"
    )
    args = parser.parse_args(argv)
    found = scan(args.root)
    baseline = json.loads(args.baseline.read_text()) if args.baseline.is_file() else {}
    violations, tightenings = compare(found, baseline)
    if args.update_baseline:
        new = tightened(found, baseline)
        args.baseline.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {len(new)} file entr(ies) to {args.baseline}")
    if violations:
        print("New hand-built test databases. Use the `db`, `db_pool` or `fresh_db` fixtures")
        print("(butlers.testing.db) instead of a per-file pool/migrated_db_url fixture or DDL:")
        for line in violations:
            print(f"  {line}")
        return 1
    if tightenings and not args.update_baseline:
        print("Ratchet can be tightened (run with --update-baseline):")
        for line in tightenings:
            print(f"  {line}")
    total = {kind: sum(c.get(kind, 0) for c in found.values()) for kind in KINDS}
    print(
        f"No new hand-built test databases ({total['fixtures']} fixtures and "
        f"{total['create_table']} CREATE TABLE literals still frozen)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
