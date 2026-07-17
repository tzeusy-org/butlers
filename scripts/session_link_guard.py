#!/usr/bin/env python3
"""Detect tool-session URLs leaking into PR titles/bodies, commits, or review comments.

Background (bu-mr5t5): a PR merged externally before a scrubbing pass on its
body completed, leaving a session-attribution line permanently baked into a
merge commit on `main`. This module gives CI, and the `butler-qa-pr-review`
validator, a deterministic, fail-closed way to catch that class of leakage
*before* merge instead of after.

The sole exception is an exact ``Claude-Session: https://claude.ai/code/session_...``
line in a terminal Git commit-trailer block. The owner's Claude Code setup adds
that provenance trailer automatically; it remains forbidden in all PR and
review surfaces, and anywhere else in a commit message.

Scope and self-match-safety, by design: this module only ever inspects text
handed to it by the caller (a PR body string, git commit messages, review
comment bodies). It never walks or greps the repository's own source tree.
That means example strings living in this file's docstrings, or in its own
test fixtures, can never trigger a false positive here — nothing in the scan
surface (PR/commit/comment metadata) ever includes repo source file contents.
Callers (CI jobs, the QA validator) must preserve that invariant: never point
this scanner at a checked-out file tree.

Extend SESSION_LINK_PATTERNS below when a new tool's session-link convention
shows up in the wild. Keep each entry's `name` unique and its regex narrowly
scoped to an actual link/label shape, not a bare tool name — a bare vendor
name match would be far too broad and flag unrelated prose.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionLinkPattern:
    name: str
    regex: re.Pattern[str]
    description: str


def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_CLAUDE_CODE_SESSION_URL = r"claude\.ai/code/session[_-][A-Za-z0-9]+"
_ALLOWED_CLAUDE_SESSION_TRAILER = _compiled(
    rf"Claude-Session:[ \t]+https://{_CLAUDE_CODE_SESSION_URL}[ \t]*"
)

# Maintainable constant: one entry per known tool-session link/footer shape.
# Assembled from a survey of this repo's merged PR bodies and commit history
# (bu-mr5t5) plus the other LLM CLI runtimes this codebase already spawns
# (see RuntimeAdapter subclasses: ClaudeCode, Codex, Gemini, OpenCode).
SESSION_LINK_PATTERNS: tuple[SessionLinkPattern, ...] = (
    SessionLinkPattern(
        name="claude-session-footer-label",
        regex=_compiled(r"claude-session\s*:"),
        description="Claude Code commit/PR footer session-attribution label",
    ),
    SessionLinkPattern(
        name="claude-code-session-url",
        regex=_compiled(_CLAUDE_CODE_SESSION_URL),
        description="Claude Code hosted session URL",
    ),
    SessionLinkPattern(
        name="codex-cloud-task-url",
        regex=_compiled(r"chatgpt\.com/codex/tasks/[A-Za-z0-9_-]+"),
        description="OpenAI Codex cloud task URL",
    ),
    SessionLinkPattern(
        name="codex-cloud-task-url-legacy-host",
        regex=_compiled(r"chat\.openai\.com/codex/tasks/[A-Za-z0-9_-]+"),
        description="OpenAI Codex cloud task URL (legacy host)",
    ),
)


@dataclass(frozen=True)
class Finding:
    source: str
    pattern_name: str
    pattern_description: str
    matched_text: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "pattern_name": self.pattern_name,
            "pattern_description": self.pattern_description,
            "matched_text": self.matched_text,
        }


def find_session_links(text: str) -> list[tuple[SessionLinkPattern, str]]:
    """Return every (pattern, matched substring) hit in `text`. Empty if clean."""
    if not text:
        return []
    hits: list[tuple[SessionLinkPattern, str]] = []
    for pattern in SESSION_LINK_PATTERNS:
        for match in pattern.regex.finditer(text):
            hits.append((pattern, match.group(0)))
    return hits


def _strip_allowed_claude_session_commit_trailers(message: str) -> str:
    """Remove exact Claude session trailers from a terminal Git trailer block.

    The generic matcher remains intentionally strict. This source-specific
    preprocessing runs only for commit messages and only removes exact HTTPS
    Claude session lines that ``git interpret-trailers`` recognizes. Git owns
    the trailer grammar, including separator whitespace, folded values, and
    its terminal-block rules. A matching line in the subject/body, or with a
    folded continuation, therefore remains visible to ``find_session_links``.
    """
    original_lines = message.splitlines(keepends=True)
    parse_lines = original_lines.copy()
    candidate_markers: dict[int, str] = {}

    for index, line in enumerate(original_lines):
        if not _ALLOWED_CLAUDE_SESSION_TRAILER.fullmatch(line.rstrip("\r\n")):
            continue

        marker = f"sessionlinkguard{secrets.token_hex(16)}"
        line_ending = line[len(line.rstrip("\r\n")) :]
        parse_lines[index] = f"Claude-Session: {marker}{line_ending}"
        candidate_markers[index] = marker

    if not candidate_markers:
        return message

    proc = subprocess.run(
        ["git", "interpret-trailers", "--parse", "--no-divider"],
        input="".join(parse_lines),
        capture_output=True,
        check=True,
        text=True,
    )
    parsed_trailers = {line.casefold() for line in proc.stdout.splitlines()}

    for index, marker in candidate_markers.items():
        if f"claude-session: {marker}" in parsed_trailers:
            original_lines[index] = ""
    return "".join(original_lines)


def scan_sources(named_texts: dict[str, str]) -> list[Finding]:
    """Scan a {source_label: text} map. Returns findings in source-then-pattern order."""
    findings: list[Finding] = []
    for source, text in named_texts.items():
        scan_text = (
            _strip_allowed_claude_session_commit_trailers(text)
            if source.startswith("commit ")
            else text
        )
        for pattern, matched_text in find_session_links(scan_text):
            findings.append(Finding(source, pattern.name, pattern.description, matched_text))
    return findings


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "session_link_guard: clean — no tool-session links found."
    lines = [f"session_link_guard: found {len(findings)} tool-session link leak(s):"]
    for f in findings:
        lines.append(
            f"  - [{f.source}] {f.pattern_description} ({f.pattern_name}): {f.matched_text!r}"
        )
    lines.append(
        "Strip the offending line(s) before merge — session/task URLs must never "
        "reach a PR title/body, review reply, or non-trailer commit text."
    )
    return "\n".join(lines)


def iter_commit_messages(commit_range: str, cwd: Path | None = None) -> dict[str, str]:
    """Return {"commit <short-sha>": full message} for every commit in commit_range."""
    proc = subprocess.run(
        ["git", "log", "--format=%H%x1f%B%x1e", commit_range],
        cwd=cwd,
        capture_output=True,
        check=True,
        text=True,
    )
    out: dict[str, str] = {}
    for record in proc.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, body = record.partition("\x1f")
        if not sha:
            continue
        out[f"commit {sha[:12]}"] = body
    return out


def _load_review_comments(path: Path) -> dict[str, str]:
    """Load comment bodies from either a flat list or a raw GraphQL PR payload.

    Accepted shapes:
      - flat list: [{"source": "...", "body": "..."}, ...]
      - raw GraphQL PR object: {"reviewThreads": {"nodes": [{"comments":
        {"nodes": [{"body": "..."}, ...]}}, ...]}}
    """
    data: Any = json.loads(path.read_text())
    out: dict[str, str] = {}

    if isinstance(data, list):
        for i, item in enumerate(data):
            source = item.get("source") or f"review comment {i}"
            out[source] = item.get("body", "") or ""
        return out

    if isinstance(data, dict):
        thread_nodes = (data.get("reviewThreads") or {}).get("nodes") or []
        for t_idx, thread in enumerate(thread_nodes):
            comment_nodes = (thread.get("comments") or {}).get("nodes") or []
            for c_idx, comment in enumerate(comment_nodes):
                out[f"review thread {t_idx} comment {c_idx}"] = comment.get("body", "") or ""

    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail closed when a PR title/body, commit message, or review comment "
            "contains a tool-session link/footer pattern (see SESSION_LINK_PATTERNS)."
        )
    )
    parser.add_argument(
        "--pr-title-file", type=Path, help="Path to a file containing the PR title text"
    )
    parser.add_argument(
        "--pr-body-file", type=Path, help="Path to a file containing the PR body text"
    )
    parser.add_argument(
        "--pr-body-stdin", action="store_true", help="Read the PR body text from stdin"
    )
    parser.add_argument(
        "--commit-range",
        help="git log revision range to scan commit messages from, e.g. 'origin/main..HEAD'",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="git repo path to run --commit-range against (default: current directory)",
    )
    parser.add_argument(
        "--review-comments-file",
        type=Path,
        help=(
            "Best-effort: path to a JSON file of review comment bodies. Missing "
            "file is treated as 'no comments available' rather than an error."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON instead of text"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Tracked separately from `named_texts`: a --commit-range with zero commits
    # in it (e.g. no local commits yet ahead of main) is a legitimate "nothing
    # to scan, so it's clean" outcome, not the same as "no input was requested
    # at all" — the two need different messages even though both end up with
    # an empty (or empty-valued) named_texts dict.
    requested_any_input = bool(
        args.pr_title_file or args.pr_body_stdin or args.pr_body_file or args.commit_range
    )

    named_texts: dict[str, str] = {}

    if args.pr_title_file:
        named_texts["pr_title"] = args.pr_title_file.read_text()

    if args.pr_body_stdin:
        named_texts["pr_body"] = sys.stdin.read()
    elif args.pr_body_file:
        named_texts["pr_body"] = args.pr_body_file.read_text()

    if args.commit_range:
        named_texts.update(iter_commit_messages(args.commit_range, cwd=args.repo))

    if args.review_comments_file:
        if args.review_comments_file.exists():
            try:
                named_texts.update(_load_review_comments(args.review_comments_file))
            except (json.JSONDecodeError, OSError) as exc:
                print(
                    f"session_link_guard: warning: could not read review comments "
                    f"({exc}); continuing with body/commit checks only.",
                    file=sys.stderr,
                )
        else:
            print(
                "session_link_guard: warning: review comments file not found; "
                "continuing with body/commit checks only (best-effort surface).",
                file=sys.stderr,
            )

    if not requested_any_input:
        print(
            "session_link_guard: no input provided (need --pr-title-file, "
            "--pr-body-file/--pr-body-stdin, and/or --commit-range); nothing to scan.",
            file=sys.stderr,
        )
        return 0

    findings = scan_sources(named_texts)

    if args.json:
        json.dump([f.to_dict() for f in findings], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(format_findings(findings))

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
