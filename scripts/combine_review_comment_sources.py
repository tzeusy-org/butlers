#!/usr/bin/env python3
"""Flatten raw GitHub REST API dumps into session_link_guard's review-comments shape.

Used by the `session-link-guard` CI job (bu-mr5t5): `gh api ... --paginate`
writes each surface (PR review comments, PR issue-thread comments, PR
reviews) to its own file as raw, unfiltered JSON. This script combines them
into the flat `[{"source": ..., "body": ...}, ...]` list that
`scripts/session_link_guard.py --review-comments-file` expects.

Deliberately tolerant of a missing/empty/malformed input file: review-comment
coverage is a best-effort surface, so one bad fetch should shrink the combined
set rather than fail the whole job (the PR body + commit message checks are
the mandatory surfaces and run regardless).

`gh api ... --paginate` without a `--jq` filter concatenates one JSON array
per page rather than emitting a single well-formed JSON document, so each
input file is parsed as a *stream* of JSON values (one or more arrays) rather
than assumed to be exactly one JSON array.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _iter_json_values(text: str):
    """Yield each whitespace-separated top-level JSON value found in text."""
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            return
        value, end = decoder.raw_decode(text, idx)
        yield value
        idx = end


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Best-effort: return every dict found across all top-level JSON arrays in path."""
    try:
        text = path.read_text()
    except OSError as exc:
        print(
            f"combine_review_comment_sources: warning: cannot read {path}: {exc}", file=sys.stderr
        )
        return []

    records: list[dict[str, Any]] = []
    try:
        for value in _iter_json_values(text):
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                records.append(value)
    except json.JSONDecodeError as exc:
        print(
            f"combine_review_comment_sources: warning: malformed JSON in {path}: {exc}",
            file=sys.stderr,
        )

    return records


def combine(labeled_paths: list[tuple[str, Path]]) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    for label, path in labeled_paths:
        for record in _load_records(path):
            record_id = record.get("id", "?")
            combined.append(
                {
                    "source": f"{label}-{record_id}",
                    "body": record.get("body") or "",
                }
            )
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        nargs=2,
        metavar=("LABEL", "PATH"),
        action="append",
        default=[],
        help="A (source label, raw gh-api-dump file) pair; repeatable.",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Where to write the combined JSON list"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    labeled_paths = [(label, Path(path)) for label, path in args.input]
    combined = combine(labeled_paths)
    args.output.write_text(json.dumps(combined))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
