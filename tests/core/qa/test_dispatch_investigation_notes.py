"""Tests for INVESTIGATION_NOTES.md parsing and PR body substitution.

Covers:
- ``_load_investigation_notes``: parses well-formed file, handles missing file,
  missing sections, extra sections, case-insensitive headers, empty sections,
  out-of-order sections, ``worktree_path=None``.
- ``_create_qa_pr``: substitutes agent-provided sections into the PR body when
  the notes file exists; falls back to placeholder text when it does not.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    _NOTES_PLACEHOLDER,
    _create_qa_pr,
    _load_investigation_notes,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.repo_whitelist import RepoWhitelist

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding() -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint="a" * 64,
        source_type="log_scanner",
        source_butler="finance",
        severity=1,
        exception_type="ValueError",
        event_summary="test error",
        call_site="src/foo.py:bar",
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_loaded_whitelist(repos: list[str]) -> RepoWhitelist:
    wl = RepoWhitelist(db_pool=None)
    wl._allowed = frozenset(repos)
    wl._loaded = True
    wl._last_loaded_at = time.monotonic()
    return wl


def _write_notes(worktree: Path, content: str) -> Path:
    """Write INVESTIGATION_NOTES.md to the agent's CWD inside the worktree."""
    agent_dir = worktree / ".tmp" / "qa-agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    notes = agent_dir / "INVESTIGATION_NOTES.md"
    notes.write_text(content, encoding="utf-8")
    return notes


# ---------------------------------------------------------------------------
# _load_investigation_notes — pure parser tests
# ---------------------------------------------------------------------------


def test_load_notes_returns_empty_when_worktree_path_is_none():
    assert _load_investigation_notes(None) == {}


def test_load_notes_returns_empty_when_file_missing(tmp_path: Path):
    # Worktree exists but the agent never wrote the notes file.
    assert _load_investigation_notes(tmp_path) == {}


