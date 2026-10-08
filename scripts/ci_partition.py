"""Fresh pytest membership and deterministic whole-file CI assignment.

Weights are advisory. Only the source-owned collector's actual items establish
membership; complete run-bound receipts independently establish execution.
Opaque identities are privacy minimization, not authentication or anonymity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import secrets
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SELECTORS = {
    "unit": "not integration and not e2e and not nightly and not bench and not perf",
    "integration": "integration and not nightly and not bench and not perf",
}
DIMENSIONS = {"unit": 5, "integration": 6}
HEX = re.compile(r"[0-9a-f]{64}")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value


def file_path(name: str, root: Path) -> Path:
    if not isinstance(name, str) or "\\" in name or "::" in name:
        raise ValueError("invalid inventory file")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or path.suffix != ".py"
        or path.parts[0] not in {"tests", "roster"}
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("inventory file must be canonical repository-relative Python")
    candidate = root / name
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("inventory file is missing or escapes the checkout")
    return candidate


def checkout_identity(root: Path) -> dict:
    def git(*args):
        return (
            subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )

    head = git("rev-parse", "HEAD")
    workflow = os.environ.get("GITHUB_SHA")
    if workflow is not None and workflow != head:
        raise ValueError("inventory checkout does not match workflow")
    # Include working bytes for local conformance; actual hosted checkouts are
    # additionally bound to the exact committed tree and workflow attempt.
    inputs = {}
    for directory in ("src", "tests", "roster", "scripts", "alembic"):
        for path in sorted((root / directory).rglob("*.py")):
            if path.is_file():
                inputs[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("conftest.py", "pyproject.toml", "uv.lock"):
        path = root / name
        if path.is_file():
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "head": head,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "run": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "python": platform.python_version(),
        "config_digest": digest(inputs),
        "lock_digest": inputs.get("uv.lock"),
        "selectors": SELECTORS,
    }


def body_digest(data: dict) -> str:
    return digest({key: value for key, value in data.items() if key != "digest"})


def validate_inventory(data: dict, *, root: Path, current: bool = True) -> None:
    if data.get("schema") != "ci-inventory.v1" or data.get("digest") != body_digest(data):
        raise ValueError("inventory schema or body digest mismatch")
    if current and data.get("identity") != checkout_identity(root):
        raise ValueError("inventory source/config/attempt mismatch")
    if not isinstance(data.get("nonce"), str) or not HEX.fullmatch(data["nonce"]):
        raise ValueError("inventory nonce missing")
    if data.get("selectors") != SELECTORS or data.get("complete") is not True:
        raise ValueError("inventory collection incomplete or selector mismatch")
    if not isinstance(data.get("pytest_version"), str) or not data.get("plugins_digest"):
        raise ValueError("inventory installed collector identity missing")
    age = datetime.now(UTC) - datetime.fromisoformat(data["collected_at"])
    if not timedelta(0) <= age <= timedelta(hours=24):
        raise ValueError("inventory stale")
    if set(data["lanes"]) != set(DIMENSIONS):
        raise ValueError("inventory lane population mismatch")
    global_nodes = set()
    for lane, files in data["lanes"].items():
        if not isinstance(files, dict) or not files:
            raise ValueError("required lane empty")
        nodes = []
        for name, values in files.items():
            file_path(name, root)
            if not isinstance(values, list) or not values or values != sorted(set(values)):
                raise ValueError("inventory nodes malformed or duplicated")
            if any(not isinstance(value, str) or not HEX.fullmatch(value) for value in values):
                raise ValueError("inventory contains nonopaque item identity")
            nodes.extend(values)
        if len(nodes) != len(set(nodes)) or global_nodes.intersection(nodes):
            raise ValueError("inventory item appears in multiple files or lanes")
        global_nodes.update(nodes)
    if not isinstance(data["smoke"], dict):
        raise ValueError("smoke selector evidence missing")
    for name, values in data["smoke"].items():
        file_path(name, root)
        if not name.startswith("tests/") or name.startswith("tests/e2e/"):
            raise ValueError("smoke path differs from dedicated selector")
        if values != sorted(set(values)) or any(not HEX.fullmatch(value) for value in values):
            raise ValueError("smoke identities malformed")


def collect_inventory(*, root: Path = ROOT) -> dict:
    """One actual pytest process, replacing its default marker expression explicitly."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ci-inventory-") as directory:
        output = Path(directory) / "items.json"
        nonce = secrets.token_hex(32)
        environment = {
            **os.environ,
            "CI_INVENTORY_OUTPUT": str(output),
            "CI_INVENTORY_NONCE": nonce,
            "CI_INVENTORY_ROOT": str(root.resolve()),
            "PYTHONPATH": os.pathsep.join(
                [str(Path(__file__).resolve().parent), os.environ.get("PYTHONPATH", "")]
            ),
        }
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "roster/",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "-m",
            "",
            "-p",
            "ci_inventory_collector",
            "-p",
            "no:cacheprovider",
        ]
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True)
        if result.returncode != 0 or not output.is_file():
            # No arbitrary collection/failure/parameter output enters a receipt.
            raise ValueError("actual inventory collection failed")
        collected = read_json(output)
    data = {
        **collected,
        "schema": "ci-inventory.v1",
        "identity": checkout_identity(root),
        "selectors": SELECTORS,
        "nonce": nonce,
        "collected_at": datetime.now(UTC).isoformat(),
    }
    data["digest"] = body_digest(data)
    validate_inventory(data, root=root)
    return data


