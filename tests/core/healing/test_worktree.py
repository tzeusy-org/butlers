"""Tests for butlers.core.healing.worktree.

Covers:
- Branch name format (12-char fingerprint-short + epoch)
- create_healing_worktree: branch + worktree creation, return value
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

import time
import uuid
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
    def test_branch_name_uses_first_12_hex_chars(self) -> None:
        fp = _make_fingerprint("abc123def456")
        name = _branch_name("email", fp)
        parts = name.split("/")
        assert parts[0] == "self-healing"
        assert parts[1] == "email"
        epoch_part = parts[2]
        short, epoch_str = epoch_part.rsplit("-", 1)
        assert short == "abc123def456"
        assert epoch_str.isdigit()

    def test_branch_name_prefix_is_self_healing(self) -> None:
        fp = _make_fingerprint()
        name = _branch_name("mybutler", fp)
        assert name.startswith("self-healing/mybutler/")

    def test_branch_name_epoch_is_recent(self) -> None:
        before = int(time.time())
        fp = _make_fingerprint()
        name = _branch_name("b", fp)
        after = int(time.time())
        epoch = int(name.rsplit("-", 1)[1])
        assert before <= epoch <= after + 1

    def test_worktree_path_derived_from_branch(self, tmp_path: Path) -> None:
        branch = "self-healing/email/abc123def456-1710700000"
        wt = _worktree_path(tmp_path, branch)
        expected = tmp_path / ".healing-worktrees" / "self-healing" / "email" / "abc123def456-1710700000"
        assert wt == expected


# ---------------------------------------------------------------------------
# create_healing_worktree
# ---------------------------------------------------------------------------


class TestCreateHealingWorktree:
    async def test_returns_path_and_branch(self, tmp_path: Path) -> None:
        """create_healing_worktree returns (worktree_path, branch_name)."""
        fp = _make_fingerprint("abc123def456")

        # Simulate successful git branch and git worktree add
        async def mock_run_git(*args, cwd, capture_stderr=True):
            return 0, "", ""

        with patch(
            "butlers.core.healing.worktree._run_git",
            side_effect=mock_run_git,
        ):
            wt_path, branch = await create_healing_worktree(tmp_path, "email", fp)

        assert branch.startswith("self-healing/email/abc123def456-")
        assert wt_path == _worktree_path(tmp_path, branch)

    async def test_branch_collision_raises(self, tmp_path: Path) -> None:
        """Branch creation failure raises WorktreeCreationError before worktree add."""
        fp = _make_fingerprint()

        async def mock_run_git(*args, cwd, capture_stderr=True):
            # Simulate branch already exists
            return 1, "", "fatal: A branch named 'self-healing/email/...' already exists."

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            with pytest.raises(WorktreeCreationError) as exc_info:
                await create_healing_worktree(tmp_path, "email", fp)

        assert "already exists" in exc_info.value.git_output

    async def test_worktree_add_failure_cleans_branch(self, tmp_path: Path) -> None:
        """When git worktree add fails, the orphaned branch is deleted."""
        fp = _make_fingerprint("deadbeef0000")
        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            if args[0] == "branch":
                return 0, "", ""  # branch created OK
            if args[0] == "worktree" and args[1] == "add":
                return 1, "", "error: worktree add failed"
            if args[0] == "branch" and args[1] == "-D":
                return 0, "", ""  # branch deletion
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            with pytest.raises(WorktreeCreationError) as exc_info:
                await create_healing_worktree(tmp_path, "email", fp)

        # Verify branch -D was called
        delete_calls = [c for c in calls if c[0] == "branch" and c[1] == "-D"]
        assert len(delete_calls) == 1
        assert "worktree add failed" in exc_info.value.git_output

    async def test_git_lock_raises_worktree_creation_error(self, tmp_path: Path) -> None:
        """Git lock file causes WorktreeCreationError (no retry)."""
        fp = _make_fingerprint()

        call_count = 0

        async def mock_run_git(*args, cwd, capture_stderr=True):
            nonlocal call_count
            call_count += 1
            if args[0] == "branch" and call_count == 1:
                return 0, "", ""  # branch OK
            if args[0] == "worktree":
                return 1, "", "fatal: Unable to create '.git/worktrees/...': File exists"
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            with pytest.raises(WorktreeCreationError):
                await create_healing_worktree(tmp_path, "email", fp)

    async def test_parent_directory_created(self, tmp_path: Path) -> None:
        """Parent directory for worktree is created if it doesn't exist."""
        fp = _make_fingerprint()

        captured_paths: list[str] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            if args[0] == "worktree" and args[1] == "add":
                captured_paths.append(args[2])
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            wt_path, _ = await create_healing_worktree(tmp_path, "calendar", fp)

        assert wt_path.parent.exists()


