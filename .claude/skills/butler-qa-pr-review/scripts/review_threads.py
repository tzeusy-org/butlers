#!/usr/bin/env python3
"""Inspect and operate on GitHub PR review threads for butler-qa-pr-review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _github import fetch_pr_threads, normalize_repo, run_gh_json


def _thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    comments = thread.get("comments", {}).get("nodes", []) or []
    first = comments[0] if comments else None
    last = comments[-1] if comments else None
    return {
        "thread_id": thread["id"],
        "path": thread.get("path"),
        "line": thread.get("line"),
        "is_resolved": thread.get("isResolved", False),
        "is_outdated": thread.get("isOutdated", False),
        "top_comment_id": first.get("databaseId") if first else None,
        "top_comment_url": first.get("url") if first else None,
        "last_comment_id": last.get("databaseId") if last else None,
        "last_comment_url": last.get("url") if last else None,
        "comments": comments,
    }


def cmd_list(args: argparse.Namespace) -> int:
    pr = fetch_pr_threads(args.repo, args.pr)
    raw_threads = pr.get("reviewThreads", {}).get("nodes", []) or []
    threads = [_thread_payload(thread) for thread in raw_threads]
    if not args.all:
        threads = [
            thread
            for thread in threads
            if not thread["is_resolved"] and not thread["is_outdated"]
        ]

    output = {
        "repo": normalize_repo(args.repo),
        "pr_number": args.pr,
        "pr_url": pr["url"],
        "head_ref": pr["headRefName"],
        "head_sha": pr["headRefOid"],
        "review_decision": pr.get("reviewDecision"),
        "thread_count": len(threads),
        "threads": threads,
    }
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    if args.body_file is not None:
        return Path(args.body_file).read_text(encoding="utf-8")
    raise SystemExit("one of --body or --body-file is required")


def cmd_reply(args: argparse.Namespace) -> int:
    body = _read_body(args)
    route = (
        f"repos/{normalize_repo(args.repo)}/pulls/{args.pr}/comments/"
        f"{args.comment_id}/replies"
    )
    result = run_gh_json(
        [
            "api",
            "--method",
            "POST",
            route,
            "--input",
            "-",
        ],
        stdin_obj={"body": body},
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    result = run_gh_json(
        [
            "api",
            "graphql",
            "-F",
            f"threadId={args.thread_id}",
            "-f",
            (
                "query=mutation($threadId:ID!) { "
                "resolveReviewThread(input:{threadId:$threadId}) { "
                "thread { id isResolved } } }"
            ),
        ]
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List unresolved PR review threads, post inline replies, and resolve "
            "threads using gh-authenticated GitHub API calls."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="List PR review threads as JSON."
    )
    list_parser.add_argument("--repo", default="https://github.com/Tzeusy/butlers")
    list_parser.add_argument("--pr", type=int, required=True)
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="Include resolved and outdated threads too.",
    )
    list_parser.set_defaults(func=cmd_list)

    reply_parser = subparsers.add_parser(
        "reply", help="Reply to a top-level PR review comment."
    )
    reply_parser.add_argument("--repo", default="https://github.com/Tzeusy/butlers")
    reply_parser.add_argument("--pr", type=int, required=True)
    reply_parser.add_argument("--comment-id", type=int, required=True)
    body_group = reply_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body")
    body_group.add_argument("--body-file")
    reply_parser.set_defaults(func=cmd_reply)

    resolve_parser = subparsers.add_parser(
        "resolve", help="Resolve a review thread by GraphQL thread node ID."
    )
    resolve_parser.add_argument("--thread-id", required=True)
    resolve_parser.set_defaults(func=cmd_resolve)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
