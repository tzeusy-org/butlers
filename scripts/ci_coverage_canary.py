#!/usr/bin/env python3
"""Scratch-only healthy/corrupt/restored probe; every outcome refuses publication.

This runs the unchanged coverage validator on real downloaded merge-group inputs.
It is not an event adapter and never turns PR artifacts into merge-group evidence.
Only closed source identities, digests and categorical outcomes leave subprocesses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORRUPT_BYTES = b"controlled unreadable coverage database\n"
EXPECTED_REFUSAL = "check_ci_coverage: unit-1: unreadable coverage data or metadata"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = {
        "schema": "ci-controlled-coverage-negative.v1",
        "stage": "admission",
        "complete": False,
        "head": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
        "healthy": False,
        "corrupt_refused": False,
        "restored_healthy": False,
        "validator_body_sha256": hashlib.sha256(
            (ROOT / "scripts/check_ci_coverage.py").read_bytes()
        ).hexdigest(),
        "raw_subprocess_output_retained": False,
        "scope": "scratch-only reporting predicate; no report/badge publication",
    }
    data = args.input_root / "unit-1/coverage-unit-1.data"
    metadata_path = data.with_suffix(".data.metadata.json")
    original_data = original_metadata = None

    def validate() -> tuple[int, bool]:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_ci_coverage.py",
                "--input-root",
                str(args.input_root),
                "--inventory-dir",
                str(args.inventory_dir),
                "--head",
                receipt["head"],
                "--run-id",
                receipt["run_id"],
                "--run-attempt",
                receipt["run_attempt"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # No exception messages, paths, SQL values or arbitrary output are emitted.
        return result.returncode, result.stderr.strip() == EXPECTED_REFUSAL

    try:
        if receipt["event"] != "merge_group" or not all(
            isinstance(receipt[key], str) and re.fullmatch(pattern, receipt[key])
            for key, pattern in (
                ("head", r"[0-9a-f]{40}"),
                ("run_id", r"[1-9][0-9]*"),
                ("run_attempt", r"[1-9][0-9]*"),
            )
        ):
            receipt["category"] = "wrong-or-missing-real-workflow-identity"
            return 3
        receipt["stage"] = "healthy-validator"
        code, _ = validate()
        receipt["healthy_exit"] = code
        if code != 0:
            receipt["category"] = "healthy-validation-unavailable"
            return 3
        receipt["healthy"] = True
        original_data = data.read_bytes()
        original_metadata = metadata_path.read_bytes()
        metadata = json.loads(original_metadata)
        receipt["original_unit_1_raw_sha256"] = hashlib.sha256(original_data).hexdigest()
        receipt["original_unit_1_metadata_sha256"] = hashlib.sha256(original_metadata).hexdigest()
        receipt["assignment_digest"] = metadata["assignment_digest"]
        receipt["stage"] = "corrupt-validator"
        data.write_bytes(CORRUPT_BYTES)
        metadata["sha256"] = hashlib.sha256(CORRUPT_BYTES).hexdigest()
        metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n")
        receipt["corrupt_raw_sha256"] = metadata["sha256"]
        receipt["only_raw_digest_field_changed"] = {
            key: value for key, value in metadata.items() if key != "sha256"
        } == {key: value for key, value in json.loads(original_metadata).items() if key != "sha256"}
        code, matched = validate()
        receipt["corrupt_exit"] = code
        receipt["corrupt_refused"] = code == 2 and matched
        receipt["stage"] = "restored-validator"
        data.write_bytes(original_data)
        metadata_path.write_bytes(original_metadata)
        code, _ = validate()
        receipt["restored_exit"] = code
        receipt["restored_healthy"] = code == 0
        receipt["complete"] = (
            receipt["corrupt_refused"]
            and receipt["restored_healthy"]
            and receipt["only_raw_digest_field_changed"]
        )
        receipt["category"] = "expected-corrupt-refusal" if receipt["complete"] else "unknown"
        receipt["stage"] = "complete" if receipt["complete"] else receipt["stage"]
        # Expected negative is deliberately a failed step, never a publication permit.
        return 2 if receipt["complete"] else 3
    except subprocess.TimeoutExpired:
        receipt["category"] = "bounded-validator-timeout"
        return 3
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        receipt["category"] = "unavailable-or-malformed-input"
        return 3
    finally:
        if original_data is not None and original_metadata is not None:
            data.write_bytes(original_data)
            metadata_path.write_bytes(original_metadata)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
