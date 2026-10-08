"""Compare genuine shard observations; no count-only or incomplete gain claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path


def valid_observation(data: dict) -> bool:
    """Require the observer's complete population, provenance and phase contract."""
    try:
        if data["complete"] is not True or data["pytest_exit"] != 0:
            return False
        origin = tuple(data[key] for key in ("source", "run", "attempt"))
        if origin != ("local", "local", "local") and not (
            isinstance(origin[0], str)
            and re.fullmatch(r"[0-9a-f]{40}", origin[0])
            and all(isinstance(value, str) and value.isdigit() for value in origin[1:])
        ):
            return False
        age = datetime.now(UTC) - datetime.fromisoformat(data["collected_at"])
        if not timedelta(0) <= age <= timedelta(days=14):
            return False
        if data["lane"] not in {"unit", "integration"} or type(data["shard"]) is not int:
            return False
        if not 1 <= data["shard"] <= 5:
            return False
        for key in ("manifest_digest", "compatibility_digest", "selected_node_digest"):
            if not isinstance(data[key], str) or not re.fullmatch(r"[0-9a-f]{64}", data[key]):
                return False
        nodes = data["nodes"]
        if not isinstance(nodes, dict) or not nodes or type(data["selected_count"]) is not int:
            return False
        if data["selected_count"] != len(nodes) or set(data["node_files"]) != set(nodes):
            return False
        expected = hashlib.sha256(json.dumps(sorted(nodes), sort_keys=True).encode()).hexdigest()
        if expected != data["selected_node_digest"]:
            return False
        files = data["files"]
        if not isinstance(files, list) or not files or len(files) != len(set(files)):
            return False
        workers, tracers = data["effective_workers"], data["actual_tracers"]
        if (
            not isinstance(workers, list)
            or not workers
            or any(
                not isinstance(worker, str) or not re.fullmatch(r"gw\d+|controller", worker)
                for worker in workers
            )
        ):
            return False
        if not isinstance(tracers, list) or any(
            tracer not in {"CTracer", "SysMonitor"} for tracer in tracers
        ):
            return False
        resources = data["worker_resources"]
        if not isinstance(resources, dict) or not resources or not set(resources) <= set(workers):
            return False
        for resource in resources.values():
            if resource["tracer"] not in {None, "CTracer", "SysMonitor"}:
                return False
            for key in ("cpu_s", "max_rss"):
                value = resource[key]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    return False
        for node, phases in nodes.items():
            if not re.fullmatch(r"[0-9a-f]{64}", node) or data["node_files"][node] not in files:
                return False
            if not {"setup", "teardown"} <= phases.keys():
                return False
            if phases["setup"]["outcome"] == "passed" and "call" not in phases:
                return False
            for phase, observed in phases.items():
                if phase not in {"setup", "call", "teardown"}:
                    return False
                if observed["outcome"] not in {"passed", "skipped"}:
                    return False
                for field in ("duration_s", "completed_s"):
                    value = observed[field]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def compare(before: dict, after: dict) -> dict:
    valid = valid_observation(before) and valid_observation(after)
    if not valid:
        return {
            "evidence_valid": False,
            "identity_equal": False,
            "phase_outcomes_equal": False,
            "eligible": False,
            "wall_clock_gain_claimed": False,
            "scope": "Missing or invalid observer evidence; no comparison credit",
        }
    keys = (
        "lane",
        "shard",
        "manifest_digest",
        "compatibility_digest",
        "selected_node_digest",
        "selected_count",
    )
    identity = valid and all(before.get(key) == after.get(key) for key in keys)
    identity = identity and (before["source"] == "local") == (after["source"] == "local")
    identity = identity and before["node_files"] == after["node_files"]
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
        "evidence_valid": valid,
        "identity_equal": identity,
        "phase_outcomes_equal": outcomes,
        "eligible": eligible,
        "evidence_scope": (
            "local miniature conformance" if before["source"] == "local" else "hosted observation"
        ),
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
