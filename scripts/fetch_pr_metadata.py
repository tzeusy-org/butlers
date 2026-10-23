#!/usr/bin/env python3
"""Write validated current PR title/body privately; never echo provider content."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

REPOSITORY = "tzeusy-org/butlers"
ATTEMPT_SECONDS = 30
RETRY_SECONDS = 97
BACKOFF_SECONDS = (2, 5)


def fetch(number: int) -> tuple[str, str]:
    if type(number) is not int or number <= 0:
        raise ValueError("invalid_pr_identity")
    deadline = time.monotonic() + RETRY_SECONDS
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("gh api", RETRY_SECONDS)
        try:
            result = subprocess.run(
                ["gh", "api", f"repos/{REPOSITORY}/pulls/{number}"],
                capture_output=True,
                check=True,
                timeout=min(ATTEMPT_SECONDS, remaining),
            )
        except subprocess.SubprocessError:
            if attempt == len(BACKOFF_SECONDS):
                raise
            delay = BACKOFF_SECONDS[attempt]
            if deadline - time.monotonic() <= delay:
                raise
            time.sleep(delay)
        else:
            break
    # A successful read is validated exactly once, outside the retry loop.
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


def _remove_outputs(paths: list[Path]) -> bool:
    """Attempt every owned removal even when one fails; never echo local errors."""
    complete = True
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            complete = False
    return complete


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default diagnostics can echo invalid caller input.
        raise ValueError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--number", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except (ValueError, TypeError):
        print("pr_metadata: unavailable_or_invalid")
        return 1
    paths = [
        args.output_dir / "pr_title.txt",
        args.output_dir / "pr_body.txt",
        args.output_dir / "pr_metadata_receipt.json",
    ]
    # All three are one output unit. A failed cleanup must not start a read.
    if not _remove_outputs(paths):
        print("pr_metadata: unavailable_or_invalid")
        return 1
    try:
        number = int(args.number)
        values = fetch(number)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for path, text in zip(paths[:2], values, strict=True):
            path.write_text(text)
        receipt = {
            "repository": REPOSITORY,
            "number": number,
            "title_sha256": hashlib.sha256(values[0].encode()).hexdigest(),
            "body_sha256": hashlib.sha256(values[1].encode()).hexdigest(),
            "evidence_scope": "live metadata at fetch; trigger source identity is unchanged",
        }
        paths[2].write_text(json.dumps(receipt) + "\n")
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        _remove_outputs(paths)
        print("pr_metadata: unavailable_or_invalid")
        return 1
    print("pr_metadata: validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
