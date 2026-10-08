"""Replay frozen public paths/refs without manufacturing scope or elapsed evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing.changed_files import ChangedFiles  # noqa: E402
from butlers.testing.scoped_runner import (  # noqa: E402
    FULL_SUITE_FALLBACK_ALLOWLIST,
    _plan_for_changed_files,
)


def replay(rows: list[dict], *, root: Path = ROOT) -> dict:
    outcomes = []
    for row in rows:
        files = row.get("files")
        if (
            not isinstance(files, list)
            or not files
            or any(not isinstance(name, str) for name in files)
        ):
            outcomes.append({"id": row.get("id"), "evidence": "UNKNOWN", "reason": "UNSAFE_DIFF"})
            continue
        base, head = row.get("base"), row.get("head")
        qualified = isinstance(base, str) and isinstance(head, str)
        plan = _plan_for_changed_files(
            ChangedFiles(
                files=files,
                base_ref=base if qualified else "unavailable-replay-base",
                head_ref=head if qualified else "HEAD",
                sources=("frozen-public-corpus",),
            ),
            repo_dir=root,
            fallback_allowlist=FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",),
        )
        outcomes.append(
            {
                "id": row.get("id"),
                "evidence": "qualified"
                if qualified and "BASE_UNAVAILABLE" not in plan.reason_codes
                else "UNKNOWN",
                "decision": plan.data(),
            }
        )
    counts = Counter(r.get("decision", {}).get("mode", "unknown") for r in outcomes)
    scoped = sum(
        r["evidence"] == "qualified" and r.get("decision", {}).get("mode") == "scoped"
        for r in outcomes
    )
    return {
        "schema": "planner-replay.v1",
        "planner_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
        .decode()
        .strip(),
        "records": outcomes,
        "counts": dict(counts),
        "qualified_scoped": scoped,
        "unknown": sum(r["evidence"] == "UNKNOWN" for r in outcomes),
        "limits": ("Candidate source decisions only; actual testcase, timing "
                   "and protected evidence separate"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({name: result[name] for name in ("counts", "qualified_scoped", "unknown")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
