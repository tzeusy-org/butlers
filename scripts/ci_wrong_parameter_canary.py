#!/usr/bin/env python3
"""Scratch-only same-count misattribution after the real assigned tests finish.

Only the unique child owning a known public parametrized test is changed. This
does not select or skip tests. An unchanged preflight must reject the false claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ci_partition import digest
from ci_shard_observer import node_digest

ACTUAL_NODE = (
    "tests/testing/test_source_test_map.py::"
    "test_dot_prefixed_ci_paths_escalate_without_losing_the_dot[.github/workflows/ci.yml]"
)
WRONG_NODE = (
    "tests/testing/test_source_test_map.py::"
    "test_dot_prefixed_ci_paths_escalate_without_losing_the_dot[controlled-uncollected-parameter]"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--control-dir", type=Path, required=True)
    args = parser.parse_args()
    before = args.receipt.read_bytes()
    receipt = json.loads(before)
    if receipt.get("complete") is not True or receipt.get("pytest_exit") != 0:
        raise ValueError("controlled mutation requires an actual complete healthy child")
    original = node_digest(ACTUAL_NODE, receipt["nonce"])
    wrong = node_digest(WRONG_NODE, receipt["nonce"])
    control = {
        "schema": "ci-controlled-wrong-parameter.v1",
        "target_present": original in receipt["nodes"],
        "same_count": True,
        "raw_parameter_values_retained": False,
        "before_receipt_sha256": hashlib.sha256(before).hexdigest(),
    }
    args.control_dir.mkdir(parents=True, exist_ok=True)
    if original in receipt["nodes"]:
        if wrong in receipt["nodes"]:
            raise ValueError("wrong identity must be absent from actual collected items")
        (args.control_dir / "healthy-shard-observation.json").write_bytes(before)
        count = len(receipt["nodes"])
        for field in ("nodes", "node_files", "node_classes", "logical_starts"):
            values = receipt[field]
            values[wrong] = values.pop(original)
        receipt["selected_node_digest"] = digest(sorted(receipt["nodes"]))
        if len(receipt["nodes"]) != count or receipt["selected_count"] != count:
            raise ValueError("controlled mutation must preserve actual cardinality")
        args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        control.update(
            original_identity=original,
            wrong_identity=wrong,
            selected_count=count,
            after_receipt_sha256=hashlib.sha256(args.receipt.read_bytes()).hexdigest(),
            expected_refusal="child differs from complete actual inventory identities",
        )
    (args.control_dir / "receipt.json").write_text(json.dumps(control, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
