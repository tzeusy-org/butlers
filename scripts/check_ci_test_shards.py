#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Run and verify the file-level backend CI test shards.

The backend CI lanes intentionally use checked-in file manifests instead of
node-id or keyword shards.  ``verify`` derives each lane's current pytest
selection from its marker expression, then proves the manifests select every
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
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIRECTORY = Path(".github/ci-test-shards")


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
        shard_count=5,
        maxfail=5,
        ignore_e2e=False,
        workers="auto",
    ),
}


@dataclass(frozen=True)
class ShardSpec:
    """One checked-in file manifest participating in a lane."""

    lane: str
    index: int
    manifest: Path


def _lane_config(lane: str) -> LaneConfig:
    try:
        return LANES[lane]
    except KeyError as exc:
        raise ValueError(f"Unknown CI test lane {lane!r}; expected one of {sorted(LANES)}") from exc


def _shard_specs(*, lane: str, repo_root: Path) -> list[ShardSpec]:
    config = _lane_config(lane)
    return [
        ShardSpec(lane=lane, index=index, manifest=MANIFEST_DIRECTORY / f"{lane}-{index}.txt")
        for index in range(1, config.shard_count + 1)
    ]


def _read_manifest(*, manifest: Path, repo_root: Path) -> list[str]:
    """Read one manifest, refusing anything other than live test-file paths."""
    if not manifest.is_file():
        raise ValueError(f"Missing CI test-shard manifest: {manifest}")

    files: list[str] = []
    for number, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line != raw_line or "::" in line or "\\" in line:
            raise ValueError(
                f"{manifest}:{number}: manifests must contain plain relative test files"
            )
        path = PurePosixPath(line)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
            raise ValueError(
                f"{manifest}:{number}: manifests must contain repo-relative Python test files"
            )
        if path.parts[0] not in {"tests", "roster"}:
            raise ValueError(f"{manifest}:{number}: test files must live under tests/ or roster/")
        candidate = repo_root / path
        if not candidate.is_file():
            raise ValueError(f"{manifest}:{number}: stale or missing test file {line}")
        files.append(line)

    if not files:
        raise ValueError(f"{manifest}: manifests must not be empty")
    duplicate_files = sorted(file for file, count in Counter(files).items() if count > 1)
    if duplicate_files:
        raise ValueError(
            f"{manifest}: test files are listed more than once: {_format_paths(duplicate_files)}"
        )
    if files != sorted(files):
        raise ValueError(f"{manifest}: test files must be sorted for deterministic review")
    return files


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


def _format_paths(paths: list[str]) -> str:
    visible = ", ".join(paths[:20])
    suffix = f" (+{len(paths) - 20} more)" if len(paths) > 20 else ""
    return visible + suffix


