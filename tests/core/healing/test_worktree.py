"""Tests for butlers.core.healing.worktree.

Covers:
- Branch name format (12-char fingerprint-short + epoch)
- Branch name format with custom prefix (QA investigations)
- create_healing_worktree: branch + worktree creation, return value
- create_healing_worktree: custom prefix parameter (QA path)
- create_healing_worktree: branch collision raises WorktreeCreationError
- create_healing_worktree: worktree add failure cleans up orphaned branch
- remove_healing_worktree: worktree removal, branch deletion, remote deletion
- remove_healing_worktree: force-remove for dirty worktrees
- remove_healing_worktree: no-op when worktree already gone (prune + log)
- reap_stale_worktrees: terminal+aged → reaped
- reap_stale_worktrees: active attempt preserved
- reap_stale_worktrees: orphaned worktree (no DB row) → reaped with WARNING
- reap_stale_worktrees: orphaned branch with no worktree → deleted
- .healing-worktrees in .gitignore
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.healing.worktree import (
    WorktreeCreationError,
    _branch_name,
    _worktree_path,
    create_healing_worktree,
    reap_stale_worktrees,
    remove_healing_worktree,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fingerprint(prefix: str = "a" * 12) -> str:
    """Return a 64-char hex fingerprint with the given 12-char prefix."""
    return (prefix + "0" * (64 - len(prefix)))[:64]


# ---------------------------------------------------------------------------
# Branch name format
# ---------------------------------------------------------------------------


class TestBranchNameFormat:
    def test_branch_name_format(self) -> None:
        """Branch name structure, path derivation, and prefix variants all correct."""
        before = int(time.time())
        fp = _make_fingerprint("abc123def456")
        name = _branch_name("email", fp)
        after = int(time.time())

        parts = name.split("/")
        assert parts[0] == "self-healing"
        assert parts[1] == "email"
        short, epoch_str = parts[2].rsplit("-", 1)
        assert short == "abc123def456"
        assert epoch_str.isdigit()
        assert before <= int(epoch_str) <= after + 1

        # Path derived from branch
        branch = "self-healing/email/abc123def456-1710700000"
        wt = _worktree_path(tmp_path := Path("/tmp/wt"), branch)
        expected = (
            tmp_path / ".healing-worktrees" / "self-healing" / "email" / "abc123def456-1710700000"
        )
        assert wt == expected

        # Custom prefix replaces self-healing; structure preserved
        qa_name = _branch_name("email", fp, prefix="qa")
        assert qa_name.startswith("qa/email/abc123def456-")

        fp2 = _make_fingerprint("deadbeef0000")
        parts2 = _branch_name("travel", fp2, prefix="qa").split("/")
        assert parts2[0] == "qa" and parts2[1] == "travel"
        short2, epoch_str2 = parts2[2].rsplit("-", 1)
        assert short2 == "deadbeef0000" and epoch_str2.isdigit()

        assert _branch_name("general", fp, prefix="custom-prefix").startswith("custom-prefix/general/")


# ---------------------------------------------------------------------------
# create_healing_worktree
# ---------------------------------------------------------------------------


class TestCreateHealingWorktree:
    async def test_success_path_and_prefix_variants(self, tmp_path: Path) -> None:
        """create_healing_worktree returns (path, branch); parent dir created; QA prefix works."""
        fp = _make_fingerprint("abc123def456")
        calls: list[tuple[str, ...]] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            wt_path, branch = await create_healing_worktree(tmp_path, "email", fp)
        assert branch.startswith("self-healing/email/abc123def456-")
        assert wt_path == _worktree_path(tmp_path, branch)
        assert wt_path.parent.exists()

        # QA prefix
        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            qa_path, qa_branch = await create_healing_worktree(tmp_path, "email", fp, prefix="qa")
            sh_path, sh_branch = await create_healing_worktree(tmp_path, "email", fp)
        assert qa_branch.startswith("qa/email/abc123def456-")
        assert qa_path == _worktree_path(tmp_path, qa_branch)
        assert ".healing-worktrees/qa/" in str(qa_path)
        assert sh_branch.startswith("self-healing/email/")
        assert ".healing-worktrees/self-healing/" in str(sh_path)
        assert qa_path.parent.exists()

        # Custom base ref
        calls.clear()
        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await create_healing_worktree(
                tmp_path, "email", fp, prefix="qa", base_ref="origin/main"
            )
        assert calls[0][0] == "branch"
        assert calls[0][2] == "origin/main"

    async def test_failure_cases(self, tmp_path: Path) -> None:
        """Branch collision, worktree add failure (cleanup), and git lock all raise WorktreeCreationError."""
        fp = _make_fingerprint()

        # Branch collision
        async def branch_collision(*args, cwd, capture_stderr=True):
            return 1, "", "fatal: A branch named 'self-healing/email/...' already exists."

        with patch("butlers.core.healing.worktree._run_git", side_effect=branch_collision):
            with pytest.raises(WorktreeCreationError) as exc_info:
                await create_healing_worktree(tmp_path, "email", fp)
        assert "already exists" in exc_info.value.git_output

        # Worktree add failure cleans up orphaned branch
        fp2 = _make_fingerprint("deadbeef0000")
        calls: list[tuple] = []

        async def add_failure(*args, cwd, capture_stderr=True):
            calls.append(args)
            if args[0] == "branch" and len(args) == 3:
                return 0, "", ""  # branch created OK
            if args[0] == "worktree" and args[1] == "add":
                return 1, "", "error: worktree add failed"
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=add_failure):
            with pytest.raises(WorktreeCreationError) as exc_info2:
                await create_healing_worktree(tmp_path, "email", fp2)
        assert len([c for c in calls if c[0] == "branch" and c[1] == "-D"]) == 1
        assert "worktree add failed" in exc_info2.value.git_output

        # Git lock raises WorktreeCreationError
        call_count = 0

        async def git_lock(*args, cwd, capture_stderr=True):
            nonlocal call_count
            call_count += 1
            if args[0] == "branch" and call_count == 1:
                return 0, "", ""
            if args[0] == "worktree":
                return 1, "", "fatal: Unable to create '.git/worktrees/...': File exists"
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=git_lock):
            with pytest.raises(WorktreeCreationError):
                await create_healing_worktree(tmp_path, "email", fp)


# ---------------------------------------------------------------------------
# remove_healing_worktree
# ---------------------------------------------------------------------------


class TestRemoveHealingWorktree:
    async def test_remove_branch_force_and_no_branch(self, tmp_path: Path) -> None:
        """Normal remove with branch -D; force on dirty; no branch deletion when flag False."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        # Normal remove + branch deletion
        calls: list[tuple] = []

        async def mock_git_ok(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_git_ok):
            await remove_healing_worktree(tmp_path, branch, delete_branch=True)
        assert "worktree" in [c[0] for c in calls]
        assert "branch" in [c[0] for c in calls]

        # No branch deletion
        calls2: list[tuple] = []

        async def mock_git_ok2(*args, cwd, capture_stderr=True):
            calls2.append(args)
            return 0, "", ""

        wt_dir.mkdir(parents=True, exist_ok=True)
        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_git_ok2):
            await remove_healing_worktree(tmp_path, branch, delete_branch=False)
        assert not any(c for c in calls2 if c[0] == "branch" and c[1] == "-D")

        # Force remove on dirty worktree
        calls3: list[tuple] = []

        async def dirty_git(*args, cwd, capture_stderr=True):
            calls3.append(args)
            if args[0] == "worktree" and args[1] == "remove" and "--force" not in args:
                return 1, "", "error: worktree has uncommitted changes"
            return 0, "", ""

        wt_dir.mkdir(parents=True, exist_ok=True)
        with patch("butlers.core.healing.worktree._run_git", side_effect=dirty_git):
            await remove_healing_worktree(tmp_path, branch)
        assert any("--force" in c for c in calls3)

    async def test_nonexistent_remote_and_failure(self, tmp_path: Path) -> None:
        """Nonexistent worktree triggers prune; remote delete works; failure is non-fatal."""
        branch = "self-healing/email/abc123def456-1710700000"
        # Do NOT create the worktree directory

        # Nonexistent worktree → prune
        calls: list[tuple] = []

        async def mock_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_git):
            await remove_healing_worktree(tmp_path, branch, delete_branch=False)
        assert any(c[0] == "worktree" and "prune" in c for c in calls)

        # Remote delete
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)
        calls2: list[tuple] = []

        async def mock_git2(*args, cwd, capture_stderr=True):
            calls2.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_git2):
            await remove_healing_worktree(tmp_path, branch, delete_remote=True)
        assert any("push" in c and "--delete" in c for c in calls2)

        # Failure is non-fatal
        wt_dir.mkdir(parents=True, exist_ok=True)

        async def always_fail(*args, cwd, capture_stderr=True):
            return 1, "", "fatal: some git error"

        with patch("butlers.core.healing.worktree._run_git", side_effect=always_fail):
            await remove_healing_worktree(tmp_path, branch)  # must not raise


