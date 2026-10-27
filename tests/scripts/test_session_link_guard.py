"""Tests for scripts/session_link_guard.py.

Regression guard for bu-mr5t5: a PR merged externally before its body was
fully scrubbed of a tool-session link, leaving one permanently baked into a
`main` merge commit. These tests cover strict PR title/body/comment matching,
the one allowed exact Claude session commit trailer, git-commit-range and
review-comment-file plumbing, and the CLI's exit-code contract, using fast
deterministic inputs — no network, no real GitHub PR required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import fetch_pr_metadata as metadata  # noqa: E402
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


@pytest.mark.parametrize("label", ["Claude-Session", "claude-session", "CLAUDE-SESSION"])
def test_find_session_links_matches_claude_session_footer_label(label: str) -> None:
    text = f"Body text.\n\n{label}: {_CLAUDE_EXAMPLE_URL}\n"
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
            "review comment 1": f"See {_CLAUDE_EXAMPLE_URL} for details.",
        }
    )
    assert len(findings) == 1
    assert {f.source for f in findings} == {"review comment 1"}


def test_scan_sources_allows_exact_claude_session_commit_trailer() -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"fix: retain valid Claude Code attribution\n\n"
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"
            )
        }
    )
    assert findings == []


@pytest.mark.parametrize(
    "terminal_context",
    [
        "Other-Key : valid Git trailer with space before the separator\n",
        "Other-Key: a folded trailer value\n continuation text\n",
        (
            "ordinary terminal prose accepted by Git's 25-percent trailer rule\n"
            "Signed-off-by: Example <example@example.invalid>\n"
        ),
    ],
)
def test_scan_sources_allows_claude_footer_in_git_terminal_trailer_context(
    terminal_context: str,
) -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"fix: retain valid Git trailer semantics\n\n"
                f"{terminal_context}"
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"
            )
        }
    )

    assert findings == []


def test_scan_sources_rejects_claude_footer_without_git_trailer_separator() -> None:
    findings = slg.scan_sources({"commit abc123": f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"})

    assert {finding.pattern_name for finding in findings} == {
        "claude-session-footer-label",
        "claude-code-session-url",
    }


def test_scan_sources_rejects_folded_claude_footer_value() -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"fix: reject a non-exact trailer value\n\n"
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"
                " continuation text\n"
            )
        }
    )

    assert {finding.pattern_name for finding in findings} == {
        "claude-session-footer-label",
        "claude-code-session-url",
    }


def test_scan_sources_keeps_nonterminal_footer_when_a_terminal_footer_is_valid() -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n\n"
                "fix: terminal trailer remains valid\n\n"
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"
            )
        }
    )

    assert {finding.pattern_name for finding in findings} == {
        "claude-session-footer-label",
        "claude-code-session-url",
    }


def test_scan_sources_rejects_claude_session_outside_terminal_trailer_block() -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"fix: avoid session leak\n\n"
                f"Claude-Session: {_CLAUDE_EXAMPLE_URL}\n\n"
                "This prose keeps the line out of the terminal trailer block.\n"
            )
        }
    )
    assert {finding.pattern_name for finding in findings} == {
        "claude-session-footer-label",
        "claude-code-session-url",
    }


@pytest.mark.parametrize("indent", [" ", "\t"])
def test_scan_sources_rejects_indented_claude_session_terminal_line(indent: str) -> None:
    findings = slg.scan_sources(
        {
            "commit abc123": (
                f"fix: avoid an indented session leak\n\n"
                f"{indent}Claude-Session: {_CLAUDE_EXAMPLE_URL}\n"
            )
        }
    )

    assert {finding.pattern_name for finding in findings} == {
        "claude-session-footer-label",
        "claude-code-session-url",
    }


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


def test_iter_commit_messages_allows_exact_claude_session_trailer(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "chore: base commit", "a.txt")
    _commit(tmp_path, f"fix: leak\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n", "b.txt")
    head_sha = _commit(tmp_path, "docs: clean commit", "c.txt")

    messages = slg.iter_commit_messages(f"{base_sha}..{head_sha}", cwd=tmp_path)

    assert len(messages) == 2
    findings = slg.scan_sources(messages)
    assert findings == []


@pytest.mark.parametrize(
    ("trailer_line", "expected_exit_code"),
    [
        (f"Claude-Session: {_CLAUDE_EXAMPLE_URL}", 0),
        (f"claude-session: {_CLAUDE_EXAMPLE_URL}", 1),
        (f"CLAUDE-SESSION: {_CLAUDE_EXAMPLE_URL}", 1),
        (f"ClAuDe-SeSsIoN: {_CLAUDE_EXAMPLE_URL}", 1),
        (f" Claude-Session: {_CLAUDE_EXAMPLE_URL}", 1),
        (f"Claude-Session : {_CLAUDE_EXAMPLE_URL}", 1),
    ],
    ids=("canonical", "lowercase", "uppercase", "mixed-case", "indented", "spaced"),
)
def test_main_commit_range_only_exempts_exact_claude_session_trailer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    trailer_line: str,
    expected_exit_code: int,
) -> None:
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "chore: base commit", "a.txt")
    head_sha = _commit(tmp_path, f"fix: trailer check\n\n{trailer_line}\n", "b.txt")

    exit_code = slg.main(["--commit-range", f"{base_sha}..{head_sha}", "--repo", str(tmp_path)])

    assert exit_code == expected_exit_code
    if expected_exit_code:
        assert "claude-code-session-url" in capsys.readouterr().out
    else:
        assert "clean" in capsys.readouterr().out.lower()


def test_main_rejects_session_url_after_embedded_record_separator_in_commit_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "chore: base commit", "a.txt")
    head_sha = _commit(
        tmp_path,
        f"fix: do not split commit bodies\n\n\x1ehidden {_CLAUDE_EXAMPLE_URL}\n",
        "b.txt",
    )

    exit_code = slg.main(["--commit-range", f"{base_sha}..{head_sha}", "--repo", str(tmp_path)])

    assert exit_code == 1
    assert "claude-code-session-url" in capsys.readouterr().out


def test_main_rejects_claude_session_after_git_divider_in_commit_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "chore: base commit", "a.txt")
    head_sha = _commit(
        tmp_path,
        f"fix: retain post-divider commit text\n\n---\n\nClaude-Session: {_CLAUDE_EXAMPLE_URL}\n",
        "b.txt",
    )

    exit_code = slg.main(["--commit-range", f"{base_sha}..{head_sha}", "--repo", str(tmp_path)])

    assert exit_code == 1
    assert "claude-code-session-url" in capsys.readouterr().out


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

    # REQ-testing-044: public mode retains detection without exposing a match.
    assert slg.main(["--pr-body-file", str(body_file), "--redact-findings"]) == 1
    public = capsys.readouterr().out
    assert "claude-code-session-url" in public and _CLAUDE_EXAMPLE_URL not in public
    assert slg.main(["--pr-body-file", str(body_file), "--redact-findings", "--json"]) == 1
    categories = json.loads(capsys.readouterr().out)
    assert categories["count"] == 2 and all("matched_text" not in x for x in categories["findings"])
    # Execute the actual workflow producer with a private, synthetic gh adapter.
    # The stale event strings are deliberately clean and cannot supply authority.
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(
        x
        for job in workflow["jobs"].values()
        for x in job.get("steps", [])
        if x.get("name") == "Write PR title and body to files"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "gh"
    fake.write_text(
        '#!/bin/sh\n[ "$1" = api ] || exit 9\n'
        '[ "$2" = repos/tzeusy-org/butlers/pulls/123 ] || exit 9\n'
        'cat "$PR_FIXTURE"\n'
    )
    fake.chmod(0o755)
    fixture = tmp_path / "private-response.json"
    runner = tmp_path / "runner"
    runner.mkdir()
    environment = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "PR_NUMBER": "123",
        "PR_TITLE": "clean frozen title",
        "PR_BODY": "clean frozen body",
        "PR_FIXTURE": str(fixture),
        "RUNNER_TEMP": str(runner),
    }
    for body_text, number, expected in (
        (_CLAUDE_EXAMPLE_URL, 123, 1),
        (None, 123, 0),
        ("clean", 124, None),
        ({"malformed": True}, 123, None),
    ):
        fixture.write_text(
            json.dumps(
                {
                    "number": number,
                    "title": "actual live title",
                    "body": body_text,
                    "base": {"repo": {"full_name": "tzeusy-org/butlers"}},
                }
            )
        )
        producer = subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert _CLAUDE_EXAMPLE_URL not in producer.stdout + producer.stderr
        if expected is None:
            assert producer.returncode != 0 and not (runner / "pr_body.txt").exists()
        else:
            assert producer.returncode == 0
            assert (
                slg.main(["--pr-body-file", str(runner / "pr_body.txt"), "--redact-findings"])
                == expected
            )
            assert _CLAUDE_EXAMPLE_URL not in capsys.readouterr().out

    # REQ-testing-044: one failed read must not force a full CI rerun. Reuse
    # the actual workflow/scanner fixture and keep provider diagnostics private.
    attempts = tmp_path / "attempts"
    environment["ATTEMPTS"] = str(attempts)
    environment["ATTEMPT_SNAPSHOT"] = str(tmp_path / "attempt-snapshot.json")
    fixture.write_text(
        json.dumps(
            {
                "number": 123,
                "title": "actual live title",
                "body": "clean retry body",
                "base": {"repo": {"full_name": "tzeusy-org/butlers"}},
            }
        )
    )
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:] == ['api', 'repos/tzeusy-org/butlers/pulls/123']\n"
        "counter = pathlib.Path(os.environ['ATTEMPTS'])\n"
        "counter.write_text((counter.read_text() if counter.exists() else '') + 'x')\n"
        "if counter.read_text() == 'x':\n"
        "    root = pathlib.Path(os.environ['RUNNER_TEMP'])\n"
        "    pathlib.Path(os.environ['ATTEMPT_SNAPSHOT']).write_text(json.dumps([n for n in "
        "['pr_title.txt', 'pr_body.txt', 'pr_metadata_receipt.json'] if (root / n).exists()]))\n"
        "    print('private-provider-failure', file=sys.stderr)\n"
        "    raise SystemExit(7)\n"
        "sys.stdout.write(pathlib.Path(os.environ['PR_FIXTURE']).read_text())\n"
    )
    for name in ("pr_title.txt", "pr_body.txt", "pr_metadata_receipt.json"):
        (runner / name).write_text("stale clean input")
    producer = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert producer.returncode == 0
    assert attempts.read_text() == "xx"
    assert json.loads(Path(environment["ATTEMPT_SNAPSHOT"]).read_text()) == []
    assert "private-provider-failure" not in producer.stdout + producer.stderr
    assert (runner / "pr_title.txt").read_text() == "actual live title"
    assert (runner / "pr_body.txt").read_text() == "clean retry body"
    receipt = json.loads((runner / "pr_metadata_receipt.json").read_text())
    assert receipt["number"] == 123 and receipt["repository"] == "tzeusy-org/butlers"
    assert slg.main(["--pr-body-file", str(runner / "pr_body.txt"), "--redact-findings"]) == 0


def test_main_exits_nonzero_on_leaking_pr_title(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    title_file = tmp_path / "title.txt"
    title_file.write_text(f"fix: inspect {_CLAUDE_EXAMPLE_URL}")

    exit_code = slg.main(["--pr-title-file", str(title_file)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "pr_title" in out
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


@pytest.mark.parametrize("mode", ["first", "recover", "exhaust", "budget", "oversleep"])
def test_metadata_retry_obeys_one_deadline(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control only time/process boundaries; run the real retry and validator."""
    now = 0.0
    waits: list[float] = []
    caps: list[float] = []
    good = json.dumps(
        {
            "number": 123,
            "title": "live",
            "body": None,
            "base": {"repo": {"full_name": "tzeusy-org/butlers"}},
        }
    ).encode()

    def run(command, *, capture_output, check, timeout):
        nonlocal now
        assert command == ["gh", "api", "repos/tzeusy-org/butlers/pulls/123"]
        assert capture_output and check
        caps.append(timeout)
        if mode == "first" or mode == "recover" and len(caps) == 3:
            return subprocess.CompletedProcess(command, 0, stdout=good)
        now += 96 if mode == "budget" else (31 if mode == "recover" else 0)
        if len(caps) == 2:
            raise subprocess.TimeoutExpired(command, timeout, stderr=b"private-provider-error")
        raise subprocess.CalledProcessError(7, command, stderr=b"private-provider-error")

    def sleep(seconds):
        nonlocal now
        waits.append(seconds)
        now += 100 if mode == "oversleep" else seconds

    monkeypatch.setattr(metadata.time, "monotonic", lambda: now)
    monkeypatch.setattr(metadata.time, "sleep", sleep)
    monkeypatch.setattr(metadata.subprocess, "run", run)
    if mode in {"first", "recover"}:
        assert metadata.fetch(123) == ("live", "")
    else:
        with pytest.raises(subprocess.SubprocessError):
            metadata.fetch(123)
    expected = {
        "first": ([30], []),
        "recover": ([30, 30, 28], [2, 5]),
        "exhaust": ([30, 30, 30], [2, 5]),
        "budget": ([30], []),
        "oversleep": ([30], [2]),
    }
    assert (caps, waits) == expected[mode]


