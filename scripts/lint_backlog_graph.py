#!/usr/bin/env python3
"""Report dispatch-blocking Beads graph states without changing any bead.

Read the complete graph via ``bd list --all --limit 0 --json`` or offline
``--issues-json-file`` (JSON array/object or native ``bd export`` JSONL).
G1-G6 exit 1; G7 is advisory. Input failures exit 2 even with --report-only.
This is local operator tooling, never a CI gate or an automatic graph repair.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

RULES = frozenset(f"G{i}" for i in range(1, 8))
BLOCK_EXEMPT_LABELS = frozenset({"pr-review", "pr-review-task", "owner-gated", "external-gate"})
OWNER_ASSIGNEES = frozenset({"tze"})
_AGENT_ACTOR = re.compile(
    r"^(?:coordinator|agent|worker|codex|claude|gemini|gpt|orca)(?:[:/-]|$)", re.I
)
_HEARTBEAT_BLOCK = re.compile(r"\[beads-heartbeat\](.*?)\[/beads-heartbeat\]", re.S)
_HEARTBEAT_TIME = re.compile(r"\blast_heartbeat_at=([^\s]+)")


class InputUnavailableError(ValueError):
    """The input cannot establish an unambiguous issue graph."""


def _validate_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise InputUnavailableError("expected an issue object or array")
    issues = []
    seen = set()
    for record in data:
        if not isinstance(record, dict):
            raise InputUnavailableError("expected issue objects")
        if record.get("_type", "issue") != "issue":
            continue
        if any(
            not isinstance(record.get(key), str) or not record[key]
            for key in ("id", "title", "status")
        ):
            raise InputUnavailableError("issue requires nonempty id, title and status")
        if record["id"] in seen:
            raise InputUnavailableError("duplicate issue identity")
        seen.add(record["id"])
        dependencies = record.get("dependencies", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(edge, dict)
            or not isinstance(edge.get("type"), str)
            or not isinstance(edge.get("depends_on_id"), str)
            or not edge["depends_on_id"]
            for edge in dependencies
        ):
            raise InputUnavailableError("expected typed dependency edges with depends_on_id")
        labels = record.get("labels", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise InputUnavailableError("expected a list of labels")
        for key in ("parent", "assignee", "owner", "notes", "updated_at"):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise InputUnavailableError(f"expected a string {key}")
        issues.append(record)
    return issues


def load_issues(path: Path | None) -> list[dict[str, Any]]:
    """Use the decision lint's document-first, JSONL-fallback input shape."""
    if path is None:
        try:
            result = subprocess.run(
                ["bd", "list", "--all", "--limit", "0", "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InputUnavailableError("live bd read unavailable") from exc
        if result.returncode != 0:
            # bd diagnostics can contain issue notes or connection details.
            raise InputUnavailableError(f"live bd read failed (exit {result.returncode})")
        text = result.stdout
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise InputUnavailableError("snapshot unreadable") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise InputUnavailableError("invalid JSON/JSONL snapshot") from exc
        if not data:
            raise InputUnavailableError("empty snapshot") from exc
    return _validate_records(data)


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _is_owner(issue: dict[str, Any]) -> bool:
    assignee = issue.get("assignee")
    return bool(assignee) and (assignee in OWNER_ASSIGNEES or assignee == issue.get("owner"))


def lint_issues(
    records: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    rules: set[str] | frozenset[str] = RULES,
) -> list[dict[str, Any]]:
    """Return one finding per bead/rule, in stable issue/rule order.

    Missing blocker records never prove G3's all-closed predicate. Parent walks
    are cycle-safe so a broken graph can still be diagnosed without hanging.
    """
    issues = _validate_records(records)
    index = {issue["id"]: issue for issue in issues}
    parents: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for issue in issues:
        ids = {
            edge["depends_on_id"]
            for edge in issue.get("dependencies", [])
            if edge["type"] in {"parent-child", "parent"}
        }
        if issue.get("parent"):
            ids.add(issue["parent"])
        parents[issue["id"]] = ids
        for parent in ids:
            children.setdefault(parent, set()).add(issue["id"])
    clock = now or datetime.now(UTC)
    results = []

    def live(id: str) -> bool:
        return id in index and index[id]["status"] != "closed"

    def emit(issue: dict[str, Any], rule: str, name: str, message: str, fix: str) -> None:
        if rule in rules:
            results.append(
                {
                    "id": issue["id"],
                    "title": issue["title"],
                    "ok": False,
                    "violations": [f"{rule} {name}: {message} Fix: {fix}"],
                }
            )

    for issue in sorted(issues, key=lambda i: i["id"]):
        id = issue["id"]
        if not live(id):
            continue
        blockers = {
            edge["depends_on_id"]
            for edge in issue.get("dependencies", [])
            if edge["type"] == "blocks"
        }
        live_children = {child for child in children.get(id, ()) if live(child)}
        labels = set(issue.get("labels", []))
        if blockers & live_children:
            emit(
                issue,
                "G1",
                "parent-blocks-child",
                "parent waits on its own live child.",
                "Remove the blocks edge; close the parent after its children.",
            )
        ancestors = set()
        pending = list(parents[id])
        while pending:
            parent = pending.pop()
            if parent not in ancestors:
                ancestors.add(parent)
                pending.extend(parents.get(parent, ()))
        if blockers & ancestors:
            emit(
                issue,
                "G2",
                "waits-on-ancestor",
                "work waits on its own parent or ancestor.",
                "Remove the edge or point it at the specific sibling.",
            )
        if (
            issue["status"] == "blocked"
            and blockers
            and all(b in index and index[b]["status"] == "closed" for b in blockers)
        ):
            emit(
                issue,
                "G3",
                "stale-blocked",
                "all recorded blockers are closed.",
                "Reopen the bead or add its real blocker.",
            )
        if (
            issue["status"] == "blocked"
            and not blockers
            and not issue.get("assignee")
            and not labels & BLOCK_EXEMPT_LABELS
        ):
            emit(
                issue,
                "G4",
                "blocked-without-reason",
                "unassigned blocked work has no blocking edge or exemption.",
                "Add its real blocking edge or reopen it.",
            )
        if issue["status"] == "open" and issue.get("issue_type") != "epic" and live_children:
            emit(
                issue,
                "G5",
                "claimable-parent",
                "open non-epic work has live children.",
                "Retype the parent to epic.",
            )
        has_owner_gate = any(
            b in index
            and index[b]["status"] in {"open", "in_progress", "blocked", "deferred"}
            and (_is_owner(index[b]) or "decision" in index[b].get("labels", []))
            for b in blockers
        )
        if (
            issue["status"] == "open"
            and not issue.get("assignee")
            and "owner-gated" in labels
            and not has_owner_gate
        ):
            emit(
                issue,
                "G6",
                "label-only-owner-gate",
                "the owner-gated label has no unresolved owner/decision blocker.",
                "Add the owner decision edge, or decide it and remove the label.",
            )
        if _AGENT_ACTOR.match(issue.get("assignee") or ""):
            times = [_timestamp(issue.get("updated_at"))]
            blocks = _HEARTBEAT_BLOCK.findall(issue.get("notes") or "")
            heartbeat = _HEARTBEAT_TIME.findall(blocks[-1]) if blocks else []
            if heartbeat:
                times.append(_timestamp(heartbeat[-1]))
            recent = any(t is not None and t > clock - timedelta(hours=24) for t in times)
            if not recent:
                emit(
                    issue,
                    "G7",
                    "stale-claim",
                    "agent claim has no update or latest heartbeat newer than 24 hours.",
                    "The coordinator verifies and releases the stale claim.",
                )
    return results


def _rules(value: str) -> set[str]:
    selected = set(value.split(","))
    if not selected <= RULES:
        raise argparse.ArgumentTypeError("rules must be comma-separated G1 through G7")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--issues-json-file", type=Path, help="Offline JSON array/object or bd export JSONL"
    )
    parser.add_argument("--json", action="store_true", help="Emit only the finding array on stdout")
    parser.add_argument(
        "--rules", type=_rules, default=RULES, help="Only these comma-separated rule codes"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="Exit 0 for findings; input errors still exit 2"
    )
    args = parser.parse_args(argv)
    try:
        results = lint_issues(load_issues(args.issues_json_file), rules=args.rules)
    except InputUnavailableError as exc:
        print(f"lint_backlog_graph: could not obtain issue data: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for result in results:
            message = result["violations"][0]
            level = "warning" if message.startswith("G7 ") else "error"
            print(f"{result['id']} [{level}] {message}")
        if not results:
            print("Backlog graph: no findings.")
    failures = any(not r["violations"][0].startswith("G7 ") for r in results)
    return 1 if failures and not args.report_only else 0


if __name__ == "__main__":
    raise SystemExit(main())
