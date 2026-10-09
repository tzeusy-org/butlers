#!/usr/bin/env python3
"""Bind and validate the complete same-run coverage population before reporting.

Coverage combine can warn and skip an unreadable input. Read every database first,
and bind its digest to the producer's checkout, workflow attempt and file manifest.
This is report integrity, not a coverage threshold or a required test verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from check_ci_test_shards import LANES, REPO_ROOT
from ci_partition import assigned_files, read_json, validate_assignment
from coverage import CoverageData
from coverage.exceptions import CoverageException


def _source_files(repo_root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(repo_root)) for path in (repo_root / "src/butlers").rglob("*.py")
    )


def _checkout_head(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def write_shard_metadata(
    *,
    coverage_file: Path,
    repo_root: Path,
    lane: str,
    shard: int,
    test_files: list[str],
    assignment_digest: str | None = None,
) -> None:
    """Stamp existing data after pytest exits; never fabricate a missing artifact."""
    if not coverage_file.is_file():
        return
    checkout = _checkout_head(repo_root)
    workflow_head = os.environ.get("GITHUB_SHA")
    if workflow_head is not None and workflow_head != checkout:
        raise ValueError("coverage producer checkout does not match the workflow head")
    metadata = {
        "schema": "ci-coverage.v1",
        "assignment_digest": assignment_digest,
        "head": checkout,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
        "lane": lane,
        "shard": shard,
        "config": asdict(LANES[lane]),
        "test_files": test_files,
        "source_files": _source_files(repo_root),
        "sha256": hashlib.sha256(coverage_file.read_bytes()).hexdigest(),
    }
    coverage_file.with_suffix(coverage_file.suffix + ".metadata.json").write_text(
        json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8"
    )


def validate_inputs(
    *,
    input_root: Path,
    repo_root: Path,
    head: str,
    run_id: str,
    run_attempt: str,
    inventory_dir: Path | None = None,
) -> list[Path]:
    """Reject a partial, stale, mixed, unreadable or incompatible ten-file report."""
    if not head or not run_id or not run_attempt or _checkout_head(repo_root) != head:
        raise ValueError("coverage reporter requires the exact checkout and workflow attempt")
    if input_root.is_symlink():
        raise ValueError("coverage input root must be an ordinary directory")
    if inventory_dir is None:
        raise ValueError("coverage requires generated same-run assignment")
    inventory = read_json(inventory_dir / "inventory.json")
    assignment = read_json(inventory_dir / "assignment.json")
    validate_assignment(inventory, assignment, root=repo_root)
    specs = [
        (lane, index)
        for lane, config in LANES.items()
        for index in range(1, config.shard_count + 1)
    ]
    expected_directories = {f"{lane}-{index}" for lane, index in specs}
    actual_directories = {
        path.name for path in input_root.iterdir() if path.is_dir() and not path.is_symlink()
    }
    if actual_directories != expected_directories:
        raise ValueError("coverage input directory population does not match the declared shards")
    if {path.name for path in input_root.iterdir()} != expected_directories:
        raise ValueError("coverage input root contains unexpected files")
    source_files = _source_files(repo_root)
    if not source_files:
        raise ValueError("coverage reporter source population is empty")
    expected_measured = {str((repo_root / name).resolve()) for name in source_files}
    tracing = None
    inputs = []
    for lane, index in specs:
        label = f"{lane}-{index}"
        directory = input_root / label
        data_file = directory / f"coverage-{label}.data"
        metadata_file = data_file.with_suffix(".data.metadata.json")
        if set(directory.iterdir()) != {data_file, metadata_file}:
            raise ValueError(f"{label}: missing or extra coverage input/metadata")
        if data_file.is_symlink() or metadata_file.is_symlink():
            raise ValueError(f"{label}: coverage inputs must be ordinary files")
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            files = assigned_files(inventory, assignment, lane=lane, index=index, root=repo_root)
            expected = {
                "schema": "ci-coverage.v1",
                "assignment_digest": assignment["digest"],
                "head": head,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "event": "merge_group",
                "lane": lane,
                "shard": index,
                "config": asdict(LANES[lane]),
                "test_files": files,
                "source_files": source_files,
                "sha256": hashlib.sha256(data_file.read_bytes()).hexdigest(),
            }
            if json.dumps(metadata, sort_keys=True) != json.dumps(expected, sort_keys=True):
                raise ValueError(f"{label}: coverage identity, manifest or digest mismatch")
            data = CoverageData(basename=str(data_file))
            data.read()
            measured = data.measured_files()
            if (
                not measured
                or {str(Path(name).resolve()) for name in measured} != expected_measured
            ):
                raise ValueError(f"{label}: empty or mismatched measured source population")
            if not any(data.lines(name) for name in measured):
                raise ValueError(f"{label}: no executed source lines")
            mode = (data.has_arcs(), sorted((name, data.file_tracer(name)) for name in measured))
            if tracing is not None and mode != tracing:
                raise ValueError(f"{label}: incompatible coverage tracing")
            tracing = mode
        except (OSError, UnicodeError, json.JSONDecodeError, CoverageException) as exc:
            raise ValueError(f"{label}: unreadable coverage data or metadata") from exc
        inputs.append(data_file)
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--inventory-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = validate_inputs(
            input_root=args.input_root,
            repo_root=REPO_ROOT,
            head=args.head,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            inventory_dir=args.inventory_dir,
        )
    except (OSError, ValueError) as exc:
        print(f"check_ci_coverage: {exc}", file=sys.stderr)
        return 2
    print(f"Validated {len(inputs)} same-run coverage inputs before combine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
