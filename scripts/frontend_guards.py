#!/usr/bin/env python3
"""Run the hosted ``guards`` job's frontend lint steps locally (bu-5n9pfa).

``STEPS`` is the one list of frontend guard steps. ``tests/contracts/
test_frontend_guard_steps.py`` pins it against ``.github/workflows/ci.yml`` so the local
runners (``make check-guards`` and ``scripts/pre_push.py``) cannot drift from the hosted job.

The steps run only when a ``frontend/`` path changed against ``--base``; otherwise this prints
one line and exits 0. When frontend paths changed but ``frontend/node_modules`` is absent the
steps cannot run, so this fails loudly instead of reporting a pass.

Hosted-only steps (not run here): ``npm run knip`` and ``npm run build`` (slow, build artifact),
and the node test runner contract in the hosted "Preserve bounded Node tooling" step.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"

# (hosted step name, npm script). `lint` already includes lint:visual-role-css.
STEPS: tuple[tuple[str, str], ...] = (
    ("Lint", "lint"),
    ("Em-dash copy gate (bu-370uj)", "lint:emdash"),
    ("Query-result coercion gate (bu-ep4ks.5)", "lint:query-coercion"),
    ("Owner-time survivor", "lint:owner-time"),
)

HOSTED_ONLY: tuple[str, ...] = ("knip", "build")


def changed_paths(base: str) -> list[str]:
    """Paths differing from ``base`` in commits, index and worktree, plus untracked files."""
    names: list[str] = []
    for args in (
        ["diff", "--name-only", "-z", base],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True
        ).stdout.decode()
        names += [name for name in out.split("\0") if name]
    return names


def frontend_changed(paths: list[str]) -> bool:
    return any(name.startswith("frontend/") for name in paths)


def inventory_stale() -> bool:
    """True when COPY_INVENTORY.md differs from a fresh render; never writes the file."""
    spec = importlib.util.spec_from_file_location(
        "extract_frontend_copy", REPO_ROOT / "scripts/extract-frontend-copy.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report, _ = module.generate_report(module.collect_tsx_files(module.SCAN_DIRS))
    current = FRONTEND / "COPY_INVENTORY.md"
    return not current.is_file() or current.read_text(encoding="utf-8") != report


def run(base: str, *, skip_inventory: bool = False, assume_changed: bool = False) -> int:
    if not assume_changed and not frontend_changed(changed_paths(base)):
        print(f"frontend-guards: no frontend/ path changed against {base}, nothing to run")
        return 0
    if not (FRONTEND / "node_modules").is_dir():
        names = ", ".join(script for _, script in STEPS)
        print(
            f"frontend guard steps not run: {names} (frontend/node_modules missing; "
            "run `npm ci` in frontend/)",
            file=sys.stderr,
        )
        return 1
    failed: list[str] = []
    for title, script in STEPS:
        print(f"frontend-guards: npm run {script}  [{title}]", flush=True)
        if subprocess.run(["npm", "run", script], cwd=FRONTEND).returncode:
            failed.append(script)
    if not skip_inventory and inventory_stale():
        print(
            "frontend/COPY_INVENTORY.md is stale. Regenerate it with "
            "'python3 scripts/extract-frontend-copy.py' and commit the result.",
            file=sys.stderr,
        )
        failed.append("COPY_INVENTORY.md")
    if failed:
        print(f"frontend-guards: FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("frontend-guards: all steps passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--skip-inventory", action="store_true")
    parser.add_argument(
        "--assume-changed",
        action="store_true",
        help="caller already knows a frontend/ path changed (pre_push); skip the base diff",
    )
    parser.add_argument("--list", action="store_true", help="print the npm scripts and exit")
    args = parser.parse_args()
    if args.list:
        print("\n".join(script for _, script in STEPS))
        return 0
    return run(args.base, skip_inventory=args.skip_inventory, assume_changed=args.assume_changed)


if __name__ == "__main__":
    raise SystemExit(main())