def test_load_notes_parses_well_formed_file(tmp_path: Path):
    _write_notes(
        tmp_path,
        """\
## Root Cause
The `foo` helper raised `ValueError` because the input was empty.

## Fix Summary
Added a guard in `foo()` that returns early when the input is empty.

## Test Coverage
Added `test_foo_empty_input` covering the new guard.
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert set(notes) == {"root_cause", "fix_summary", "test_coverage"}
    assert "ValueError" in notes["root_cause"]
    assert "guard in `foo()`" in notes["fix_summary"]
    assert "test_foo_empty_input" in notes["test_coverage"]


def test_load_notes_is_case_insensitive_on_headers(tmp_path: Path):
    _write_notes(
        tmp_path,
        """\
## ROOT CAUSE
rc body

## fix summary
fs body

## Test COVERAGE
tc body
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert notes == {
        "root_cause": "rc body",
        "fix_summary": "fs body",
        "test_coverage": "tc body",
    }


def test_load_notes_partial_sections_returns_partial_dict(tmp_path: Path):
    """Missing sections are absent from the dict — caller substitutes placeholders."""
    _write_notes(
        tmp_path,
        """\
## Root Cause
only this section

## Fix Summary
and this one
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert notes == {
        "root_cause": "only this section",
        "fix_summary": "and this one",
    }
    assert "test_coverage" not in notes


def test_load_notes_ignores_extra_h2_sections(tmp_path: Path):
    _write_notes(
        tmp_path,
        """\
## Background
some unrelated stuff

## Root Cause
rc body

## Notes
more unrelated stuff

## Fix Summary
fs body

## Test Coverage
tc body
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert set(notes) == {"root_cause", "fix_summary", "test_coverage"}
    assert "unrelated" not in notes["root_cause"]


def test_load_notes_drops_empty_sections(tmp_path: Path):
    """A header with no body is treated as missing (placeholder will fill in)."""
    _write_notes(
        tmp_path,
        """\
## Root Cause
real content

## Fix Summary

## Test Coverage
   \t
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert notes == {"root_cause": "real content"}


def test_load_notes_accepts_sections_in_any_order(tmp_path: Path):
    _write_notes(
        tmp_path,
        """\
## Test Coverage
tc body

## Root Cause
rc body

## Fix Summary
fs body
""",
    )
    notes = _load_investigation_notes(tmp_path)
    assert notes["root_cause"] == "rc body"
    assert notes["fix_summary"] == "fs body"
    assert notes["test_coverage"] == "tc body"


# ---------------------------------------------------------------------------
# _create_qa_pr — body substitution integration tests
# ---------------------------------------------------------------------------


def _capture_gh_pr_create_subprocess(remote_url: str, captured_args: list):
    """Build a fake create_subprocess_exec that captures gh pr create args.

    Sequence of subprocess calls inside _create_qa_pr that we need to satisfy:
    1. git log (no-op detection) → returns one commit (so push proceeds)
    2. git remote get-url → returns remote_url
    3. gh auth setup-git → success
    4. git push → success
    5. gh pr create → success, returns PR URL on stdout
    6. gh pr view → success, returns PR metadata
    Any further calls return success.
    """
    call_index = 0

    async def _fake_subprocess(*args, **kwargs):
        nonlocal call_index
        captured_args.append(args)
        proc = MagicMock()
        if call_index == 0:
            proc.communicate = AsyncMock(return_value=(b"abc1234 fix: something\n", b""))
            proc.returncode = 0
        elif call_index == 1:
            proc.communicate = AsyncMock(return_value=(remote_url.encode(), b""))
            proc.returncode = 0
        elif call_index == 2:
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        elif call_index == 3:
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        elif call_index == 4:
            proc.communicate = AsyncMock(
                return_value=(b"https://github.com/acme/repo/pull/42\n", b"")
            )
            proc.returncode = 0
        elif call_index == 5:
            proc.communicate = AsyncMock(
                return_value=(b'{"createdAt":"2026-05-15T06:10:30Z"}', b"")
            )
            proc.returncode = 0
        else:
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        call_index += 1
        return proc

    return _fake_subprocess


def _extract_pr_body(captured_args: list) -> str:
    """Pull the --body argument out of the gh pr create call (5th subprocess call)."""
    gh_args = captured_args[4]
    assert gh_args[0] == "gh" and gh_args[1] == "pr" and gh_args[2] == "create"
    body_idx = gh_args.index("--body")
    return gh_args[body_idx + 1]


@pytest.mark.asyncio
async def test_create_qa_pr_substitutes_agent_notes_into_body(tmp_path: Path):
    """When INVESTIGATION_NOTES.md is present, its sections appear in the PR body."""
    worktree = tmp_path
    _write_notes(
        worktree,
        """\
## Root Cause
The `relationship_jobs` runner crashed because a config key was renamed.

## Fix Summary
Updated the runner to read the new key and added a backwards-compat shim.

## Test Coverage
Added `test_relationship_jobs_reads_renamed_key` to lock in the new behavior.
""",
    )

    captured: list = []
    fake = _capture_gh_pr_create_subprocess("https://github.com/acme/repo.git", captured)
    whitelist = _make_loaded_whitelist(["acme/repo"])

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        side_effect=fake,
    ):
        pr_url, pr_number, pr_created_at, error = await _create_qa_pr(
            repo_root=worktree,
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
            worktree_path=worktree,
        )

    assert error is None, f"unexpected error: {error}"
    assert pr_url == "https://github.com/acme/repo/pull/42"
    assert pr_number == 42
    assert pr_created_at == datetime(2026, 5, 15, 6, 10, 30, tzinfo=UTC)

    body = _extract_pr_body(captured)
    # Agent content is present
    assert "config key was renamed" in body
    assert "backwards-compat shim" in body
    assert "test_relationship_jobs_reads_renamed_key" in body
    # Placeholder is gone for the sections the agent filled in
    assert _NOTES_PLACEHOLDER not in body


@pytest.mark.asyncio
async def test_create_qa_pr_uses_placeholder_when_notes_missing(tmp_path: Path):
    """No notes file → all three sections fall back to the placeholder string."""
    worktree = tmp_path  # no notes file written

    captured: list = []
    fake = _capture_gh_pr_create_subprocess("https://github.com/acme/repo.git", captured)
    whitelist = _make_loaded_whitelist(["acme/repo"])

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        side_effect=fake,
    ):
        pr_url, _pr_number, _pr_created_at, error = await _create_qa_pr(
            repo_root=worktree,
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
            worktree_path=worktree,
        )

    assert error is None
    assert pr_url == "https://github.com/acme/repo/pull/42"
    body = _extract_pr_body(captured)
    # All three sections fall back to the placeholder
    assert body.count(_NOTES_PLACEHOLDER) == 3
    assert "### Root Cause" in body
    assert "### Fix Summary" in body
    assert "### Test Coverage" in body


@pytest.mark.asyncio
async def test_create_qa_pr_partial_notes_mixes_agent_text_and_placeholder(tmp_path: Path):
    """Sections the agent omits use the placeholder; provided sections show real content."""
    worktree = tmp_path
    _write_notes(
        worktree,
        """\
## Root Cause
agent-supplied root cause

## Fix Summary
agent-supplied fix summary
""",  # Test Coverage intentionally omitted
    )

    captured: list = []
    fake = _capture_gh_pr_create_subprocess("https://github.com/acme/repo.git", captured)
    whitelist = _make_loaded_whitelist(["acme/repo"])

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        side_effect=fake,
    ):
        await _create_qa_pr(
            repo_root=worktree,
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
            worktree_path=worktree,
        )

    body = _extract_pr_body(captured)
    assert "agent-supplied root cause" in body
    assert "agent-supplied fix summary" in body
    # Only the missing Test Coverage section uses the placeholder
    assert body.count(_NOTES_PLACEHOLDER) == 1