def test_metadata_invalid_reads_never_retry_or_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    good = {
        "number": 123,
        "title": "private-title",
        "body": "private-body",
        "base": {"repo": {"full_name": "tzeusy-org/butlers"}},
    }
    variants = [
        b"private-invalid-json",
        json.dumps({**good, "base": {"repo": {"full_name": "wrong/repo"}}}).encode(),
        json.dumps({**good, "title": 8}).encode(),
        json.dumps({**good, "number": True}).encode(),
        OSError("private-local-error"),
    ]
    paths = [tmp_path / n for n in ("pr_title.txt", "pr_body.txt", "pr_metadata_receipt.json")]
    for value in variants:
        calls = []

        def run(command, *, calls=calls, value=value, **kwargs):
            calls.append(command)
            if isinstance(value, OSError):
                raise value
            return subprocess.CompletedProcess(command, 0, stdout=value)

        with monkeypatch.context() as patch:
            patch.setattr(metadata.subprocess, "run", run)
            for path in paths:
                path.write_text("stale private data")
            assert metadata.main(["--number", "123", "--output-dir", str(tmp_path)]) == 1
        assert len(calls) == 1
        assert not any(path.exists() for path in paths)
        output = capsys.readouterr()
        assert output.out == "pr_metadata: unavailable_or_invalid\n" and output.err == ""
    for value in (True, 0, "123"):
        with pytest.raises(ValueError):
            metadata.fetch(value)
    # Invalid CLI values must also stay categorical, before any provider call.
    with monkeypatch.context() as patch:
        patch.setattr(metadata, "fetch", lambda _: pytest.fail("invalid argument reached fetch"))
        assert (
            metadata.main(["--number", "private-not-a-number", "--output-dir", str(tmp_path)]) == 1
        )
        assert metadata.main(["--unknown-private-option"]) == 1
    output = capsys.readouterr()
    assert output.out == "pr_metadata: unavailable_or_invalid\n" * 2 and output.err == ""


