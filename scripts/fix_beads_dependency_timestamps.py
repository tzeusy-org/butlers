#!/usr/bin/env python3
"""
Fix zero-timestamp dependencies in .beads/issues.jsonl.

This script detects dependency records with created_at="0001-01-01T00:00:00Z"
and replaces them with the issue's updated_at timestamp (or current time as fallback).

Usage:
    python scripts/fix_beads_dependency_timestamps.py [--dry-run] [--jsonl-path PATH]

Options:
    --dry-run         Show what would be fixed without writing changes
    --jsonl-path PATH Path to issues.jsonl (default: .beads/issues.jsonl)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ZERO_TIMESTAMP = "0001-01-01T00:00:00Z"


def _current_timestamp() -> str:
    """Return current UTC timestamp in RFC3339 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def fix_dependency_timestamps(jsonl_path: Path, dry_run: bool = False) -> dict[str, int]:
    """
    Fix zero-timestamp dependencies in issues.jsonl.

    Args:
        jsonl_path: Path to the issues.jsonl file
        dry_run: If True, don't write changes, just report what would be fixed

    Returns:
        Dictionary with statistics: issues_scanned, issues_modified, deps_fixed
    """
    if not jsonl_path.exists():
        print(f"Error: {jsonl_path} not found", file=sys.stderr)
        sys.exit(1)

    stats = {"issues_scanned": 0, "issues_modified": 0, "deps_fixed": 0}
    modified_lines: list[str] = []

    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            modified_lines.append(line)
            continue

        try:
            issue = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"Warning: skipping malformed line: {e}", file=sys.stderr)
            modified_lines.append(line)
            continue

        stats["issues_scanned"] += 1
        deps = issue.get("dependencies", [])
        if not deps:
            modified_lines.append(line)
            continue

        # Check for zero timestamps
        modified = False
        for dep in deps:
            if dep.get("created_at") == ZERO_TIMESTAMP:
                # Use the issue's updated_at as the dependency timestamp, fallback to current
                fallback_ts = issue.get("updated_at") or _current_timestamp()
                dep["created_at"] = fallback_ts

                if not modified:
                    print(f"Fixing issue {issue['id']}:")
                    stats["issues_modified"] += 1
                    modified = True

                print(
                    f"  - Dependency {dep['issue_id']} -> {dep['depends_on_id']} "
                    f"(type: {dep.get('type', 'blocks')}): {ZERO_TIMESTAMP} -> {fallback_ts}"
                )
                stats["deps_fixed"] += 1

        # Write the (possibly modified) line
        modified_lines.append(json.dumps(issue, separators=(",", ":")))

    # Write back if not dry-run
    if not dry_run and stats["deps_fixed"] > 0:
        jsonl_path.write_text("\n".join(modified_lines) + "\n")
        print(f"\nWrote {stats['issues_modified']} modified issues to {jsonl_path}")
    elif dry_run and stats["deps_fixed"] > 0:
        print(f"\nDry run: would fix {stats['deps_fixed']} dependencies in {jsonl_path}")
    elif stats["deps_fixed"] == 0:
        print(f"\nNo zero-timestamp dependencies found in {jsonl_path}")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix zero-timestamp dependencies in .beads/issues.jsonl"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fixed without writing changes",
    )
    parser.add_argument(
        "--jsonl-path",
        type=Path,
        default=Path(".beads/issues.jsonl"),
        help="Path to issues.jsonl (default: .beads/issues.jsonl)",
    )
    args = parser.parse_args()

    stats = fix_dependency_timestamps(args.jsonl_path, args.dry_run)
    print(
        f"\nSummary: scanned {stats['issues_scanned']} issues, "
        f"modified {stats['issues_modified']} issues, "
        f"fixed {stats['deps_fixed']} dependencies"
    )


if __name__ == "__main__":
    main()