# ---------------------------------------------------------------------------
# reap_stale_worktrees
# ---------------------------------------------------------------------------


class TestReapStaleWorktrees:
    def _make_pool(self, rows: list[dict]) -> MagicMock:
        pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return rows
            return []

        pool.fetch = AsyncMock(side_effect=mock_fetch)
        return pool

    async def test_reap_stale_worktrees(self, tmp_path: Path, caplog) -> None:
        """Terminal+aged → reaped; active → preserved; orphaned worktree (no DB row) → WARNING; orphaned branch (no wt) deleted; mixed prefixes all reaped; custom prefix filter respected."""
        old = datetime.now(UTC) - timedelta(hours=36)
        recent = datetime.now(UTC)

        # Terminal → reaped; active → preserved; orphaned worktree → reaped with WARNING
        terminal_branch = "self-healing/email/abc123def456-1710600000"
        active_branch = "self-healing/email/def456abc123-1710700000"
        orphaned_branch = "self-healing/email/orphan00000-1710500000"
        for b in (terminal_branch, active_branch, orphaned_branch):
            _worktree_path(tmp_path, b).mkdir(parents=True)

        pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [
                    {"branch_name": terminal_branch, "status": "failed", "closed_at": old, "updated_at": old, "healing_session_id": None},
                    {"branch_name": active_branch, "status": "investigating", "closed_at": None, "updated_at": recent, "healing_session_id": str(uuid.uuid4())},
                    # orphaned_branch has no row → orphaned
                ]
            return []

        pool.fetch = AsyncMock(side_effect=mock_fetch)
        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove),
            caplog.at_level(logging.WARNING, logger="butlers.core.healing.worktree"),
        ):
            count = await reap_stale_worktrees(tmp_path, pool)
        assert terminal_branch in remove_calls and active_branch not in remove_calls
        assert orphaned_branch in remove_calls and count == 2
        assert any("orphaned" in r.message.lower() for r in caplog.records)

        # Orphaned branch (no worktree) → git branch -D called
        orphan_tmp = tmp_path / "orphan_test"
        orphan_tmp.mkdir()
        orphan_branch = "self-healing/calendar/orphan000000-1710400000"
        pool_orphan = MagicMock()

        async def mock_fetch_orphan(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [{"branch_name": orphan_branch, "status": "failed", "closed_at": None, "updated_at": None, "healing_session_id": None}]
            return []

        pool_orphan.fetch = AsyncMock(side_effect=mock_fetch_orphan)
        git_calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            git_calls.append(args)
            if args[0] == "branch" and args[1] == "--list":
                pattern = args[2] if len(args) > 2 else ""
                return (0, orphan_branch, "") if pattern.startswith("self-healing") else (0, "", "")
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await reap_stale_worktrees(orphan_tmp, pool_orphan)
        assert any(c[0] == "branch" and c[1] == "-D" for c in git_calls)

        # Mixed prefixes (self-healing + qa) → all terminal+aged reaped; custom prefix filters
        old2 = datetime.now(UTC) - timedelta(hours=48)
        mixed_tmp = tmp_path / "mixed_test"
        mixed_tmp.mkdir()
        branches = [
            "self-healing/email/aaa000000000-1710600000",
            "self-healing/calendar/bbb111111111-1710600001",
            "qa/email/ccc222222222-1710600002",
        ]
        for b in branches:
            _worktree_path(mixed_tmp, b).mkdir(parents=True)
        pool_mixed = MagicMock()

        async def mock_fetch_mixed(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [{"branch_name": b, "status": "failed", "closed_at": old2, "updated_at": old2, "healing_session_id": None} for b in branches]
            return []

        pool_mixed.fetch = AsyncMock(side_effect=mock_fetch_mixed)
        remove_mixed: list[str] = []

        async def mock_remove_mixed(repo_root, branch_name, **kwargs):
            remove_mixed.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove_mixed),
        ):
            count_mixed = await reap_stale_worktrees(mixed_tmp, pool_mixed)
        assert count_mixed == 3 and all(b in remove_mixed for b in branches)

        # Custom prefix: only qa reaped
        qa_tmp = tmp_path / "qa_test"
        qa_tmp.mkdir()
        qa_branch = "qa/email/bbb111111111-1710600001"
        _worktree_path(qa_tmp, qa_branch).mkdir(parents=True)
        pool_qa = MagicMock()

        async def mock_fetch_qa(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [{"branch_name": qa_branch, "status": "failed", "closed_at": old2, "updated_at": old2, "healing_session_id": None}]
            return []

        pool_qa.fetch = AsyncMock(side_effect=mock_fetch_qa)
        remove_qa: list[str] = []

        async def mock_remove_qa(repo_root, branch_name, **kwargs):
            remove_qa.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove_qa),
        ):
            count_qa = await reap_stale_worktrees(qa_tmp, pool_qa, prefixes=("qa",))
        assert count_qa == 1 and qa_branch in remove_qa


# ---------------------------------------------------------------------------
# .gitignore check
# ---------------------------------------------------------------------------


class TestGitignore:
    def test_healing_worktrees_in_gitignore(self) -> None:
        """Verify .healing-worktrees/ is listed in the repository's .gitignore."""
        here = Path(__file__).resolve()
        root = here
        while root != root.parent:
            if (root / ".git").exists():
                break
            root = root.parent

        gitignore = root / ".gitignore"
        assert gitignore.exists(), f".gitignore not found at {gitignore}"
        content = gitignore.read_text()
        assert ".healing-worktrees/" in content, ".healing-worktrees/ not found in .gitignore"
