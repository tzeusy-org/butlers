#!/usr/bin/env python3
"""Publish provisional, content-blind evidence; host terminal validation owns escalation."""

from __future__ import annotations

import argparse
from pathlib import Path

from butlers.nightly_assurance import EvidenceUnavailable, atomic_json
from butlers.nightly_github import GitHubEvidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish-evidence", action="store_true")
    args = parser.parse_args()
    github = GitHubEvidence()
    try:
        assessment = github.read_assessment(args.run_id, provisional=True)
        receipt = {"status": "observed", "assessment": assessment.document(), "issue": None}
        if args.publish_evidence and (assessment.failures or assessment.unavailable):
            receipt["issue"] = github.upsert_issue(assessment)
        atomic_json(args.output, receipt)
    except EvidenceUnavailable:
        atomic_json(args.output, {"status": "unavailable", "run_id": args.run_id})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
