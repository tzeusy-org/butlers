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
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
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

    full_nodes = _collect_node_ids(
        paths=[], marker=config.marker, ignore_e2e=config.ignore_e2e, repo_root=repo_root
    )
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


def run_shard(
    *, lane: str, shard: int, repo_root: Path, coverage_file: Path, evidence_dir: Path
) -> int:
    """Execute one lane shard with the CI-owned marker and file manifest."""
    config = _lane_config(lane)
    if shard not in range(1, config.shard_count + 1):
        raise ValueError(f"{lane}: shard must be between 1 and {config.shard_count}, got {shard}")
    manifest = repo_root / MANIFEST_DIRECTORY / f"{lane}-{shard}.txt"
    files = _read_manifest(manifest=manifest, repo_root=repo_root)
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
            config.workers,
            "--dist",
            "loadfile",
            "--cov=src/butlers",
            f"--cov-report=json:{evidence_dir / 'coverage.json'}",
            "--cov-report=term-missing",
            f"--junitxml={evidence_dir / 'raw-junit.xml'}",
            "--",
            *files,
        ]
    )
    environment = {
        **os.environ,
        "COVERAGE_FILE": str(coverage_file),
        "TEST_EVIDENCE_DIR": str(evidence_dir),
    }
    return subprocess.run(  # noqa: S603
        command, cwd=repo_root, check=False, env=environment
    ).returncode


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("verify", help="fail closed unless every lane manifest is exact")
    run_parser = subcommands.add_parser("run", help="run one CI-owned file shard")
    run_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    run_parser.add_argument("--shard", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command in (None, "verify"):
            verify()
            return 0
        coverage_file = Path(os.environ["COVERAGE_FILE"])
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
