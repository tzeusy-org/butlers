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
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ci_partition import file_path, reconcile  # noqa: E402

from butlers.testing.scope_cost import (  # noqa: E402
    environment,
    measurement_species,
    runner_class,
    whole_file_cost,
)


def number(value: object) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError("cost timer unavailable")
    return float(value)


def build(bundles: list[dict], *, root: Path = ROOT) -> dict:
    if not isinstance(bundles, list) or not bundles:
        raise ValueError("complete compatible reference and affected measurement required")
    source = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10).decode().strip()
    )
    runs, samples, setup, files, tracers = [], [], [], {}, set()
    observed_environment = None
    classification = None
    hardware_ledger: dict[str, list[dict]] = {}

    def observe(receipt: dict, identity: dict, label: str) -> dict:
        nonlocal classification
        observed = receipt["cost_environment"]
        actual_class = runner_class(observed)
        if observed["configuration"] != environment(root)["configuration"] or (
            classification is not None and actual_class != classification
        ):
            raise ValueError("heavy runtime/configuration incompatible")
        if (
            receipt.get("cost_context")
            != hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
        ):
            raise ValueError("cost context incompatible")
        species = measurement_species(receipt)
        tracers.add(species)
        classification = actual_class
        model = observed["runtime"]["cpu_model_digest"]
        row = {
            "environment": observed,
            "run": str(identity["run"]),
            "attempt": str(identity["attempt"]),
            "source": identity["head"],
            "job": label,
            "workers": len(receipt["effective_workers"]),
            "instrumentation": species,
        }
        if (
            not row["run"].isdecimal()
            or not row["attempt"].isdecimal()
            or not re.fullmatch(r"[0-9a-f]{40}", row["source"])
        ):
            raise ValueError("cost source/run/attempt incompatible")
        hardware_ledger.setdefault(model, []).append(row)
        return row

    def measured_file(name: str, seconds: object, measurement: dict) -> None:
        actual_sha = hashlib.sha256((root / name).read_bytes()).hexdigest()
        sample = {
            "seconds": number(seconds),
            "model": measurement["environment"]["runtime"]["cpu_model_digest"],
            "measurement": measurement,
        }
        row = files.setdefault(name, {"sha256": actual_sha, "seconds": 0.0, "observations": []})
        row["observations"].append(sample)
        row["seconds"] = whole_file_cost(row["observations"])

    for bundle in bundles:
        run = bundle["run"]
        if not isinstance(run, str) or not run.isdecimal() or run in runs:
            raise ValueError("cost run identity unavailable")
        reconcile(
            bundle["inventory"], bundle["assignment"], bundle["receipts"], root=root, current=False
        )
        affected = bundle["affected"]
        if affected.get("kind") != "affected-cost.v1" or affected.get("schema") != 1:
            raise ValueError("cost context incompatible")
        paired = affected.get("cost_environment")
        if (
            not isinstance(paired, dict)
            or paired.get("configuration") != environment(root)["configuration"]
            or not isinstance(paired.get("runtime"), dict)
            or affected.get("cost_context")
            != hashlib.sha256(json.dumps(paired, sort_keys=True).encode()).hexdigest()
        ):
            raise ValueError("cost context incompatible")
        if observed_environment is None:
            observed_environment = paired
        if (
            affected.get("run") != bundle["affected_run"]
            or not str(bundle["affected_run"]).isdecimal()
        ):
            raise ValueError("cost source/run incompatible")
        names = affected.get("files")
        if not isinstance(names, list) or not names or len(names) != len(set(names)):
            raise ValueError("affected file population incompatible")
        for name in names:
            file_path(name, root)
        affected_source = affected["source_head"]
        if not isinstance(affected_source, str) or not re.fullmatch(
            r"[0-9a-f]{40}", affected_source
        ):
            raise ValueError("affected source incompatible")
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
        completed = [
            number(p["completed_s"])
            for phases in affected["nodes"].values()
            for p in phases.values()
        ]
        if (
            affected.get("selected_count") != len(affected["nodes"])
            or affected.get("selected_node_digest")
            != hashlib.sha256(
                json.dumps(sorted(affected["nodes"]), sort_keys=True).encode()
            ).hexdigest()
        ):
            raise ValueError("affected logical population incompatible")
        if set(affected.get("node_files", {}).values()) != set(affected["files"]):
            raise ValueError("affected file population incompatible")
        command = affected.get("command", [])
        if (
            not isinstance(command, list)
            or "-n" not in command
            or command.index("-n") + 1 >= len(command)
            or command[command.index("-n") + 1] != "auto"
        ):
            raise ValueError("affected command incompatible")
        step = number(affected["test_step_elapsed_s"])
        job = number(bundle["affected_job_seconds"])
        first, last = (
            number(affected["first_logical_test_s"]),
            number(affected["last_test_completed_s"]),
        )
        if not 0 < first <= min(completed) <= last == max(completed) <= step <= job:
            raise ValueError("affected clock ordering incompatible")
        # Sum serial phase costs without dividing by workers. Independently add
        # actual job setup plus pytest startup/finalization, not elapsed minus
        # parallel serial work (which could otherwise fabricate zero overhead).
        setup.append((job - step) + first + (step - last))
        measurement = observe(
            affected,
            {
                "run": affected["run"],
                "attempt": affected["attempt"],
                "head": affected_source,
            },
            "affected",
        )
        phase_files: dict[str, list[float]] = {}
        for node, phases in affected["nodes"].items():
            phase_files.setdefault(affected["node_files"][node], []).extend(
                number(phase["duration_s"]) for phase in phases.values()
            )
        durations = {name: math.fsum(values) for name, values in phase_files.items()}
        if affected.get("file_durations_s") != durations:
            raise ValueError("affected file phase costs incompatible")
        for name, seconds in durations.items():
            measured_file(name, seconds, measurement)
        clocks = bundle["heavy_job_seconds"]
        if set(clocks) != set(bundle["receipts"]):
            raise ValueError("heavy job clocks incomplete")
        samples.append(max(number(v) for v in clocks.values()))
        for label, receipt in bundle["receipts"].items():
            identity = receipt["inventory_identity"]
            if identity.get("python") != receipt["cost_environment"]["runtime"]["python"]:
                raise ValueError("heavy runtime/configuration incompatible")
            if str(identity["run"]) != run:
                raise ValueError("heavy run incompatible")
            command = receipt["command"]
            if "-n" not in command or command[command.index("-n") + 1] != "auto":
                raise ValueError("heavy worker policy incompatible")
            measurement = observe(receipt, identity, label)
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
                measured_file(name, seconds, measurement)
        runs.append(run)
    if len(tracers) != 1 or not tracers <= {"CTracer", "SysMonitor", "untraced"} or not files:
        raise ValueError("cost tracer/files incompatible")
    return {
        "schema": "test-scope-cost.v2",
        "context": hashlib.sha256(json.dumps(classification, sort_keys=True).encode()).hexdigest(),
        "environment": observed_environment,
        "runner_class": classification,
        "hardware_ledger": hardware_ledger,
        "hardware_qualification": (
            "finite empirical max over recorded known models; CPUs are not identical"
        ),
        "builder_environment": environment(root),
        "source_head": source,
        "reference": {
            "sample_count": len(runs),
            "qualification": "finite observed reference; natural ten-run outcome separate",
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
