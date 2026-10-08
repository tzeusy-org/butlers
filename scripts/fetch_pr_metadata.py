#!/usr/bin/env python3
"""Write validated current PR title/body privately; never echo provider content."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REPOSITORY = "tzeusy-org/butlers"


def fetch(number: int) -> tuple[str, str]:
    if type(number) is not int or number <= 0:
        raise ValueError("invalid_pr_identity")
    result = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/pulls/{number}"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    value = json.loads(result.stdout)
    if (
        not isinstance(value, dict)
        or type(value.get("number")) is not int
        or value["number"] != number
        or value.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
        or not isinstance(value.get("title"), str)
        or "body" not in value
        or value.get("body") is not None
        and not isinstance(value["body"], str)
    ):
        raise ValueError("invalid_pr_metadata")
    return value["title"], value.get("body") or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = [args.output_dir / "pr_title.txt", args.output_dir / "pr_body.txt"]
    # A failed request must not leave a previous successful scan input behind.
    for path in paths:
        path.unlink(missing_ok=True)
    try:
        values = fetch(args.number)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for path, text in zip(paths, values, strict=True):
            path.write_text(text)
        receipt = {
            "repository": REPOSITORY,
            "number": args.number,
            "title_sha256": hashlib.sha256(values[0].encode()).hexdigest(),
            "body_sha256": hashlib.sha256(values[1].encode()).hexdigest(),
            "evidence_scope": "live metadata at fetch; trigger source identity is unchanged",
        }
        (args.output_dir / "pr_metadata_receipt.json").write_text(json.dumps(receipt) + "\n")
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        for path in paths:
            path.unlink(missing_ok=True)
        print("pr_metadata: unavailable_or_invalid")
        return 1
    print("pr_metadata: validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