def test_metadata_cleanup_failures_are_categorical_and_attempt_all_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    names = ("pr_title.txt", "pr_body.txt", "pr_metadata_receipt.json")
    real_unlink, real_write = Path.unlink, Path.write_text
    for phase in ("before-read", "after-partial-publication", "terminal-read"):
        directory = tmp_path / phase
        directory.mkdir()
        for name in names:
            (directory / name).write_text("stale")
        removed, fetched = [], []

        def unlink(path, *args, removed=removed, phase=phase, **kwargs):
            removed.append(path.name)
            if phase == "before-read" and path.name == names[0]:
                raise PermissionError("private-removal-error")
            if phase == "after-partial-publication" and len(removed) > 3 and path.name == names[0]:
                raise PermissionError("private-secondary-cleanup-error")
            return real_unlink(path, *args, **kwargs)

        def write(path, text, *args, **kwargs):
            if path.name == names[1]:
                raise OSError("private-publication-error")
            return real_write(path, text, *args, **kwargs)

        def fetch(number, *, fetched=fetched, phase=phase):
            fetched.append(number)
            if phase == "terminal-read":
                raise subprocess.CalledProcessError(7, "gh api", stderr=b"private-terminal-error")
            return "private-title", "private-body"

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", unlink)
            patch.setattr(Path, "write_text", write)
            patch.setattr(metadata, "fetch", fetch)
            assert metadata.main(["--number", "123", "--output-dir", str(directory)]) == 1
        assert removed == list(names) * (1 if phase == "before-read" else 2)
        assert fetched == ([] if phase == "before-read" else [123])
        assert not (directory / names[1]).exists() and not (directory / names[2]).exists()
        output = capsys.readouterr()
        assert output.out == "pr_metadata: unavailable_or_invalid\n" and output.err == ""


