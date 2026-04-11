"""Tests for repository whitelist enforcement in QA dispatch.

Covers:
- _create_qa_pr: whitelist blocks PR when repo not in list
- _create_qa_pr: empty whitelist blocks all (fail-closed)
- _create_qa_pr: no whitelist param → fail-closed (new RepoWhitelist with no pool)
- _create_qa_pr: allowed repo proceeds to git push
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import _create_qa_pr
from butlers.core.qa.models import QaFinding
from butlers.core.qa.repo_whitelist import RepoWhitelist

pytestmark = pytest.mark.unit


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
    """Return a pre-loaded whitelist with the given ``owner/repo`` entries."""
    wl = RepoWhitelist(db_pool=None)
    wl._allowed = frozenset(repos)
    wl._loaded = True
    wl._last_loaded_at = time.monotonic()
    return wl


def _mock_git_remote(remote_url: str):
    """Patch asyncio.create_subprocess_exec to return ``remote_url`` for git remote get-url."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(remote_url.encode(), b""))
    mock_proc.returncode = 0
    return mock_proc


@pytest.mark.asyncio
async def test_create_qa_pr_no_gh_token():
    """Returns 'no_gh_token' immediately when no token is provided."""
    pr_url, pr_number, error = await _create_qa_pr(
        repo_root=Path("/tmp/repo"),
        branch_name="qa/test-branch",
        finding=_make_finding(),
        attempt_id=uuid.uuid4(),
        labels=[],
        gh_token=None,
    )
    assert pr_url is None
    assert pr_number is None
    assert error == "no_gh_token"


@pytest.mark.asyncio
async def test_create_qa_pr_whitelist_empty_blocks_all():
    """An empty whitelist blocks PR creation for ALL repos (fail-closed)."""
    empty_whitelist = _make_loaded_whitelist([])

    mock_proc = _mock_git_remote("https://github.com/acme/repo.git")
    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=empty_whitelist,
        )

    assert pr_url is None
    assert pr_number is None
    assert error is not None
    assert error.startswith("repo_not_whitelisted")
    assert "whitelist_empty" in error


@pytest.mark.asyncio
async def test_create_qa_pr_repo_not_in_whitelist():
    """A repo not in the whitelist is blocked."""
    whitelist = _make_loaded_whitelist(["other/repo"])

    mock_proc = _mock_git_remote("https://github.com/acme/repo.git")
    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
        )

    assert error is not None
    assert error.startswith("repo_not_whitelisted")
    assert "not_in_whitelist" in error
    assert "acme/repo" in error


@pytest.mark.asyncio
async def test_create_qa_pr_no_whitelist_param_blocks_all():
    """When whitelist=None is passed, a no-pool RepoWhitelist is used → fail-closed."""
    # We need the git remote call to succeed so we can reach the whitelist check.
    # git remote get-url → returns a URL, then whitelist check happens.
    git_proc = _mock_git_remote("https://github.com/acme/repo.git")

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        return_value=git_proc,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=None,  # explicit None → new RepoWhitelist(db_pool=None)
        )

    assert error is not None
    assert error.startswith("repo_not_whitelisted")


@pytest.mark.asyncio
async def test_create_qa_pr_git_remote_fails_blocks():
    """When git remote get-url fails, PR creation is blocked (fail-closed)."""
    whitelist = _make_loaded_whitelist(["acme/repo"])

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repo"))
    mock_proc.returncode = 128  # git error

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
        )

    assert error == "repo_not_whitelisted:remote_unavailable"


@pytest.mark.asyncio
async def test_create_qa_pr_allowed_repo_proceeds_to_push():
    """An allowed repo passes the whitelist check and reaches HTTPS git push."""
    whitelist = _make_loaded_whitelist(["acme/repo"])

    call_index = 0
    call_args_list = []

    async def _fake_subprocess(*args, **kwargs):
        nonlocal call_index
        call_args_list.append(args)
        proc = MagicMock()
        if call_index == 0:
            # First call: git remote get-url
            proc.communicate = AsyncMock(return_value=(b"https://github.com/acme/repo.git", b""))
            proc.returncode = 0
        elif call_index == 1:
            # Second call: git log (no-op detection) — return a commit line so push is attempted
            proc.communicate = AsyncMock(return_value=(b"abc1234 fix: something\n", b""))
            proc.returncode = 0
        elif call_index == 2:
            # Third call: gh auth setup-git
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        elif call_index == 3:
            # Fourth call: git push → fail so we don't continue to gh pr create
            proc.communicate = AsyncMock(return_value=(b"", b"push failed"))
            proc.returncode = 1
        else:
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        call_index += 1
        return proc

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        side_effect=_fake_subprocess,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
        )

    # The whitelist passed; we got to git push which failed.
    assert error is not None
    assert "git push failed" in error
    assert call_args_list[2] == ("gh", "auth", "setup-git")
    assert call_args_list[3] == (
        "git",
        "push",
        "https://github.com/acme/repo.git",
        "qa/test-branch",
    )


@pytest.mark.asyncio
async def test_create_qa_pr_ssh_url_allowed():
    """SSH remotes are parsed, but pushes still use HTTPS token auth."""
    whitelist = _make_loaded_whitelist(["acme/repo"])

    call_index = 0
    call_args_list = []

    async def _fake_subprocess(*args, **kwargs):
        nonlocal call_index
        call_args_list.append(args)
        proc = MagicMock()
        if call_index == 0:
            # git remote get-url → SSH URL
            proc.communicate = AsyncMock(return_value=(b"git@github.com:acme/repo.git", b""))
            proc.returncode = 0
        elif call_index == 1:
            # git log (no-op detection) — return a commit line so push is attempted
            proc.communicate = AsyncMock(return_value=(b"abc1234 fix: something\n", b""))
            proc.returncode = 0
        elif call_index == 2:
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
        else:
            # git push → fail
            proc.communicate = AsyncMock(return_value=(b"", b"push error"))
            proc.returncode = 1
        call_index += 1
        return proc

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        side_effect=_fake_subprocess,
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=Path("/tmp/repo"),
            branch_name="qa/test-branch",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=whitelist,
        )

    # Should have passed the whitelist (SSH parsed correctly) and reached push.
    assert error is not None and "git push failed" in error
    assert call_args_list[3] == (
        "git",
        "push",
        "https://github.com/acme/repo.git",
        "qa/test-branch",
    )
