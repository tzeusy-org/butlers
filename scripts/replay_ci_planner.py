#!/usr/bin/env python3
"""Truthful frozen-input planner replay; never execute tests or refresh refs.

Name-only historical vectors have no immutable source identity and cannot earn
SCOPED credit. Optional authenticated original Git joins may prove source
eligibility, independently of current collection, cost or executing evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing import resource_readers  # noqa: E402
from butlers.testing.changed_files import (  # noqa: E402
    ChangedFiles,
    get_changed_files,
    historical_manifest_delta,
)
from butlers.testing.scoped_runner import (  # noqa: E402
    FULL_SUITE_FALLBACK_ALLOWLIST,
    _plan_for_changed_files,
)


def replay(corpus_path, expected_sha256, *, root=ROOT, joins=()):
    raw = corpus_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("frozen corpus hash mismatch")
    corpus = json.loads(raw)
    if (
        len(corpus["valid_145"]) != 145
        or len(corpus["last_100_merged"]) != 100
        or len(corpus["regressing_three"]) != 3
    ):
        raise ValueError("complete original frozen populations required")
    ids = [row["id"] for row in corpus["valid_145"]]
    if len(set(ids)) != 145:
        raise ValueError("frozen branch population duplicates")
    bindings = {row["run"]: row for row in joins}
    if len(bindings) != len(joins):
        raise ValueError("historical Git joins duplicate")
    # Refuse a stale registry before evaluating the entire frozen population.
    # Individual planner calls still make their own fresh source checks.
    readers = resource_readers.load(root)

    def decision(row):
        files = row["files"]
        if (
            not isinstance(files, list)
            or not files
            or len(files) != len(set(files))
            or any(type(p) is not str for p in files)
        ):
            return {
                "id": row["id"],
                "mode": "full",
                "reason_codes": ["MALFORMED_HISTORICAL_VECTOR"],
            }
        changed = ChangedFiles(files=sorted(files), sources=("historical-vector",))
        eligibility = None
        if row["id"] in bindings:
            join = bindings[row["id"]]
            actual = get_changed_files(join["head"], join["base"], repo_dir=root)
            if actual.files != sorted(files):
                raise ValueError("original Git diff differs from frozen vector")
            changed = actual
            eligibility = historical_manifest_delta(root, changed)
        plan = _plan_for_changed_files(
            changed,
            repo_dir=root,
            fallback_allowlist=FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",),
        )
        result = plan.as_dict()
        return {
            "id": row["id"],
            "mode": result["mode"],
            "candidate_test_paths": result["candidate_test_paths"],
            "reason_codes": result["reason_codes"],
            "historical_source_eligibility": eligibility,
        }

    current = [decision(row) for row in corpus["valid_145"]]
    regressions = [decision(row) for row in corpus["regressing_three"]]
    candidates = [decision(row) for row in corpus["manifest_candidates_40_not_all_eligible"]]
    scoped = sum(row["mode"] == "scoped" for row in current)
    census = []
    for row in corpus["last_100_merged"]:
        paths = [item["path"] for item in row["files"]]
        resources = [p for p in paths if resource_readers.family(p)]
        census.append(
            {
                "PR": row["number"],
                "resources": [
                    {
                        "path": path,
                        "mode": "full"
                        if resource_readers.unbounded(path, readers)
                        else "candidate",
                        "reader_files": resource_readers.select(path, readers),
                    }
                    for path in resources
                ],
            }
        )
    if resource_readers.load(root) != readers:
        raise ValueError("reader source changed during frozen replay")
    return {
        "schema": "planner-replay.v1",
        "corpus_sha256": expected_sha256,
        "valid_145": current,
        "malformed_3": [
            {"id": row["id"], "mode": "UNKNOWN", "scoped_credit": False}
            for row in corpus["malformed_3_UNKNOWN_not_scoped_credit"]
        ],
        "regressing_three": regressions,
        "manifest_candidates": candidates,
        "last_100_reader_census": census,
        "registry_digest": readers["digest"],
        "thresholds": {
            "historical_scoped": scoped,
            "historical_required": 57,
            "historical_met": scoped >= 57,
            "regressions_all_full": all(row["mode"] == "full" for row in regressions),
            "natural_next_50": "UNOBSERVED",
            "natural_next_20": "UNOBSERVED",
            "ten_affected_max_vs_p90": "UNOBSERVED",
        },
        "limitations": [
            "No wall-clock gain claimed",
            "Source eligibility is not SCOPED/cost/native evidence",
            "Current static reader census is not an independent executing historical-reader audit",
            "Name-only vectors cannot authenticate original source/configuration",
            "No natural PR or native execution is produced by replay",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--joins", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        joins = json.loads(args.joins.read_text()) if args.joins else []
        body = replay(args.corpus, args.expected_sha256, joins=joins)
        args.output.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n")
        print(json.dumps({"replay": True, "thresholds": body["thresholds"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print("frozen planner replay unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
