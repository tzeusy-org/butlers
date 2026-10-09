"""Independent eleven-child actual-identity proof and truthful smoke derivation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ci_partition import read_json, reconcile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = read_json(args.inventory_dir / "inventory.json")
        assignment = read_json(args.inventory_dir / "assignment.json")
        carriers = {}
        for directory in args.receipts.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("unexpected child artifact")
            name = directory.name.removeprefix("ci-").removesuffix("-test-evidence")
            if name in carriers:
                raise ValueError("duplicate child carrier")
            carriers[name] = read_json(directory / "shard-observation.json")
        proof = reconcile(data, assignment, carriers)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "reconciliation.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
        # Output false triggers the ORIGINAL dedicated smoke invocation, never
        # a fictitious derived command or empty-selector release record.
        import os

        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"smoke_covered={str(proof['smoke_covered']).lower()}\n")
        if proof["smoke_covered"]:
            elapsed = [carriers[name]["test_step_elapsed_s"] for name in carriers]
            skipped = sorted(
                {
                    carrier["node_classes"][node]
                    for carrier in carriers.values()
                    for node, phases in carrier["nodes"].items()
                    if node in {value for values in data["smoke"].values() for value in values}
                    and any(phase["outcome"] == "skipped" for phase in phases.values())
                }
            )
            release = {
                "cmd": proof["actual_shard_commands"],
                "sha": data["identity"]["head"],
                "duration_s": max(elapsed),
                "duration_scope": (
                    "maximum actual child test-step duration; no dedicated smoke command timer"
                ),
                "status": "PASS",
                "exit_code": 0,
                "skipped_classes": skipped,
                **proof,
            }
            (args.output / "release-evidence.json").write_text(
                json.dumps(release, sort_keys=True) + "\n"
            )
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::complete eleven-child execution proof unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
