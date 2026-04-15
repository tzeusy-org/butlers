#!/usr/bin/env python3
"""Validate Butler QA PR review completion conditions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from _github import fetch_pr_threads, fetch_required_checks, normalize_repo

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
        "latest_comment_author": (
            latest.get("author", {}).get("login") if latest else None
        ),
        "latest_comment_is_terminal": _is_terminal_reply(latest.get("body", "")) if latest else False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that a PR has no unresolved non-outdated review threads, "
            "that each thread ends with an Accepted/Wontfix terminal reply, and "
            "that all required GitHub checks are passing."
        )
    )
    parser.add_argument("--repo", default="https://github.com/Tzeusy/butlers")
    parser.add_argument("--pr", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pr = fetch_pr_threads(args.repo, args.pr)
    raw_threads = pr.get("reviewThreads", {}).get("nodes", []) or []
    active_threads = [thread for thread in raw_threads if not thread.get("isOutdated", False)]
    unresolved = [_thread_summary(thread) for thread in active_threads if not thread.get("isResolved", False)]
    missing_terminal_reply = [
        _thread_summary(thread)
        for thread in active_threads
        if not _is_terminal_reply((thread.get("comments", {}).get("nodes", []) or [{}])[-1].get("body", ""))
    ]

    required_checks = fetch_required_checks(args.repo, args.pr)
    failing_or_pending_checks = [
        check for check in required_checks if check.get("bucket") != "pass"
    ]

    ok = not unresolved and not missing_terminal_reply and not failing_or_pending_checks

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
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
