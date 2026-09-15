#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""CI wrapper around ``butlers.testing.scoped_runner`` for the PR affected-test lane (bu-v28ho).

Emits a machine-readable decision to ``$GITHUB_OUTPUT`` so ``.github/workflows/ci.yml`` can choose
between the affected-test lane (``check-affected``) and the full ten-shard matrix
(``check-unit-*`` / ``check-integration-*``):

- ``mode=scoped`` only when the planner selected a bounded set of existing test paths with no
  escalation trigger.
- ``mode=full`` on every other outcome: an escalation (shared/cross-cutting file, unknown path),
  an empty plan (nothing mapped), or a plan that reaches into ``tests/e2e/`` -- which this lane's
  Postgres-only runner cannot execute (it needs an authenticated CLI runtime and the ``claude``
  binary that only the merge queue's full matrix provisions).

``mode=full`` must always be the safe default: this script only ever recommends narrowing away
from the ten-shard matrix, never widens what that matrix already runs. See
``about/craft-and-care/testing-and-verification.md`` for the measured planner precision that
justified shipping this lane.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from butlers.testing.scoped_runner import (  # noqa: E402
    FULL_SUITE_FALLBACK_ALLOWLIST,
    ScopedTestPlan,
    plan_scoped_tests,
)

# tests/e2e/ requires an authenticated CLI runtime and the `claude` binary,
# neither of which this fast lane's Postgres-only runner provisions. The
# generic scoped_runner allowlist has no opinion on one caller's runner
# shape, so this CI-only wrapper widens it here rather than teaching the
# library about CI's infrastructure.
CI_FALLBACK_ALLOWLIST: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",)


def decide_mode(plan: ScopedTestPlan) -> str:
    """Map a plan to the CI lane decision. Anything but a clean scope fails closed to `full`."""
    return "scoped" if plan.scope == "scoped" else "full"


def write_github_output(*, mode: str, test_paths: list[str]) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        handle.write(f"mode={mode}\n")
        handle.write(f"test_paths={json.dumps(test_paths)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="Base ref or SHA to diff against, e.g. a base branch SHA"
    )
    args = parser.parse_args(argv)

    plan = plan_scoped_tests(
        "HEAD", base=args.base, repo_dir=REPO_ROOT, fallback_allowlist=CI_FALLBACK_ALLOWLIST
    )
    print(plan.report())

    mode = decide_mode(plan)
    print(f"\n[CI DECISION] mode={mode}")

    write_github_output(mode=mode, test_paths=plan.test_paths if mode == "scoped" else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
