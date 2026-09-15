#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Per-lane collected-test budget ratchet (bu-r5mnn).

Under agentic development every bead adds tests and none removes them:
the backend suite went from roughly 9.4k to 17.4k collected tests in twelve
weeks and CI minutes tracked the count. This gate makes suite growth a
decision instead of a side effect. For each CI lane in
``scripts/check_ci_test_shards.py`` it counts the node ids the lane's marker
selection collects today and compares the count against the budget frozen in
``scripts/test-budget-baseline.json``. A lane over budget fails with the
remedy: condense tests in the same PR, or raise the budget deliberately with
``--update-baseline`` and state the net test delta and why in the PR body.

``--update-baseline`` rewrites every lane's budget to the current count plus
5% headroom (rounded up) so the next raise is again a conscious act.
``--print`` reports counts and budgets without failing.

Usage:
  uv run python scripts/check_test_budget.py                    # gate
  uv run python scripts/check_test_budget.py --print            # report only
  uv run python scripts/check_test_budget.py --update-baseline  # re-freeze

Exit codes:
  0  every lane is within budget (or --print / --update-baseline ran)
  1  at least one lane exceeds its budget
  2  baseline file is missing/malformed, or pytest collection failed
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import check_ci_test_shards as shards  # noqa: E402

BASELINE_PATH = SCRIPTS_DIR / "test-budget-baseline.json"
HEADROOM = 0.05
BASELINE_NOTE = (
    "Per-lane collected-test budget (scripts/check_test_budget.py). A lane over "
    "budget fails check-preflight: condense tests in the same PR, or run "
    "`uv run python scripts/check_test_budget.py --update-baseline` and state the "
    "net test delta (Tests: +added ~extended -removed) and why in the PR body. "
    "Budgets are current count + 5% at the last update; never raise them silently."
)


class BudgetError(Exception):
    """Configuration or collection failure (exit 2), distinct from an over-budget lane."""


def load_baseline(path: Path) -> dict[str, int]:
    """Return ``{lane: budget}`` from the baseline JSON, failing closed on any malformed shape."""
    if not path.is_file():
        raise BudgetError(f"missing test budget baseline {path}; create it with --update-baseline")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BudgetError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise BudgetError(f"{path}: expected a JSON object at the top level")

    budgets: dict[str, int] = {}
    for lane, entry in payload.items():
        if lane == "note":
            continue
        budget = entry.get("budget") if isinstance(entry, dict) else None
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise BudgetError(f"{path}: lane {lane!r} needs an integer 'budget' >= 0")
        budgets[lane] = budget
    if not budgets:
        raise BudgetError(f"{path}: no lane budgets found")
    return budgets


def collect_counts(*, repo_root: Path = REPO_ROOT) -> dict[str, int]:
    """Count the node ids each CI lane's marker selection collects right now."""
    counts: dict[str, int] = {}
    for lane in shards.LANES:
        try:
            counts[lane] = len(shards.collect_lane_node_ids(lane, repo_root=repo_root))
        except RuntimeError as exc:
            raise BudgetError(f"{lane}: {exc}") from exc
        if counts[lane] == 0:
            raise BudgetError(f"{lane}: marker selection collected zero tests")
    return counts


def propose_budgets(counts: dict[str, int], *, headroom: float = HEADROOM) -> dict[str, int]:
    """Budget = current count plus headroom, rounded up, so growth needs a decision."""
    return {lane: math.ceil(count * (1 + headroom)) for lane, count in counts.items()}


def compare(counts: dict[str, int], budgets: dict[str, int]) -> list[str]:
    """Return one ``::error::`` line per violation; an empty list means within budget."""
    errors: list[str] = []
    for lane, count in sorted(counts.items()):
        budget = budgets.get(lane)
        if budget is None:
            errors.append(
                f"::error::test budget: lane {lane!r} has no budget in "
                f"{BASELINE_PATH.name}; add it with --update-baseline"
            )
            continue
        if count > budget:
            errors.append(
                f"::error::test budget: lane {lane!r} collects {count} tests, "
                f"budget is {budget} (+{count - budget}). Condense tests in this PR, "
                "or raise the budget with `uv run python scripts/check_test_budget.py "
                "--update-baseline` and state the net test delta and why in the PR body."
            )
    for lane in sorted(set(budgets) - set(counts)):
        errors.append(
            f"::error::test budget: baseline lane {lane!r} is not a CI lane any more; "
            "remove it with --update-baseline"
        )
    return errors


def write_baseline(path: Path, *, counts: dict[str, int], budgets: dict[str, int]) -> None:
    payload: dict[str, object] = {"note": BASELINE_NOTE}
    for lane in sorted(budgets):
        payload[lane] = {"budget": budgets[lane], "collected_at_update": counts[lane]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def format_report(counts: dict[str, int], budgets: dict[str, int]) -> str:
    lines = []
    for lane, count in sorted(counts.items()):
        budget = budgets.get(lane)
        if budget is None:
            lines.append(f"{lane}: collected={count} budget=missing")
        else:
            lines.append(f"{lane}: collected={count} budget={budget} headroom={budget - count}")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="baseline JSON path")
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="repository root to collect in"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite every lane's budget to the current count + 5%% (ceil)",
    )
    mode.add_argument(
        "--print",
        "--dry-run",
        dest="print_only",
        action="store_true",
        help="print counts and budgets without failing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        counts = collect_counts(repo_root=args.repo_root)
        if args.update_baseline:
            budgets = propose_budgets(counts)
            write_baseline(args.baseline, counts=counts, budgets=budgets)
            print(f"wrote {args.baseline}")
            print(format_report(counts, budgets))
            return 0
        budgets = load_baseline(args.baseline)
    except BudgetError as exc:
        print(f"check_test_budget: {exc}", file=sys.stderr)
        return 2

    print(format_report(counts, budgets))
    errors = compare(counts, budgets)
    if args.print_only:
        for line in errors:
            print(line.replace("::error::", "over budget: "))
        return 0
    for line in errors:
        print(line)
    if errors:
        return 1
    print("OK: every CI lane is within its collected-test budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
