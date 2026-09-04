#!/usr/bin/env python3
"""Validate Butler QA PR review completion conditions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _github import fetch_pr_threads, fetch_required_checks, normalize_repo

# session_link_guard.py is a repo-root script (scripts/), not a package under
# this skill directory. Import it the same way tests/scripts/*.py do: resolve
# the repo root relative to this file and add its scripts/ dir to sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
from session_link_guard import scan_sources  # noqa: E402

_ACCEPTED_RE = re.compile(r"^Accepted in [0-9a-f]{7,40}\.\s*$", re.MULTILINE)
_WONTFIX_RE = re.compile(r"^Wontfix\.\s*$", re.MULTILINE)


def _is_terminal_reply(body: str) -> bool:
    text = (body or "").strip()
    return bool(_ACCEPTED_RE.search(text) or _WONTFIX_RE.search(text))


def _thread_summary(thread: dict[str, Any]) -> dict[str, Any]:
    comments = thread.get("comments", {}).get("nodes", []) or []
    latest = comments[-1] if comments else None
    return {
        "thread_id": thread["id"],
        "path": thread.get("path"),
        "line": thread.get("line"),
        "is_resolved": thread.get("isResolved", False),
        "is_outdated": thread.get("isOutdated", False),
        "top_comment_id": comments[0].get("databaseId") if comments else None,
        "top_comment_url": comments[0].get("url") if comments else None,
        "latest_comment_id": latest.get("databaseId") if latest else None,
        "latest_comment_url": latest.get("url") if latest else None,
        "latest_comment_author": (latest.get("author", {}).get("login") if latest else None),
        "latest_comment_is_terminal": _is_terminal_reply(latest.get("body", ""))
        if latest
        else False,
    }


def _session_link_sources(
    pr: dict[str, Any], active_threads: list[dict[str, Any]]
) -> dict[str, str]:
    """Collect every text surface bu-mr5t5's session-link guard must cover.

    Covers the PR body, every commit message on the current head (so an
    amended-after-review head is re-checked, not just the original), and
    every non-outdated review thread comment. Fetched fresh on each run
    against whatever `headRefOid` the PR is at right now, so an exact-head
    merge gate can't pass on stale, already-scrubbed state.
    """
    sources: dict[str, str] = {"pr_body": pr.get("body") or ""}

    commit_nodes = (pr.get("commits") or {}).get("nodes") or []
    for commit_node in commit_nodes:
        commit = (commit_node or {}).get("commit") or {}
        oid = commit.get("oid") or "unknown"
        sources[f"commit {oid[:12]}"] = commit.get("message", "") or ""

    for t_idx, thread in enumerate(active_threads):
        comment_nodes = (thread.get("comments") or {}).get("nodes") or []
        for c_idx, comment in enumerate(comment_nodes):
            sources[f"review thread {t_idx} comment {c_idx}"] = comment.get("body", "") or ""

    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that a PR has no unresolved non-outdated review threads, "
            "that each thread ends with an Accepted/Wontfix terminal reply, and "
            "that all required GitHub checks are passing."
        )
    )
    parser.add_argument("--repo", default="https://github.com/tzeusy-org/butlers")
    parser.add_argument("--pr", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pr = fetch_pr_threads(args.repo, args.pr)
    raw_threads = pr.get("reviewThreads", {}).get("nodes", []) or []
    active_threads = [thread for thread in raw_threads if not thread.get("isOutdated", False)]
    unresolved = [
        _thread_summary(thread) for thread in active_threads if not thread.get("isResolved", False)
    ]
    missing_terminal_reply = [
        _thread_summary(thread)
        for thread in active_threads
        if not _is_terminal_reply(
            (thread.get("comments", {}).get("nodes", []) or [{}])[-1].get("body", "")
        )
    ]

    required_checks = fetch_required_checks(args.repo, args.pr)
    failing_or_pending_checks = [
        check for check in required_checks if check.get("bucket") != "pass"
    ]

    session_link_findings = scan_sources(_session_link_sources(pr, active_threads))

    ok = (
        not unresolved
        and not missing_terminal_reply
        and not failing_or_pending_checks
        and not session_link_findings
    )

    payload = {
        "ok": ok,
        "repo": normalize_repo(args.repo),
        "pr_number": args.pr,
        "pr_url": pr["url"],
        "head_ref": pr["headRefName"],
        "head_sha": pr["headRefOid"],
        "review_decision": pr.get("reviewDecision"),
        "unresolved_threads": unresolved,
        "threads_missing_terminal_reply": missing_terminal_reply,
        "required_checks": required_checks,
        "failing_or_pending_required_checks": failing_or_pending_checks,
        "session_link_findings": [finding.to_dict() for finding in session_link_findings],
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