# ---------------------------------------------------------------------------
# remove_healing_worktree
# ---------------------------------------------------------------------------


class TestRemoveHealingWorktree:
    async def test_removes_worktree_and_branch(self, tmp_path: Path) -> None:
        """remove_healing_worktree calls worktree remove and branch -D."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await remove_healing_worktree(tmp_path, branch, delete_branch=True)

        git_commands = [c[0] for c in calls]
        assert "worktree" in git_commands
        assert "branch" in git_commands

    async def test_no_branch_deletion_when_flag_false(self, tmp_path: Path) -> None:
        """When delete_branch=False, branch -D is not called."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await remove_healing_worktree(tmp_path, branch, delete_branch=False)

        branch_delete_calls = [c for c in calls if c[0] == "branch" and c[1] == "-D"]
        assert len(branch_delete_calls) == 0

    async def test_force_remove_on_dirty_worktree(self, tmp_path: Path) -> None:
        """Falls back to --force when initial worktree remove fails."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            if args[0] == "worktree" and args[1] == "remove" and "--force" not in args:
                return 1, "", "error: worktree has uncommitted changes"
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await remove_healing_worktree(tmp_path, branch)

        force_calls = [c for c in calls if "--force" in c]
        assert len(force_calls) >= 1

    async def test_nonexistent_worktree_triggers_prune(self, tmp_path: Path) -> None:
        """When worktree dir doesn't exist, prune is called instead."""
        branch = "self-healing/email/abc123def456-1710700000"
        # Do NOT create the worktree directory

        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await remove_healing_worktree(tmp_path, branch, delete_branch=False)

        prune_calls = [c for c in calls if c[0] == "worktree" and "prune" in c]
        assert len(prune_calls) >= 1

    async def test_delete_remote_branch(self, tmp_path: Path) -> None:
        """When delete_remote=True, git push origin --delete is called."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            calls.append(args)
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await remove_healing_worktree(tmp_path, branch, delete_remote=True)

        remote_delete_calls = [c for c in calls if "push" in c and "--delete" in c]
        assert len(remote_delete_calls) == 1

    async def test_cleanup_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """Worktree remove failure is logged as WARNING and does not raise."""
        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        async def mock_run_git(*args, cwd, capture_stderr=True):
            return 1, "", "fatal: some git error"

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            # Must not raise
            await remove_healing_worktree(tmp_path, branch)


# ---------------------------------------------------------------------------
# reap_stale_worktrees
# ---------------------------------------------------------------------------


class TestReapStaleWorktrees:
    def _make_pool(
        self,
        attempts: dict,  # branch_name -> {"status": ..., "closed_at": ...}
    ) -> MagicMock:
        """Build a mock asyncpg Pool that answers healing_attempts queries."""
        pool = MagicMock()
        # Patch _healing_attempts_for_branches and _list_healing_branches
        return pool

    async def test_terminal_aged_worktree_is_reaped(self, tmp_path: Path) -> None:
        """Worktree for a terminal attempt older than 24h is removed."""
        from datetime import UTC, datetime, timedelta

        branch = "self-healing/email/abc123def456-1710600000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        old_time = datetime.now(UTC) - timedelta(hours=36)

        mock_pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            # Return the attempt row when queried for branch_names
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [
                    {
                        "branch_name": branch,
                        "status": "failed",
                        "closed_at": old_time,
                        "updated_at": old_time,
                        "healing_session_id": None,
                    }
                ]
            return []

        mock_pool.fetch = AsyncMock(side_effect=mock_fetch)

        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove),
        ):
            count = await reap_stale_worktrees(tmp_path, mock_pool)

        assert count == 1
        assert branch in remove_calls

    async def test_active_worktree_preserved(self, tmp_path: Path) -> None:
        """Worktree for an active (investigating) attempt is NOT reaped."""
        from datetime import UTC, datetime

        branch = "self-healing/email/abc123def456-1710700000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        recent = datetime.now(UTC)

        mock_pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [
                    {
                        "branch_name": branch,
                        "status": "investigating",
                        "closed_at": None,
                        "updated_at": recent,
                        "healing_session_id": str(uuid.uuid4()),
                    }
                ]
            return []

        mock_pool.fetch = AsyncMock(side_effect=mock_fetch)

        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove),
        ):
            count = await reap_stale_worktrees(tmp_path, mock_pool)

        assert count == 0
        assert branch not in remove_calls

    async def test_orphaned_worktree_is_reaped_with_warning(
        self, tmp_path: Path, caplog
    ) -> None:
        """Orphaned worktree (no DB row) is reaped with a WARNING log."""
        import logging

        branch = "self-healing/email/abc123def456-1710500000"
        wt_dir = _worktree_path(tmp_path, branch)
        wt_dir.mkdir(parents=True)

        mock_pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            # Return no rows — orphaned
            return []

        mock_pool.fetch = AsyncMock(side_effect=mock_fetch)

        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove),
            caplog.at_level(logging.WARNING, logger="butlers.core.healing.worktree"),
        ):
            count = await reap_stale_worktrees(tmp_path, mock_pool)

        assert count == 1
        assert branch in remove_calls
        assert any("orphaned" in r.message.lower() for r in caplog.records)

    async def test_orphaned_branch_no_worktree_deleted(self, tmp_path: Path) -> None:
        """Orphaned self-healing/* branches with no worktree and no active attempt are deleted."""
        branch = "self-healing/calendar/orphan000000-1710400000"
        # Do NOT create a worktree directory

        mock_pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                # Branch has a terminal attempt (not active)
                return [
                    {
                        "branch_name": branch,
                        "status": "failed",
                        "closed_at": None,
                        "updated_at": None,
                        "healing_session_id": None,
                    }
                ]
            return []

        mock_pool.fetch = AsyncMock(side_effect=mock_fetch)

        # worktree_base is empty (no subdirs)
        git_calls: list[tuple] = []

        async def mock_run_git(*args, cwd, capture_stderr=True):
            git_calls.append(args)
            if args[0] == "branch" and args[1] == "--list":
                return 0, branch, ""
            if args[0] == "worktree" and args[1] == "list":
                # No worktrees
                return 0, "", ""
            return 0, "", ""

        with patch("butlers.core.healing.worktree._run_git", side_effect=mock_run_git):
            await reap_stale_worktrees(tmp_path, mock_pool)

        branch_delete_calls = [c for c in git_calls if c[0] == "branch" and c[1] == "-D"]
        assert len(branch_delete_calls) >= 1

    async def test_returns_count(self, tmp_path: Path) -> None:
        """reap_stale_worktrees returns the total count of reaped items."""
        from datetime import UTC, datetime, timedelta

        old_time = datetime.now(UTC) - timedelta(hours=48)

        # Create 2 terminal worktrees
        branches = [
            "self-healing/email/aaa000000000-1710600000",
            "self-healing/calendar/bbb111111111-1710600001",
        ]
        for b in branches:
            wt_dir = _worktree_path(tmp_path, b)
            wt_dir.mkdir(parents=True)

        mock_pool = MagicMock()

        async def mock_fetch(*args, **kwargs):
            sql = args[0]
            if "branch_name = ANY" in sql:
                return [
                    {
                        "branch_name": b,
                        "status": "failed",
                        "closed_at": old_time,
                        "updated_at": old_time,
                        "healing_session_id": None,
                    }
                    for b in branches
                ]
            return []

        mock_pool.fetch = AsyncMock(side_effect=mock_fetch)

        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch("butlers.core.healing.worktree._list_healing_branches", return_value=[]),
            patch("butlers.core.healing.worktree.remove_healing_worktree", side_effect=mock_remove),
        ):
            count = await reap_stale_worktrees(tmp_path, mock_pool)

        assert count == 2


# ---------------------------------------------------------------------------
# .gitignore check
# ---------------------------------------------------------------------------


class TestGitignore:
    def test_healing_worktrees_in_gitignore(self) -> None:
        """Verify .healing-worktrees/ is listed in the repository's .gitignore."""
        # Walk up from this test file to find the repo root
        here = Path(__file__).resolve()
        root = here
        while root != root.parent:
            if (root / ".git").exists():
                break
            root = root.parent

        gitignore = root / ".gitignore"
        assert gitignore.exists(), f".gitignore not found at {gitignore}"
        content = gitignore.read_text()
        assert ".healing-worktrees/" in content, (
            ".healing-worktrees/ not found in .gitignore"
        )
