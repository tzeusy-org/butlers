#!/usr/bin/env python3
"""Ratchet patched daemon boots out of the suite (bu-rvgzmf.2).

A test whose subject is the boot uses ``booted_daemon``
(``src/butlers/testing/daemon.py``): the real lifecycle on a migrated clone with
only the LLM runtime faked. A test whose subject is one tool or one phase calls it
on a ``tool_host`` (``src/butlers/testing/tool_host.py``). Neither patches the
lifecycle, so a test file must not:

- patch a ``butlers.lifecycle.*`` target (``patch("butlers.lifecycle.X")``,
  ``monkeypatch.setattr("butlers.lifecycle.X", ...)``), or
- patch a private ``ButlerDaemon`` method (``patch.object(ButlerDaemon, "_x")``).

Existing patches are frozen per file, as counts, in
``scripts/test-boot-patches-baseline.json``. A file may hold at most its count;
files not listed hold none. ``--update-baseline`` lowers counts and never raises
them.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "scripts" / "test-boot-patches-baseline.json"
LIFECYCLE_PREFIX = "butlers.lifecycle."
PATCHERS = {"patch", "setattr"}


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    if not path.endswith(".py"):
        return False
    if parts[-1] == "conftest.py" and not path.startswith("src/"):
        return True
    return parts[0] == "tests" or (parts[0] == "roster" and "tests" in parts[1:-1])


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _str_arg(node: ast.Call, index: int) -> str | None:
    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
        value = node.args[index].value
        return value if isinstance(value, str) else None
    return None


def boot_patches(source: str) -> int:
    """How many lifecycle-phase patches *source* holds."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee(node)
        if callee in PATCHERS and (_str_arg(node, 0) or "").startswith(LIFECYCLE_PREFIX):
            count += 1
        elif (
            callee == "object"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "ButlerDaemon"
            and (_str_arg(node, 1) or "").startswith("_")
        ):
            count += 1
    return count


def tracked_test_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    return sorted(path for path in output.split("\0") if path and is_test_file(path))


def scan(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in tracked_test_files(root):
        file = root / path
        if file.is_file() and (count := boot_patches(file.read_text(encoding="utf-8"))):
            found[path] = count
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--update-baseline", action="store_true", help="lower frozen counts")
    args = parser.parse_args(argv)
    found = scan(args.root)
    baseline: dict[str, int] = (
        json.loads(args.baseline.read_text()) if args.baseline.is_file() else {}
    )
    violations = [
        f"{path}: {count} patched lifecycle target(s), {baseline.get(path, 0)} allowed"
        for path, count in found.items()
        if count > baseline.get(path, 0)
    ]
    stale = sum(
        count - found.get(path, 0) for path, count in baseline.items() if count > found.get(path, 0)
    )
    if args.update_baseline:
        kept = {
            path: min(count, found[path]) for path, count in baseline.items() if found.get(path)
        }
        args.baseline.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {len(kept)} file entr(ies) to {args.baseline}")
    if violations:
        print("New patched daemon boots (use booted_daemon, or a phase on tool_host):")
        for line in violations:
            print(f"  {line}")
        return 1
    if stale and not args.update_baseline:
        print(f"Ratchet can be tightened by {stale} patch(es): run with --update-baseline.")
    total = sum(found.values())
    print(f"No new patched boots ({total} frozen in {len(found)} file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
