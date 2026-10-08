"""Build a reviewed cost-profile candidate from complete official measurement bundles.

Inputs must be independently retained official receipts plus actual job-clock
metadata. This reader validates compatibility/population; it does not authenticate
a caller's JSON as hosted measurement. Output remains a reviewable candidate,
never installs itself and never turns advisory weights into upper-cost evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ci_partition import reconcile  # noqa: E402

from butlers.testing.scope_cost import context  # noqa: E402


def number(value: object) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError("cost timer unavailable")
    return float(value)


def build(bundles: list[dict], *, root: Path = ROOT) -> dict:
    if not isinstance(bundles, list) or len(bundles) < 10:
        raise ValueError("ten compatible complete runs required")
    source = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10).decode().strip()
    )
    runs, samples, setup, files, tracers = [], [], [], {}, set()
    for bundle in bundles:
        run = bundle["run"]
        if not isinstance(run, str) or not run.isdecimal() or run in runs:
            raise ValueError("cost run identity unavailable")
        reconcile(
            bundle["inventory"], bundle["assignment"], bundle["receipts"], root=root, current=False
        )
        affected = bundle["affected"]
        if affected.get("kind") != "affected-cost.v1" or affected.get("cost_context") != context(
            root
        ):
            raise ValueError("cost context incompatible")
        if (
            affected.get("run") != bundle["affected_run"]
            or not str(bundle["affected_run"]).isdecimal()
        ):
            raise ValueError("cost source/run incompatible")
        affected_source = affected["source_head"]
        if affected.get("file_hashes") != {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in affected["files"]
        }:
            raise ValueError("affected file bodies incompatible")
        for name in affected["files"]:
            raw = subprocess.check_output(
                ["git", "show", f"{affected_source}:{name}"],
                cwd=root,
                timeout=10,
                stderr=subprocess.DEVNULL,
            )
            if hashlib.sha256(raw).hexdigest() != affected["file_hashes"][name]:
                raise ValueError("affected source bodies incompatible")
        if affected.get("complete") is not True or affected.get("pytest_exit") != 0:
            raise ValueError("affected measurement incomplete")
        if affected.get("worker_policy") != "auto" or not affected.get("nodes"):
            raise ValueError("affected worker/population unavailable")
        if affected.get("logical_starts") != {n: 1 for n in affected["nodes"]}:
            raise ValueError("affected logical population incompatible")
        phase_total = 0.0
        for node, phases in affected["nodes"].items():
            if not {"setup", "teardown"} <= phases.keys() or set(phases) - {
                "setup",
                "call",
                "teardown",
            }:
                raise ValueError("affected phases incomplete")
            if phases["setup"]["outcome"] == "passed" and "call" not in phases:
                raise ValueError("affected phases incomplete")
            for phase in phases.values():
                if phase["outcome"] not in {"passed", "skipped"}:
                    raise ValueError("affected phase unsuccessful")
                phase_total += number(phase["duration_s"])
        step = number(affected["test_step_elapsed_s"])
        job = number(bundle["affected_job_seconds"])
        first, last = (
            number(affected["first_logical_test_s"]),
            number(affected["last_test_completed_s"]),
        )
        if not 0 < first <= last <= step <= job:
            raise ValueError("affected clock ordering incompatible")
        # Sum serial phase costs without dividing by workers. Independently add
        # actual job setup plus pytest startup/finalization, not elapsed minus
        # parallel serial work (which could otherwise fabricate zero overhead).
        setup.append((job - step) + first + (step - last))
        tracers.update(affected["actual_tracers"])
        clocks = bundle["heavy_job_seconds"]
        if set(clocks) != set(bundle["receipts"]):
            raise ValueError("heavy job clocks incomplete")
        samples.append(max(number(v) for v in clocks.values()))
        for receipt in bundle["receipts"].values():
            identity = receipt["inventory_identity"]
            if str(identity["run"]) != run:
                raise ValueError("heavy run incompatible")
            command = receipt["command"]
            if "-n" not in command or command[command.index("-n") + 1] != "auto":
                raise ValueError("heavy worker policy incompatible")
            tracers.update(receipt["actual_tracers"])
            for name, seconds in receipt["file_durations_s"].items():
                raw = subprocess.check_output(
                    ["git", "show", f"{identity['head']}:{name}"],
                    cwd=root,
                    timeout=10,
                    stderr=subprocess.DEVNULL,
                )
                actual_sha = hashlib.sha256((root / name).read_bytes()).hexdigest()
                if hashlib.sha256(raw).hexdigest() != actual_sha:
                    raise ValueError("heavy file bodies incompatible")
                row = {
                    "sha256": actual_sha,
                    "seconds": number(seconds),
                }
                if name in files:
                    row["seconds"] = max(row["seconds"], files[name]["seconds"])
                files[name] = row
        runs.append(run)
    if len(tracers) != 1 or not tracers <= {"CTracer", "SysMonitor"} or not files:
        raise ValueError("cost tracer/files incompatible")
    return {
        "schema": "test-scope-cost.v1",
        "context": context(root),
        "source_head": source,
        "reference": {
            "workers": "auto",
            "tracer": next(iter(tracers)),
            "runs": runs,
            "heavy_shard_seconds": samples,
            "affected_setup_seconds": max(setup),
        },
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(json.loads(args.input.read_text()))
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print("Complete compatible cost-profile candidate produced; review required")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Cost profile UNKNOWN: complete compatible measurement unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
