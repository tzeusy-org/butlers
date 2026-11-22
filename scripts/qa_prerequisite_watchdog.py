#!/usr/bin/env python3
"""Engineering-side stall watchdog for QA's prerequisite work items (bu-ejow5p.8).

QA cannot read Beads (RFC 0025 runtime-to-tracker deny) and cannot investigate itself (RFC 0015
D3), so a prerequisite that is blocked or unowned for too long has to be noticed where ``bd``
access already exists. This script reads a ``bd export`` JSONL file, never the runtime and never
the database, and reports each watched item as ``ok``, ``stalled`` or ``unknown``:

* ``ok``       closed, or live (assigned and not blocked) for the whole window;
* ``stalled``  not closed and either ``blocked`` or without an assignee, with no update for
               more than ``--days`` days (default 7);
* ``unknown``  the export is unreadable, or an item or ``<prefix>.*`` family has no row. The
               watchdog claims nothing in that case (RFC 0029: never absence without an
               instrument).

Dry run only: it prints a report and the ``bd`` commands a coordinator lane may run to file or
refresh the stall note. It never writes to the tracker. Exit status: 0 all ok, 1 any stalled,
2 any unknown with nothing stalled.

    bd export -o /tmp/issues.export.jsonl
    scripts/qa_prerequisite_watchdog.py /tmp/issues.export.jsonl --days 7
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: The QA prerequisites named by the run 25 owner gate. ``<prefix>.*`` watches a child family.
WATCHED: tuple[str, ...] = (
    "bu-mc01y5.7",
    "bu-6a9xxw.*",
    "bu-gjmx8w",
    "bu-ratgnb",
)
DEFAULT_DAYS = 7


@dataclass(frozen=True)
class Finding:
    item: str
    state: str  # ok | stalled | unknown
    detail: str


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_issues(path: Path) -> dict[str, dict[str, Any]] | None:
    """Parse a ``bd export`` JSONL; ``None`` when it cannot be read as one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    issues: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if isinstance(row, dict) and row.get("_type", "issue") == "issue" and row.get("id"):
            issues[str(row["id"])] = row
    return issues or None


def _judge(row: dict[str, Any], now: datetime, limit: timedelta) -> Finding:
    ident = str(row["id"])
    status = row.get("status")
    if status == "closed":
        return Finding(ident, "ok", "closed")
    updated = _parse_time(row.get("updated_at"))
    if updated is None:
        return Finding(ident, "unknown", "no readable updated_at")
    unowned = not (row.get("assignee") or "").strip()
    reason = "blocked" if status == "blocked" else "unowned" if unowned else None
    if reason is None:
        return Finding(ident, "ok", f"{status}, owned")
    age = now - updated
    if age > limit:
        return Finding(ident, "stalled", f"{reason} for {age.days}d (limit {limit.days}d)")
    return Finding(ident, "ok", f"{reason} for {age.days}d, within {limit.days}d")


def evaluate(
    issues: dict[str, dict[str, Any]] | None,
    now: datetime,
    days: int = DEFAULT_DAYS,
    watched: tuple[str, ...] = WATCHED,
) -> list[Finding]:
    """Judge every watched item; an unreadable export or missing row is ``unknown``."""
    if issues is None:
        return [Finding(item, "unknown", "export unreadable") for item in watched]
    limit = timedelta(days=days)
    findings: list[Finding] = []
    for item in watched:
        if item.endswith(".*"):
            prefix = item[:-1]
            rows = [row for ident, row in sorted(issues.items()) if ident.startswith(prefix)]
            if not rows:
                findings.append(Finding(item, "unknown", "no rows in export"))
            findings.extend(_judge(row, now, limit) for row in rows)
        elif item in issues:
            findings.append(_judge(issues[item], now, limit))
        else:
            findings.append(Finding(item, "unknown", "not in export"))
    return findings


def exit_code(findings: list[Finding]) -> int:
    if any(f.state == "stalled" for f in findings):
        return 1
    return 2 if any(f.state == "unknown" for f in findings) else 0


def render(findings: list[Finding]) -> str:
    lines = [f"{f.state:8} {f.item}: {f.detail}" for f in findings]
    for f in findings:
        if f.state == "stalled":
            lines.append(
                f"# bd note {f.item} 'QA prerequisite stalled: {f.detail}' "
                "(coordinator lane files or refreshes the stall note)"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("export", type=Path, help="bd export JSONL file")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--now", help="ISO timestamp override for deterministic runs")
    args = parser.parse_args(argv)
    now = _parse_time(args.now) if args.now else datetime.now(UTC)
    if now is None:
        parser.error("--now must be an ISO timestamp")
    findings = evaluate(load_issues(args.export), now, args.days)
    print(render(findings))
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