def check_budgets(data: dict, *, root: Path = ROOT) -> dict[str, int]:
    from check_test_budget import compare, load_baseline

    validate_inventory(data, root=root)
    counts = {lane: sum(map(len, files.values())) for lane, files in data["lanes"].items()}
    if compare(counts, load_baseline(root / "scripts/test-budget-baseline.json")):
        raise ValueError("fresh inventory exceeds unchanged lane budgets")
    return counts


def partition(data: dict, weights: dict, *, root: Path = ROOT) -> dict:
    """Deterministic LPT; advisory timing can never suppress collected files."""
    validate_inventory(data, root=root)
    degraded = False
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(weights["collected_at"])
        compatible = (
            weights["schema"] == "ci-weights.v1"
            and weights["config_digest"] == data["identity"]["config_digest"]
            and timedelta(0) <= age <= timedelta(days=14)
        )
    except (KeyError, TypeError, ValueError):
        compatible = False
    assignment = {}
    for lane, files in data["lanes"].items():
        candidate_lanes = weights.get("lanes") if compatible else None
        candidates = candidate_lanes.get(lane, {}) if isinstance(candidate_lanes, dict) else {}
        if not isinstance(candidates, dict):
            candidates = {}
        values = {
            name: value
            for name, value in candidates.items()
            if name in files and type(value) in (int, float) and math.isfinite(value) and value > 0
        }
        fallback = statistics.median(values.values()) if values else 1.0
        costs = {name: values.get(name, fallback) for name in files}
        degraded |= set(values) != set(files)
        bins = [
            {"index": index, "files": [], "predicted_seconds": 0.0}
            for index in range(1, DIMENSIONS[lane] + 1)
        ]
        for name in sorted(files, key=lambda item: (-costs[item], item)):
            chosen = min(bins, key=lambda item: (item["predicted_seconds"], item["index"]))
            chosen["files"].append(name)
            chosen["predicted_seconds"] += costs[name]
        if any(not item["files"] for item in bins):
            raise ValueError("required computed shard empty")
        assignment[lane] = [{**item, "files": sorted(item["files"])} for item in bins]
    result = {
        "schema": "ci-assignment.v1",
        "inventory_digest": data["digest"],
        "identity": data["identity"],
        "nonce": data["nonce"],
        "shards": assignment,
        "weights_degraded": degraded,
    }
    result["digest"] = body_digest(result)
    validate_assignment(data, result, root=root)
    return result


def validate_assignment(
    data: dict, assignment: dict, *, root: Path = ROOT, current: bool = True
) -> None:
    validate_inventory(data, root=root, current=current)
    if assignment.get("schema") != "ci-assignment.v1" or assignment.get("digest") != body_digest(
        assignment
    ):
        raise ValueError("assignment schema/body mismatch")
    if any(
        assignment.get(key) != expected
        for key, expected in (
            ("inventory_digest", data["digest"]),
            ("identity", data["identity"]),
            ("nonce", data["nonce"]),
        )
    ):
        raise ValueError("assignment does not bind current inventory")
    if set(assignment["shards"]) != set(DIMENSIONS):
        raise ValueError("assignment lane mismatch")
    for lane, count in DIMENSIONS.items():
        bins = assignment["shards"][lane]
        if [item["index"] for item in bins] != list(range(1, count + 1)):
            raise ValueError("assignment indexes mismatch")
        names = [name for item in bins for name in item["files"]]
        if len(names) != len(set(names)) or set(names) != set(data["lanes"][lane]):
            raise ValueError("assignment omits or duplicates actual collected file")
        if any(not item["files"] or item["files"] != sorted(item["files"]) for item in bins):
            raise ValueError("assignment files empty or unsorted")


def assigned_files(
    data: dict, assignment: dict, *, lane: str, index: int, root: Path = ROOT, current: bool = True
) -> list[str]:
    validate_assignment(data, assignment, root=root, current=current)
    if lane not in DIMENSIONS or type(index) is not int or not 1 <= index <= DIMENSIONS[lane]:
        raise ValueError("unknown computed shard")
    return assignment["shards"][lane][index - 1]["files"]