def _validate_lane(*, lane: str, shard_specs: list[ShardSpec], repo_root: Path) -> tuple[int, int]:
    """Prove a lane's manifests cover the selected files and nodes exactly once."""
    config = _lane_config(lane)
    expected_indexes = list(range(1, config.shard_count + 1))
    actual_indexes = sorted(spec.index for spec in shard_specs)
    if actual_indexes != expected_indexes:
        raise ValueError(
            f"{lane}: expected shard indexes {expected_indexes}, found {actual_indexes}"
        )

    full_nodes = collect_lane_node_ids(lane, repo_root=repo_root)
    if not full_nodes:
        raise ValueError(f"{lane}: marker selection produced zero tests")
    full_files = {_node_file(node_id) for node_id in full_nodes}

    manifest_owner: dict[str, int] = {}
    selected_nodes: set[str] = set()
    node_owner: dict[str, int] = {}
    shard_selections: list[tuple[ShardSpec, list[str], set[str]]] = []
    for spec in sorted(shard_specs, key=lambda item: item.index):
        if spec.lane != lane:
            raise ValueError(f"{lane}: shard {spec.index} declares lane {spec.lane!r}")
        files = _read_manifest(manifest=repo_root / spec.manifest, repo_root=repo_root)
        duplicates = sorted(file for file in files if file in manifest_owner)
        if duplicates:
            raise ValueError(
                f"{lane}: test files are listed more than once across manifests: "
                f"{_format_paths(duplicates)}"
            )
        manifest_owner.update({file: spec.index for file in files})

        shard_nodes = _collect_node_ids(
            paths=files,
            marker=config.marker,
            ignore_e2e=config.ignore_e2e,
            repo_root=repo_root,
        )
        if not shard_nodes:
            raise ValueError(f"{lane}: shard {spec.index} selects zero tests")
        overlapping_nodes = [node for node in shard_nodes if node in node_owner]
        if overlapping_nodes:
            raise ValueError(
                f"{lane}: {len(overlapping_nodes)} selected node(s) "
                "are selected by more than one shard"
            )
        node_owner.update({node: spec.index for node in shard_nodes})
        selected_nodes.update(shard_nodes)
        shard_selections.append((spec, files, shard_nodes))

    for spec, files, shard_nodes in shard_selections:
        shard_files = {_node_file(node_id) for node_id in shard_nodes}
        missing_from_shard = sorted(set(files) - shard_files)
        extra_in_shard = sorted(shard_files - set(files))
        if missing_from_shard or extra_in_shard:
            details: list[str] = []
            if missing_from_shard:
                details.append(f"zero-selected manifest files: {_format_paths(missing_from_shard)}")
            if extra_in_shard:
                details.append(f"collected outside manifest: {_format_paths(extra_in_shard)}")
            raise ValueError(
                f"{lane}: shard {spec.index} has invalid file selection ({'; '.join(details)})"
            )

    missing_files = sorted(full_files - set(manifest_owner))
    stale_files = sorted(set(manifest_owner) - full_files)
    if missing_files or stale_files:
        details = []
        if missing_files:
            details.append(f"Missing selected test files: {_format_paths(missing_files)}")
        if stale_files:
            details.append(
                f"manifest files outside current selection: {_format_paths(stale_files)}"
            )
        raise ValueError(f"{lane}: " + "; ".join(details))

    missing_nodes = full_nodes - selected_nodes
    unexpected_nodes = selected_nodes - full_nodes
    if missing_nodes or unexpected_nodes:
        details = []
        if missing_nodes:
            details.append(f"{len(missing_nodes)} selected node(s) missing from shards")
        if unexpected_nodes:
            details.append(f"{len(unexpected_nodes)} shard node(s) outside lane selection")
        raise ValueError(f"{lane}: " + "; ".join(details))

    return len(full_nodes), len(full_files)


def verify(*, repo_root: Path = REPO_ROOT) -> dict[str, tuple[int, int]]:
    """Verify both lanes independently and return their selected node/file counts."""
    results = {
        lane: _validate_lane(
            lane=lane,
            shard_specs=_shard_specs(lane=lane, repo_root=repo_root),
            repo_root=repo_root,
        )
        for lane in LANES
    }
    for lane, (node_count, file_count) in results.items():
        print(
            f"OK: {lane} manifests select {node_count} test(s) "
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
    names += [
        str(path.relative_to(repo_root)) for path in (repo_root / MANIFEST_DIRECTORY).glob("*.txt")
    ]
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
        expected = sorted(_digest(node) for node in nodes)
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
    *, lane: str, shard: int, repo_root: Path, coverage_file: Path | None, evidence_dir: Path
) -> int:
    """Execute one lane shard with the CI-owned marker and file manifest."""
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    config = _lane_config(lane)
    if shard not in range(1, config.shard_count + 1):
        raise ValueError(f"{lane}: shard must be between 1 and {config.shard_count}, got {shard}")
    manifest = repo_root / MANIFEST_DIRECTORY / f"{lane}-{shard}.txt"
    files = _read_manifest(manifest=manifest, repo_root=repo_root)
    context = timing_context(files=files, repo_root=repo_root, lane=lane, shard=shard)
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
        )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("verify", help="fail closed unless every lane manifest is exact")
    run_parser = subcommands.add_parser("run", help="run one CI-owned file shard")
    run_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    run_parser.add_argument("--shard", type=int, required=True)
    timing_parser = subcommands.add_parser("timing-key", help="exact compatible advisory cache key")
    timing_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    timing_parser.add_argument("--shard", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command in (None, "verify"):
            verify()
            return 0
        if args.command == "timing-key":
            files = _read_manifest(
                manifest=REPO_ROOT / MANIFEST_DIRECTORY / f"{args.lane}-{args.shard}.txt",
                repo_root=REPO_ROOT,
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
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"check_ci_test_shards: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
