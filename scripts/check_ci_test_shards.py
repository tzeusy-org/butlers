#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Run and verify the file-level backend CI test shards.

The backend CI lanes use freshly collected whole-file assignments instead of
node-id or keyword shards.  ``verify`` derives each lane's current pytest
selection from its marker expression, then proves the assignment selects every
test file and test node exactly once.  ``run`` is the sole selector used by
the workflow, so a marker or load-distribution change cannot drift between
shards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class LaneConfig:
    """The CI-owned pytest selection and execution contract for one lane."""

    marker: str
    shard_count: int
    maxfail: int
    ignore_e2e: bool
    workers: str


LANES: dict[str, LaneConfig] = {
    "unit": LaneConfig(
        marker="not integration and not e2e and not nightly and not bench and not perf",
        shard_count=5,
        maxfail=1,
        ignore_e2e=True,
        workers="3",
    ),
    "integration": LaneConfig(
        marker="integration and not nightly and not bench and not perf",
        shard_count=6,
        maxfail=5,
        ignore_e2e=False,
        workers="auto",
    ),
}


def _lane_config(lane: str) -> LaneConfig:
    try:
        return LANES[lane]
    except KeyError as exc:
        raise ValueError(f"Unknown CI test lane {lane!r}; expected one of {sorted(LANES)}") from exc


