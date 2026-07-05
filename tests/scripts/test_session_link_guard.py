"""Tests for scripts/session_link_guard.py.

Regression guard for bu-mr5t5: a PR merged externally before its body was
fully scrubbed of a tool-session link, leaving one permanently baked into a
`main` merge commit. These tests cover the pattern matcher (positive and
negative cases), the git-commit-range and review-comment-file plumbing, and
the CLI's exit-code contract, using fast deterministic inputs — no network,
no real GitHub PR required.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import session_link_guard as slg  # noqa: E402

pytestmark = pytest.mark.unit

# Built from the same two literal fragments so no single copy-pasted literal
# session URL sits in this file — assembled at import time, not grep-able as
# one contiguous string in the source.
_CLAUDE_HOST_FRAGMENT = "claude" + ".ai/code/session"
_CLAUDE_EXAMPLE_URL = "https://" + _CLAUDE_HOST_FRAGMENT + "_01AbCdEfGhIjKlMnOpQrStUv"
_CODEX_EXAMPLE_URL = "https://chatgpt.com/codex/tasks/task_e_012345"


def test_find_session_links_matches_claude_code_session_url() -> None:
    hits = slg.find_session_links(f"Some text with {_CLAUDE_EXAMPLE_URL} inline.")
    assert [p.name for p, _ in hits] == ["claude-code-session-url"]


def test_find_session_links_matches_claude_session_footer_label() -> None:
    text = f"Body text.\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n"
    hits = slg.find_session_links(text)
    names = {p.name for p, _ in hits}
    assert "claude-session-footer-label" in names
    assert "claude-code-session-url" in names


def test_find_session_links_matches_codex_cloud_task_url() -> None:
    hits = slg.find_session_links(f"See {_CODEX_EXAMPLE_URL} for the run.")
    assert [p.name for p, _ in hits] == ["codex-cloud-task-url"]


def test_find_session_links_matches_codex_legacy_host() -> None:
    hits = slg.find_session_links("https://chat.openai.com/codex/tasks/abc-123")
    assert [p.name for p, _ in hits] == ["codex-cloud-task-url-legacy-host"]


@pytest.mark.parametrize(
    "clean_text",
    [
        "",
        "A perfectly normal PR description about routing fixes.",
        "This file provides guidance to Claude Code when working in this repo.",
        "Fixed a bug in the session_records table (unrelated 'session' word).",
        "Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>",
        "See chatgpt.com for details (no /codex/tasks/ path).",
    ],
)
def test_find_session_links_negative_cases_do_not_match(clean_text: str) -> None:
    assert slg.find_session_links(clean_text) == []


def test_scan_sources_labels_findings_by_source() -> None:
    findings = slg.scan_sources(
        {
            "pr_body": "clean body",
            "commit abc123": f"fix: thing\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n",
        }
    )
    assert len(findings) == 2  # footer label + url, both attributed to the commit
    assert {f.source for f in findings} == {"commit abc123"}


def test_format_findings_reports_clean_when_empty() -> None:
    assert "clean" in slg.format_findings([]).lower()


def test_format_findings_lists_each_finding() -> None:
    findings = slg.scan_sources({"pr_body": _CLAUDE_EXAMPLE_URL})
    rendered = slg.format_findings(findings)
    assert "pr_body" in rendered
    assert "claude-code-session-url" in rendered


def test_self_match_guard_scanner_never_reads_the_filesystem_directly() -> None:
    """Design invariant: the matcher is a pure function of caller-supplied text.

    It must never accept a repo/tree path and grep it directly — only this
    file's own docstrings or test fixtures could contain literal example
    patterns, and if the scanner ever grepped source files instead of PR
    metadata, it would flag itself. `find_session_links`/`scan_sources` take
    only `str`/`dict[str, str]`, so that failure mode is structurally
    impossible; this test pins that contract so a future edit can't
    accidentally add a path-scanning code path.
    """
    import inspect

    find_params = inspect.signature(slg.find_session_links).parameters
    scan_params = inspect.signature(slg.scan_sources).parameters
    assert list(find_params) == ["text"]
    assert list(scan_params) == ["named_texts"]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit(path: Path, message: str, filename: str) -> str:
    (path / filename).write_text("content\n")
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_iter_commit_messages_scans_a_revision_range(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "chore: base commit", "a.txt")
    _commit(tmp_path, f"fix: leak\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n", "b.txt")
    head_sha = _commit(tmp_path, "docs: clean commit", "c.txt")

    messages = slg.iter_commit_messages(f"{base_sha}..{head_sha}", cwd=tmp_path)

    assert len(messages) == 2
    findings = slg.scan_sources(messages)
    assert len(findings) == 2  # footer label + url


def test_load_review_comments_accepts_flat_list(tmp_path: Path) -> None:
    path = tmp_path / "comments.json"
    path.write_text(json.dumps([{"source": "review comment 1", "body": _CLAUDE_EXAMPLE_URL}]))
    loaded = slg._load_review_comments(path)
    assert loaded == {"review comment 1": _CLAUDE_EXAMPLE_URL}


def test_load_review_comments_accepts_raw_graphql_payload(tmp_path: Path) -> None:
    payload = {
        "reviewThreads": {
            "nodes": [
                {"comments": {"nodes": [{"body": "clean"}, {"body": _CODEX_EXAMPLE_URL}]}},
            ]
        }
    }
    path = tmp_path / "comments.json"
    path.write_text(json.dumps(payload))
    loaded = slg._load_review_comments(path)
    assert loaded["review thread 0 comment 0"] == "clean"
    assert loaded["review thread 0 comment 1"] == _CODEX_EXAMPLE_URL


def test_main_exits_zero_on_clean_pr_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("A clean PR description with no session links.")
    exit_code = slg.main(["--pr-body-file", str(body_file)])
    assert exit_code == 0
    assert "clean" in capsys.readouterr().out.lower()


def test_main_exits_nonzero_on_leaking_pr_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text(f"Great feature.\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n")
    exit_code = slg.main(["--pr-body-file", str(body_file)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "claude-code-session-url" in out


def test_main_json_output_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text(_CLAUDE_EXAMPLE_URL)
    exit_code = slg.main(["--pr-body-file", str(body_file), "--json"])
    assert exit_code == 1
    findings = json.loads(capsys.readouterr().out)
    assert findings[0]["pattern_name"] == "claude-code-session-url"


def test_main_missing_review_comments_file_is_best_effort_not_fatal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("clean")
    exit_code = slg.main(
        [
            "--pr-body-file",
            str(body_file),
            "--review-comments-file",
            str(tmp_path / "does-not-exist.json"),
        ]
    )
    assert exit_code == 0


def test_main_returns_zero_with_no_inputs_provided(capsys: pytest.CaptureFixture[str]) -> None:
    assert slg.main([]) == 0


def test_main_treats_empty_commit_range_as_clean_not_as_no_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A --commit-range that resolves to zero commits (e.g. run locally with no
    # new commits yet ahead of main) is a legitimate clean scan, not the same
    # "nothing was requested" case as calling main() with no flags at all.
    _init_repo(tmp_path)
    sha = _commit(tmp_path, "chore: base", "a.txt")

    exit_code = slg.main(["--commit-range", f"{sha}..{sha}", "--repo", str(tmp_path)])

    assert exit_code == 0
    assert "clean" in capsys.readouterr().out.lower()