def reconcile(
    data: dict,
    assignment: dict,
    receipts: dict[str, dict],
    *,
    root: Path = ROOT,
    current: bool = True,
) -> dict:
    validate_assignment(data, assignment, root=root, current=current)
    expected_carriers = {
        f"{lane}-{index}" for lane, count in DIMENSIONS.items() for index in range(1, count + 1)
    }
    if set(receipts) != expected_carriers:
        raise ValueError("complete eleven child carriers required")
    seen = set()
    commands = []
    for lane, count in DIMENSIONS.items():
        for index in range(1, count + 1):
            receipt = receipts[f"{lane}-{index}"]
            files = assigned_files(
                data, assignment, lane=lane, index=index, root=root, current=current
            )
            nodes = {node for name in files for node in data["lanes"][lane][name]}
            if any(
                receipt.get(key) != value
                for key, value in (
                    ("inventory_digest", data["digest"]),
                    ("assignment_digest", assignment["digest"]),
                    ("nonce", data["nonce"]),
                    ("inventory_identity", data["identity"]),
                    ("lane", lane),
                    ("shard", index),
                    ("pytest_exit", 0),
                    ("complete", True),
                )
            ):
                raise ValueError("child provenance or verdict mismatch")
            if (
                set(receipt["nodes"]) != nodes
                or receipt["selected_count"] != len(nodes)
                or receipt["selected_node_digest"] != digest(sorted(nodes))
            ):
                raise ValueError("child differs from complete actual inventory identities")
            if receipt.get("logical_starts") != {node: 1 for node in nodes} or seen.intersection(
                nodes
            ):
                raise ValueError("duplicate or absent logical execution")
            if receipt["node_files"] != {
                node: name for name in files for node in data["lanes"][lane][name]
            }:
                raise ValueError("child file/item bindings differ")
            reconstructed = {name: 0.0 for name in files}
            for node, phases in receipt["nodes"].items():
                if (
                    not isinstance(phases, dict)
                    or not {"setup", "teardown"} <= phases.keys()
                    or set(phases) - {"setup", "call", "teardown"}
                    or (phases["setup"]["outcome"] == "passed" and "call" not in phases)
                ):
                    raise ValueError("child phase incomplete")
                for value in phases.values():
                    if value["outcome"] not in {"passed", "skipped"}:
                        raise ValueError("child phase not successful")
                    for field in ("duration_s", "completed_s"):
                        if (
                            type(value.get(field)) not in (float, int)
                            or not math.isfinite(value[field])
                            or value[field] < 0
                        ):
                            raise ValueError("child phase timer malformed")
                    reconstructed[receipt["node_files"][node]] += value["duration_s"]
            if receipt.get("file_durations_s") != reconstructed:
                raise ValueError("child aggregate not reconstructed from actual phases")
            command = receipt.get("command")
            if (
                not isinstance(command, list)
                or not all(isinstance(value, str) for value in command)
                or command[1:3] != ["-m", "pytest"]
                or "--" not in command
                or set(command[command.index("--") + 1 :]) != set(files)
                or len(command[command.index("--") + 1 :]) != len(files)
            ):
                raise ValueError("child actual command does not bind assigned files")
            marker = command.index("-m", 3) if "-m" in command[3:] else None
            if marker is None or command[marker + 1] != SELECTORS[lane]:
                raise ValueError("child command marker differs")
            seen.update(nodes)
            commands.append(receipt["command"])
    smoke = {node for values in data["smoke"].values() for node in values}
    return {
        "complete": True,
        "selected_count": len(seen),
        "smoke_covered": bool(smoke) and smoke <= seen,
        "smoke_selector": "uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short",
        "actual_shard_commands": commands,
        "inventory_digest": data["digest"],
        "assignment_digest": assignment["digest"],
        "identity": data["identity"],
        "evidence_mode": "derived-full-lanes",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = collect_inventory()
        counts = check_budgets(data)
        weights_path = ROOT / ".github/ci-test-weights.json"
        assignment = partition(data, read_json(weights_path) if weights_path.is_file() else {})
        args.output.mkdir(parents=True, exist_ok=True)
        for name, value in (("inventory.json", data), ("assignment.json", assignment)):
            (args.output / name).write_text(json.dumps(value, sort_keys=True) + "\n")
        print(
            json.dumps(
                {
                    "counts": counts,
                    "inventory_digest": data["digest"],
                    "assignment_digest": assignment["digest"],
                }
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "::error::fresh inventory/partition refused; inspect actual collection locally",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
