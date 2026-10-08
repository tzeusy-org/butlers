"""Compare genuine shard observations; no count-only or incomplete gain claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(before: dict, after: dict) -> dict:
    keys = (
        "lane",
        "shard",
        "manifest_digest",
        "compatibility_digest",
        "selected_node_digest",
        "selected_count",
    )
    identity = all(before.get(key) == after.get(key) for key in keys)
    population = set(before.get("nodes", {})) == set(after.get("nodes", {}))
    outcomes = population and all(
        {phase: value["outcome"] for phase, value in before["nodes"][node].items()}
        == {phase: value["outcome"] for phase, value in after["nodes"][node].items()}
        for node in before.get("nodes", {})
    )
    eligible = (
        identity and outcomes and before.get("complete") is True and after.get("complete") is True
    )
    return {
        "identity_equal": identity,
        "phase_outcomes_equal": outcomes,
        "eligible": eligible,
        "before_workers": before.get("effective_workers"),
        "after_workers": after.get("effective_workers"),
        "before_tracers": before.get("actual_tracers"),
        "after_tracers": after.get("actual_tracers"),
        "wall_clock_gain_claimed": False,
        "scope": "named shard only; coverage population/report and ten-run gate are separate",
    }


def compare_coverage(before: dict, after: dict) -> bool:
    """Normalize full filename/line/branch/context populations, not SQLite bytes."""

    def population(data: dict) -> dict:
        return {
            name: {
                key: value.get(key)
                for key in (
                    "executed_lines",
                    "missing_lines",
                    "executed_branches",
                    "missing_branches",
                    "contexts",
                )
            }
            for name, value in data["files"].items()
        }

    return population(before) == population(after)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