def _collect_node_ids(
    *, paths: list[str], marker: str, ignore_e2e: bool, repo_root: Path
) -> set[str]:
    """Collect the exact marker-selected node ids without executing tests."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "--collect-only",
        "-q",
        "-n",
        "0",
        "-m",
        marker,
        "-p",
        "no:cacheprovider",
    ]
    if ignore_e2e:
        command.append("--ignore=tests/e2e")
    result = subprocess.run(  # noqa: S603
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "pytest --collect-only failed while verifying CI test shards; "
            f"exit={result.returncode}. Run the selector locally for full pytest diagnostics."
        )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "-", " "))
    }


def collect_lane_node_ids(lane: str, *, repo_root: Path = REPO_ROOT) -> set[str]:
    """Collect every node id the CI marker selection for ``lane`` picks up today.

    Shared by ``verify`` and ``scripts/check_test_budget.py`` so the budget
    ratchet counts exactly the population the shards run.
    """
    config = _lane_config(lane)
    return _collect_node_ids(
        paths=[], marker=config.marker, ignore_e2e=config.ignore_e2e, repo_root=repo_root
    )


def _node_file(node_id: str) -> str:
    return node_id.split("::", maxsplit=1)[0]


def verify(*, repo_root: Path = REPO_ROOT) -> dict[str, tuple[int, int]]:
    """One fresh collector supplies exact cover and both unchanged lane budgets."""
    from ci_partition import check_budgets, collect_inventory, partition

    inventory = collect_inventory(root=repo_root)
    check_budgets(inventory, root=repo_root)
    weights = repo_root / ".github/ci-test-weights.json"
    partition(
        inventory, json.loads(weights.read_text()) if weights.is_file() else {}, root=repo_root
    )
    results = {
        lane: (sum(map(len, files.values())), len(files))
        for lane, files in inventory["lanes"].items()
    }
    for lane, (node_count, file_count) in results.items():
        print(
            f"OK: {lane} computed inventory selects {node_count} test(s) "
            f"across {file_count} file(s) exactly once."
        )
    return results


def _coverage_enabled() -> bool:
    """CI supplies exact 0/1; standalone calls retain coverage by default."""
    value = os.environ.get("CI_COVERAGE", "1")
    if value not in {"0", "1"}:
        raise ValueError("CI_COVERAGE must be exactly 0 or 1")
    return value == "1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def timing_context(*, files: list[str], repo_root: Path, lane: str, shard: int) -> dict:
    """Bind advisory timings to the complete Python/import/dependency topology."""
    names = [
        str(path.relative_to(repo_root))
        for directory in ("src", "tests", "roster", "scripts", "alembic")
        for path in (repo_root / directory).rglob("*.py")
    ]
    names += ["conftest.py", "pyproject.toml", "uv.lock"]
    bodies = {
        name: hashlib.sha256((repo_root / name).read_bytes()).hexdigest()
        for name in names
        if (repo_root / name).is_file()
    }
    return {
        "source": os.environ.get("GITHUB_SHA", "local"),
        "run": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "lane": lane,
        "shard": shard,
        "manifest_digest": _digest(files),
        "compatibility_digest": _digest([bodies, platform.python_version()]),
        "cpu_affinity": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "logical_cpus": os.cpu_count(),
    }


def duration_order(
    files: list[str], *, context: dict, receipt: Path | None, repo_root: Path
) -> tuple[list[str], str]:
    """Use only complete, compatible, independently re-collected timing evidence."""
    if receipt is None:
        return files, "unknown:missing"
    try:

        def unique(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError("duplicate")
                result[name] = value
            return result

        data = json.loads(receipt.read_text(), object_pairs_hook=unique)
        if type(data["complete"]) is not bool or any(
            type(data[key]) is not int for key in ("pytest_exit", "selected_count", "shard")
        ):
            raise ValueError("metadata")
        keys = ("lane", "shard", "manifest_digest", "compatibility_digest")
        if any(data[k] != context[k] for k in keys) or data["complete"] is not True:
            raise ValueError("incompatible")
        if (
            any(not isinstance(data[key], str) for key in ("source", "run", "attempt"))
            or len(data["source"]) != 40
            or any(c not in "0123456789abcdef" for c in data["source"])
            or not data["run"].isdigit()
            or not data["attempt"].isdigit()
        ):
            raise ValueError("origin")
        age = datetime.now(UTC) - datetime.fromisoformat(data["collected_at"])
        if not timedelta(0) <= age <= timedelta(days=14):
            raise ValueError("stale")
        values = data["file_durations_s"]
        if set(values) != set(files) or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values.values()
        ):
            raise ValueError("duration")
        nodes = _collect_node_ids(
            paths=files,
            marker=_lane_config(context["lane"]).marker,
            ignore_e2e=_lane_config(context["lane"]).ignore_e2e,
            repo_root=repo_root,
        )
        from ci_shard_observer import node_digest

        expected = sorted(node_digest(node, data.get("nonce")) for node in nodes)
        if data["selected_node_digest"] != _digest(expected) or data["selected_count"] != len(
            nodes
        ):
            raise ValueError("population")
        if set(data["nodes"]) != set(expected):
            raise ValueError("identity")
        if data["pytest_exit"] != 0 or set(data["node_files"]) != set(expected):
            raise ValueError("verdict")
        reconstructed = {name: 0.0 for name in files}
        for node, phases in data["nodes"].items():
            name = data["node_files"][node]
            if name not in reconstructed or "setup" not in phases or "teardown" not in phases:
                raise ValueError("phase")
            if phases["setup"]["outcome"] == "passed" and "call" not in phases:
                raise ValueError("missing call")
            for phase, observed in phases.items():
                if phase not in {"setup", "call", "teardown"}:
                    raise ValueError("phase")
                if observed["outcome"] not in {"passed", "skipped"}:
                    raise ValueError("outcome")
                for field in ("duration_s", "completed_s"):
                    value = observed[field]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("phase value")
                reconstructed[name] += observed["duration_s"]
        if any(not math.isclose(values[name], reconstructed[name]) for name in files):
            raise ValueError("aggregate")
        return sorted(files, key=lambda name: (-values[name], name)), "compatible"
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return files, "unknown:invalid"


def unit_workers(lane: str) -> str:
    """CI experiment override; ordinary/local defaults and integration remain unchanged."""
    if lane != "unit":
        return _lane_config(lane).workers
    value = os.environ.get("CI_UNIT_WORKERS", "3")
    if value not in {"3", "4", "auto"}:
        raise ValueError("CI_UNIT_WORKERS must be 3, 4, or auto")
    return value


def coverage_core() -> str:
    """Refuse an unsupported candidate instead of reporting a fallback as sysmon."""
    value = os.environ.get("CI_COVERAGE_CORE", "ctrace")
    if value not in {"ctrace", "sysmon"}:
        raise ValueError("CI_COVERAGE_CORE must be ctrace or sysmon")
    import coverage

    cov = coverage.Coverage()
    cov.set_option("run:core", value)
    cov.start()
    try:
        actual = cov._collector.tracer_name()
        expected = "SysMonitor" if value == "sysmon" else "CTracer"
        if actual != expected:
            raise ValueError("requested coverage core was not installed")
    finally:
        cov.stop()
    return value


def run_shard(
    *,
    lane: str,
    shard: int,
    repo_root: Path,
    coverage_file: Path | None,
    evidence_dir: Path,
    inventory_dir: Path | None = None,
) -> int:
    """Recollect then execute one assigned whole-file lane under unchanged predicates."""
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    config = _lane_config(lane)
    if shard not in range(1, config.shard_count + 1):
        raise ValueError(f"{lane}: shard must be between 1 and {config.shard_count}, got {shard}")
    from ci_partition import assigned_files, collect_inventory, partition, read_json
    from ci_shard_observer import node_digest

    if inventory_dir is None:
        inventory = collect_inventory(root=repo_root)
        weights = repo_root / ".github/ci-test-weights.json"
        assignment = partition(
            inventory, read_json(weights) if weights.is_file() else {}, root=repo_root
        )
    else:
        inventory = read_json(inventory_dir / "inventory.json")
        assignment = read_json(inventory_dir / "assignment.json")
    files = assigned_files(inventory, assignment, lane=lane, index=shard, root=repo_root)
    actual_nodes = _collect_node_ids(
        paths=files, marker=config.marker, ignore_e2e=config.ignore_e2e, repo_root=repo_root
    )
    expected_nodes = {node for name in files for node in inventory["lanes"][lane][name]}
    if {node_digest(node, inventory["nonce"]) for node in actual_nodes} != expected_nodes:
        raise ValueError("independent child collection differs from assigned current inventory")
    context = timing_context(files=files, repo_root=repo_root, lane=lane, shard=shard)
    context.update(
        inventory_digest=inventory["digest"],
        assignment_digest=assignment["digest"],
        inventory_identity=inventory["identity"],
        nonce=inventory["nonce"],
    )
    timing_input = os.environ.get("CI_SHARD_TIMINGS")
    files, ordering = duration_order(
        files,
        context=context,
        receipt=Path(timing_input) if timing_input else None,
        repo_root=repo_root,
    )
    coverage_enabled = _coverage_enabled()
    if coverage_enabled:
        if coverage_file is None:
            raise ValueError("COVERAGE_FILE is required when CI_COVERAGE is enabled")
        coverage_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--maxfail={config.maxfail}",
        "--tb=short",
    ]
    if config.ignore_e2e:
        command.append("--ignore=tests/e2e")
    command.extend(
        [
            "-m",
            config.marker,
            "-n",
            unit_workers(lane),
            "--dist",
            "loadfile",
            "--no-loadscope-reorder",
            "-p",
            "ci_shard_observer",
        ]
    )
    if coverage_enabled:
        command.extend(
            [
                "--cov=src/butlers",
                f"--cov-report=json:{evidence_dir / 'coverage.json'}",
                "--cov-report=term-missing",
            ]
        )
    # pytest 9.1 scopes conftest fixtures to collector objects. Interleaving
    # package paths in argv can recreate their parents and orphan fixtures.
    # Collect lexically, then the worker-visible observer hook applies the
    # validated schedule after fixture discovery and marker deselection.
    command.extend([f"--junitxml={evidence_dir / 'raw-junit.xml'}", "--", *sorted(files)])
    environment = {
        **os.environ,
        "TEST_EVIDENCE_DIR": str(evidence_dir),
        "CI_SHARD_STARTED": str(started),
        "CI_SHARD_CONTEXT": json.dumps(
            {
                **context,
                "ordering": ordering,
                "collection_order": "lexical",
                "files": files,
                "test_step_started_at": started_at,
                "requested_workers": unit_workers(lane),
                "command": command,
            }
        ),
        "CI_SHARD_RECEIPT": str(evidence_dir / "shard-observation.json"),
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).resolve().parent), os.environ.get("PYTHONPATH", "")]
        ),
    }
    if coverage_enabled:
        environment["COVERAGE_CORE"] = coverage_core()
        environment["COVERAGE_FILE"] = str(coverage_file)
    else:
        environment.pop("COVERAGE_FILE", None)
    result = subprocess.run(  # noqa: S603
        command, cwd=repo_root, check=False, env=environment
    ).returncode
    (evidence_dir / "runner-observation.json").write_text(
        json.dumps(
            {
                **context,
                "ordering": ordering,
                "collection_order": "lexical",
                "requested_workers": unit_workers(lane),
                "coverage_core": environment.get("COVERAGE_CORE") if coverage_enabled else None,
                "pytest_exit": result,
                "test_step_elapsed_s": time.monotonic() - float(environment["CI_SHARD_STARTED"]),
            },
            sort_keys=True,
        )
        + "\n"
    )
    if coverage_enabled:
        from check_ci_coverage import write_shard_metadata

        write_shard_metadata(
            coverage_file=coverage_file,
            repo_root=repo_root,
            lane=lane,
            shard=shard,
            test_files=files,
            assignment_digest=assignment["digest"],
        )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("verify", help="fresh actual inventory/partition and unchanged budgets")
    run_parser = subcommands.add_parser("run", help="run one CI-owned file shard")
    run_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    run_parser.add_argument("--shard", type=int, required=True)
    run_parser.add_argument("--inventory-dir", type=Path)
    timing_parser = subcommands.add_parser("timing-key", help="exact compatible advisory cache key")
    timing_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    timing_parser.add_argument("--shard", type=int, required=True)
    timing_parser.add_argument("--inventory-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command in (None, "verify"):
            verify()
            return 0
        if args.command == "timing-key":
            from ci_partition import assigned_files, read_json

            files = assigned_files(
                read_json(args.inventory_dir / "inventory.json"),
                read_json(args.inventory_dir / "assignment.json"),
                lane=args.lane,
                index=args.shard,
                root=REPO_ROOT,
            )
            context = timing_context(
                files=files, repo_root=REPO_ROOT, lane=args.lane, shard=args.shard
            )
            week = datetime.now(UTC).strftime("%G-%V")
            print(
                "ci-duration-v1-"
                + week
                + "-"
                + _digest(
                    {
                        k: context[k]
                        for k in ("lane", "shard", "manifest_digest", "compatibility_digest")
                    }
                )
            )
            return 0
        coverage_file = Path(os.environ["COVERAGE_FILE"]) if _coverage_enabled() else None
        evidence_dir = Path(os.environ["TEST_EVIDENCE_DIR"])
        return run_shard(
            lane=args.lane,
            shard=args.shard,
            repo_root=REPO_ROOT,
            coverage_file=coverage_file,
            evidence_dir=evidence_dir,
            inventory_dir=args.inventory_dir,
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"check_ci_test_shards: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