def test_metadata_workflow_watchdog_kills_owned_group_and_cleans_partial_outputs(
    tmp_path: Path,
) -> None:
    implementation = subprocess.run(
        ["timeout", "--version"], capture_output=True, text=True, check=True, timeout=5
    )
    assert "GNU coreutils" in implementation.stdout, "GNU timeout required; group proof unsupported"
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(
        x
        for x in workflow["jobs"]["guards"]["steps"]
        if x.get("name") == "Write PR title and body to files"
    )
    # Execute the exact shell path, shortening only its two test-owned timers.
    run = step["run"].replace("--kill-after=3s 100s", "--kill-after=0.2s 1s")
    assert run != step["run"]
    command = tmp_path / "step.sh"
    command.write_text(run)
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "python3"
    fake.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""
        import os, pathlib, select, signal, subprocess, sys, time
        root = pathlib.Path(os.environ['RUNNER_TEMP'])
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / 'owned-group').write_text(str(os.getpgrp()))
        read_fd, write_fd = os.pipe()
        try:
            try:
                child = subprocess.Popen([sys.executable, '-c',
                    'import os,signal,sys,time; '
                    'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'os.write(int(sys.argv[1]),b"R"); os.close(int(sys.argv[1])); time.sleep(60)',
                    str(write_fd)], pass_fds=(write_fd,))
            finally:
                os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 0.5)
            assert ready and os.read(read_fd, 2) == b"R"
        finally:
            os.close(read_fd)
        for name in ('pr_title.txt', 'pr_body.txt', 'pr_metadata_receipt.json'):
            (root / name).write_text('private-partial-output')
        (root / 'started').write_text('two owned processes')
        while True:
            time.sleep(1)
        """)
    )
    fake.chmod(0o755)
    # Isolated Linux subreaper owns ONLY this synthetic shell's descendants.
    # Reap the killed monitor/helper/child, not merely the direct shell leader.
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text(
        textwrap.dedent("""
        import ctypes, errno, json, os, pathlib, signal, subprocess, sys, time
        assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
        root = pathlib.Path(os.environ['RUNNER_TEMP'])
        child = subprocess.Popen(['bash', '-e', sys.argv[1]], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        settled = False
        try:
            out, err = child.communicate(timeout=5)
            assert child.returncode != 0
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    settled = True
                    break
                if pid == 0:
                    time.sleep(0.01)
            assert settled
            assert (root / 'started').read_text() == 'two owned processes'
            assert not any((root / n).exists() for n in
                           ('pr_title.txt', 'pr_body.txt', 'pr_metadata_receipt.json'))
            assert b'private-partial-output' not in out + err
            assert b'pr_metadata: unavailable_or_invalid' in out
            print(json.dumps({'nonzero': True, 'owned_children_settled': settled,
                              'outputs_absent': True}))
        finally:
            # Failure cleanup is confined to the exact group this helper wrote.
            if not settled:
                marker = root / 'owned-group'
                if marker.exists():
                    try:
                        os.killpg(int(marker.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                try:
                    child.wait(timeout=2)
                finally:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        try:
                            pid, status = os.waitpid(-1, os.WNOHANG)
                        except ChildProcessError:
                            break
                        if pid == 0:
                            time.sleep(0.01)
        """)
    )
    result = subprocess.run(
        [sys.executable, str(supervisor), str(command)],
        env={
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
            "PR_NUMBER": "123",
        },
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "nonzero": True,
        "owned_children_settled": True,
        "outputs_absent": True,
    }
