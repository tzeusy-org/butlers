#!/usr/bin/env python3
"""Ratchet per-file copies of the shared test kit out of the suite (bu-rvgzmf.2).

``src/butlers/testing/`` holds one copy of the fakes and helpers that test files
used to redefine for themselves. A test file that defines one of the names below
at module level is copying the kit instead of importing it:

=====================================  =========================================
Copied name                            Import instead
=====================================  =========================================
``_load_migration``                    ``butlers.testing.migration.load_migration``
``_FakePool``, ``_make_pool``          ``butlers.testing.fake_pool.fake_pool``
``_FakeConn``, ``_FakeConnection``,    ``butlers.testing.fake_pool.FakeConnection``
``_make_conn``
``_AsyncCtx``                          ``butlers.testing.fake_pool.AsyncCtx``
``_Row``, ``_FakeRecord``,             ``butlers.testing.records.row`` / ``Record``
``_make_row``, ``_make_record``
=====================================  =========================================

Existing copies are frozen per file in ``scripts/test-kit-copies-baseline.json``.
A file may hold at most the names its entry lists; files not listed hold none.
``--update-baseline`` drops names a file no longer defines and never adds any.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "scripts" / "test-kit-copies-baseline.json"
KIT_NAMES: dict[str, str] = {
    "_load_migration": "butlers.testing.migration.load_migration",
    "_FakePool": "butlers.testing.fake_pool.fake_pool",
    "_make_pool": "butlers.testing.fake_pool.fake_pool",
    "_FakeConn": "butlers.testing.fake_pool.FakeConnection",
    "_FakeConnection": "butlers.testing.fake_pool.FakeConnection",
    "_make_conn": "butlers.testing.fake_pool.FakeConnection",
    "_AsyncCtx": "butlers.testing.fake_pool.AsyncCtx",
    "_Row": "butlers.testing.records.row",
    "_FakeRecord": "butlers.testing.records.Record",
    "_make_row": "butlers.testing.records.row",
    "_make_record": "butlers.testing.records.row",
}


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    if not path.endswith(".py"):
        return False
    if parts[-1] == "conftest.py":
        return True
    return parts[0] == "tests" or (parts[0] == "roster" and "tests" in parts[1:-1])


def copied_names(source: str) -> list[str]:
    """Kit names bound by a module-level ``def`` or ``class`` in *source*."""
    found = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.name in KIT_NAMES
    }
    return sorted(found)


def tracked_test_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    return sorted(path for path in output.split("\0") if path and is_test_file(path))


def scan(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in tracked_test_files(root):
        file = root / path
        if file.is_file() and (names := copied_names(file.read_text(encoding="utf-8"))):
            found[path] = names
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--update-baseline", action="store_true", help="drop names files no longer define"
    )
    args = parser.parse_args(argv)
    found = scan(args.root)
    baseline: dict[str, list[str]] = (
        json.loads(args.baseline.read_text()) if args.baseline.is_file() else {}
    )
    violations = [
        f"{path}: {name} (import {KIT_NAMES[name]})"
        for path, names in found.items()
        for name in names
        if name not in baseline.get(path, [])
    ]
    stale = sum(
        1 for path, names in baseline.items() for name in names if name not in found.get(path, [])
    )
    if args.update_baseline:
        kept = {
            path: [name for name in names if name in found.get(path, [])]
            for path, names in baseline.items()
        }
        kept = {path: names for path, names in kept.items() if names}
        args.baseline.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {len(kept)} file entr(ies) to {args.baseline}")
    if violations:
        print("New per-file copies of the shared test kit (src/butlers/testing/):")
        for line in violations:
            print(f"  {line}")
        return 1
    if stale and not args.update_baseline:
        print(f"Ratchet can be tightened by {stale} name(s): run with --update-baseline.")
    total = sum(len(names) for names in found.values())
    print(f"No new test-kit copies ({total} frozen in {len(found)} file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
