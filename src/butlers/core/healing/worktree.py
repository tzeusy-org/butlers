"""Git worktree lifecycle management for healing agents.

Each healing attempt runs in an isolated git worktree branched from ``main``,
so the healing agent cannot corrupt the daemon's working tree.  The module
provides three public functions:

- ``create_healing_worktree`` — branch + worktree creation with cleanup on failure
- ``remove_healing_worktree`` — worktree removal with optional branch/remote deletion
- ``reap_stale_worktrees`` — startup reaper for orphaned/terminal worktrees

Worktree layout
---------------
Each worktree lives at::

    <repo_root>/.healing-worktrees/hotfix/<butler_name>/<fingerprint_short>-<epoch>/

The branch name matches the relative path under ``.healing-worktrees/``:
``hotfix/<butler_name>/<fingerprint_short>-<epoch>``

``<fingerprint_short>`` is the first 12 hex characters of the SHA-256 fingerprint.

Spec reference
--------------
openspec/changes/butler-self-healing/specs/healing-worktree/spec.md
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Directory name under repo root that holds all healing worktrees.
_WORKTREE_BASE = ".healing-worktrees"

#: Branch prefix for all healing branches.
_BRANCH_PREFIX = "hotfix"

#: Number of hex characters from fingerprint to include in the branch name.
_FINGERPRINT_SHORT_LEN = 12

#: Minimum age (hours) for a terminal healing attempt's worktree before the
#: reaper removes it.
_REAP_TERMINAL_AFTER_HOURS = 24

#: Terminal statuses for healing attempts — worktrees for these are eligible
#: for reaping after the age threshold.
_TERMINAL_STATUSES = frozenset(
    {"pr_open", "pr_merged", "failed", "unfixable", "timeout", "anonymization_failed"}
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorktreeCreationError(Exception):
    """Raised when worktree or branch creation fails.

    The ``git_output`` attribute contains the captured stderr from the failed
    git command, or a descriptive message when no git output is available.
    """

    def __init__(self, message: str, git_output: str = "") -> None:
        super().__init__(message)
        self.git_output = git_output


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _branch_name(butler_name: str, fingerprint: str) -> str:
    """Compute the branch name for a healing attempt."""
    short = fingerprint[:_FINGERPRINT_SHORT_LEN]
    epoch = int(time.time())
    return f"{_BRANCH_PREFIX}/{butler_name}/{short}-{epoch}"


def _worktree_path(repo_root: Path, branch_name: str) -> Path:
    """Compute the worktree path from branch name."""
    return repo_root / _WORKTREE_BASE / branch_name


async def _run_git(
    *args: str,
    cwd: Path,
    capture_stderr: bool = True,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if capture_stderr else ""
    return proc.returncode or 0, stdout, stderr


async def _list_worktree_branches(repo_root: Path) -> list[str]:
    """Return list of branches that have an active worktree (from git worktree list)."""
    rc, stdout, _ = await _run_git(
        "worktree", "list", "--porcelain",
        cwd=repo_root,
    )
    if rc != 0:
        return []
    branches: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("branch "):
            # "branch refs/heads/hotfix/..."
            ref = line[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                branches.append(ref[len("refs/heads/"):])
    return branches


async def _list_hotfix_branches(repo_root: Path) -> list[str]:
    """Return list of all local branches matching hotfix/*/*."""
    rc, stdout, _ = await _run_git(
        "branch", "--list", f"{_BRANCH_PREFIX}/*/*",
        cwd=repo_root,
    )
    if rc != 0:
        return []
    branches: list[str] = []
    for line in stdout.splitlines():
        b = line.strip().lstrip("* ").strip()
        if b:
            branches.append(b)
    return branches


async def _healing_attempts_for_branches(
    pool: asyncpg.Pool,
    branch_names: list[str],
) -> dict[str, dict]:
    """Return a mapping of branch_name -> healing_attempt row for the given branches.

    Queries ``shared.healing_attempts`` for all rows whose ``branch_name`` is
    in the provided list.
    """
    if not branch_names:
        return {}
    rows = await pool.fetch(
        """
        SELECT branch_name, status, closed_at, updated_at, healing_session_id
        FROM shared.healing_attempts
        WHERE branch_name = ANY($1::text[])
        """,
        branch_names,
    )
    return {row["branch_name"]: dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_healing_worktree(
    repo_root: Path,
    butler_name: str,
    fingerprint: str,
) -> tuple[Path, str]:
    """Create a git branch and worktree for a healing attempt.

    Branches from the current ``main`` HEAD.  The branch and worktree path
    are both derived from *butler_name* and *fingerprint*.

    Parameters
    ----------
    repo_root:
        Absolute path to the root of the git repository (the daemon's working
        tree, NOT an existing worktree).
    butler_name:
        Name of the butler whose session failed (e.g. ``"email"``).
    fingerprint:
        64-character SHA-256 hex fingerprint for the error being investigated.

    Returns
    -------
    tuple[Path, str]
        ``(worktree_path, branch_name)`` where *worktree_path* is the absolute
        path to the new worktree directory and *branch_name* is the full branch
        reference (e.g. ``"hotfix/email/abc123def456-1710700000"``).

    Raises
    ------
    WorktreeCreationError
        If branch creation fails (e.g. collision) or ``git worktree add`` fails
        (e.g. disk full, permissions, git lock).  Partial state is cleaned up
        before the exception propagates.
    """
    branch = _branch_name(butler_name, fingerprint)
    wt_path = _worktree_path(repo_root, branch)

    # Ensure parent directory exists
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Create branch from main HEAD
    rc, _, stderr = await _run_git(
        "branch", branch, "main",
        cwd=repo_root,
    )
    if rc != 0:
        raise WorktreeCreationError(
            f"Failed to create branch {branch!r}: {stderr}",
            git_output=stderr,
        )

    # Step 2: Create worktree at the computed path
    rc, _, stderr = await _run_git(
        "worktree", "add", str(wt_path), branch,
        cwd=repo_root,
    )
    if rc != 0:
        # Clean up the orphaned branch we just created
        delete_rc, _, delete_stderr = await _run_git(
            "branch", "-D", branch,
            cwd=repo_root,
        )
        if delete_rc != 0:
            logger.warning(
                "Failed to delete orphaned branch %r after worktree creation failure: %s",
                branch,
                delete_stderr,
            )
        raise WorktreeCreationError(
            f"git worktree add failed for branch {branch!r}: {stderr}",
            git_output=stderr,
        )

    logger.info(
        "Created healing worktree: path=%s branch=%s",
        wt_path,
        branch,
    )
    return wt_path, branch


async def remove_healing_worktree(
    repo_root: Path,
    branch_name: str,
    delete_branch: bool = True,
    delete_remote: bool = False,
) -> None:
    """Remove a healing worktree and optionally delete associated branches.

    Best-effort: errors are logged at WARNING level and do not propagate.
    This is intentional — worktree removal is a cleanup operation and must
    not break the healing attempt status machine.

    Parameters
    ----------
    repo_root:
        Absolute path to the repository root.
    branch_name:
        The branch name (e.g. ``"hotfix/email/abc123def456-1710700000"``).
        Used to derive the worktree path and to delete the branch.
    delete_branch:
        When ``True`` (default), delete the local branch after removing the
        worktree.  Set to ``False`` when the branch backs an open PR.
    delete_remote:
        When ``True``, also delete the remote branch via
        ``git push origin --delete <branch>``.  Used for
        ``anonymization_failed`` cleanup where the branch was already pushed.
    """
    wt_path = _worktree_path(repo_root, branch_name)

    # Step 1: Remove worktree
    if wt_path.exists():
        rc, _, stderr = await _run_git(
            "worktree", "remove", str(wt_path),
            cwd=repo_root,
        )
        if rc != 0:
            # Try force-remove for dirty worktrees (uncommitted changes)
            rc2, _, stderr2 = await _run_git(
                "worktree", "remove", "--force", str(wt_path),
                cwd=repo_root,
            )
            if rc2 != 0:
                logger.warning(
                    "Failed to remove healing worktree %s (even with --force): %s | %s",
                    wt_path,
                    stderr,
                    stderr2,
                )
            else:
                logger.debug("Force-removed dirty healing worktree: %s", wt_path)
    else:
        # Worktree directory doesn't exist; prune the git worktree metadata
        await _run_git("worktree", "prune", cwd=repo_root)

    # Step 2: Delete remote branch (before local, so we still have the ref)
    if delete_remote:
        rc, _, stderr = await _run_git(
            "push", "origin", "--delete", branch_name,
            cwd=repo_root,
        )
        if rc != 0:
            logger.warning(
                "Failed to delete remote branch %r: %s",
                branch_name,
                stderr,
            )

    # Step 3: Delete local branch
    if delete_branch:
        rc, _, stderr = await _run_git(
            "branch", "-D", branch_name,
            cwd=repo_root,
        )
        if rc != 0:
            logger.warning(
                "Failed to delete local branch %r: %s",
                branch_name,
                stderr,
            )

    logger.info(
        "Removed healing worktree: branch=%s delete_branch=%s delete_remote=%s",
        branch_name,
        delete_branch,
        delete_remote,
    )


async def reap_stale_worktrees(
    repo_root: Path,
    pool: asyncpg.Pool,
) -> int:
    """Reap stale and orphaned healing worktrees on dispatcher startup.

    Scans ``.healing-worktrees/`` for:

    1. **Terminal + aged**: worktrees for attempts with a terminal status
       (``failed``, ``timeout``, etc.) and ``closed_at`` older than 24 hours.
    2. **Orphaned worktrees**: directories in ``.healing-worktrees/`` with
       no matching ``healing_attempts`` row.
    3. **Orphaned branches**: local ``hotfix/*/`` branches with no worktree
       and no active (``investigating`` / ``pr_open``) attempt.

    Parameters
    ----------
    repo_root:
        Absolute path to the repository root.
    pool:
        asyncpg connection pool for ``shared.healing_attempts`` queries.

    Returns
    -------
    int
        Total count of reaped worktrees (terminal + orphans).
    """
    reaped = 0
    now = datetime.now(UTC)
    threshold = now - timedelta(hours=_REAP_TERMINAL_AFTER_HOURS)

    worktree_base = repo_root / _WORKTREE_BASE

    # -----------------------------------------------------------------------
    # Phase 1: scan worktree directories
    # -----------------------------------------------------------------------
    if worktree_base.exists():
        # Worktrees are stored at:
        #   .healing-worktrees/hotfix/<butler_name>/<short>-<epoch>/
        # which is 3 levels under worktree_base.
        # Scan:  worktree_base / "hotfix" / <butler_name> / <slug>
        candidate_paths: list[Path] = []
        hotfix_dir = worktree_base / _BRANCH_PREFIX
        if hotfix_dir.is_dir():
            for butler_dir in hotfix_dir.iterdir():
                if not butler_dir.is_dir():
                    continue
                for wt_dir in butler_dir.iterdir():
                    if wt_dir.is_dir():
                        candidate_paths.append(wt_dir)

        # Reconstruct branch names from paths:
        #   branch = "hotfix/<butler_name>/<slug>"
        def _branch_from_wt(wt_dir: Path) -> str:
            return f"{_BRANCH_PREFIX}/{wt_dir.parent.name}/{wt_dir.name}"

        # Fetch all matching attempt rows in one query
        candidate_branches = [_branch_from_wt(p) for p in candidate_paths]
        attempt_map = await _healing_attempts_for_branches(pool, candidate_branches)

        for wt_dir in candidate_paths:
            branch = _branch_from_wt(wt_dir)
            attempt = attempt_map.get(branch)

            if attempt is None:
                # Orphaned worktree — no DB row
                logger.warning(
                    "Removing orphaned healing worktree with no matching attempt: %s",
                    branch,
                )
                await remove_healing_worktree(
                    repo_root, branch,
                    delete_branch=True,
                    delete_remote=False,
                )
                reaped += 1
                continue

            status = attempt["status"]
            closed_at = attempt.get("closed_at")

            if status not in _TERMINAL_STATUSES:
                # Active attempt — preserve it
                continue

            # Terminal: check age
            if closed_at is None:
                # No closed_at — use updated_at as fallback
                closed_at = attempt.get("updated_at")

            if closed_at is None:
                continue  # Can't determine age; skip

            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=UTC)

            if closed_at > threshold:
                # Terminal but not old enough yet
                continue

            # Eligible for reaping
            # For pr_open/pr_merged: keep local branch (it backs the PR)
            delete_branch = status not in ("pr_open", "pr_merged")
            logger.info(
                "Reaping stale healing worktree: branch=%s status=%s closed_at=%s",
                branch,
                status,
                closed_at,
            )
            await remove_healing_worktree(
                repo_root, branch,
                delete_branch=delete_branch,
                delete_remote=False,
            )
            reaped += 1

    # -----------------------------------------------------------------------
    # Phase 2: orphaned hotfix branches with no worktree and no active attempt
    # -----------------------------------------------------------------------
    all_hotfix_branches = await _list_hotfix_branches(repo_root)
    worktree_branches = set(await _list_worktree_branches(repo_root))

    orphan_branches = [b for b in all_hotfix_branches if b not in worktree_branches]
    if orphan_branches:
        branch_attempts = await _healing_attempts_for_branches(pool, orphan_branches)
        for branch in orphan_branches:
            attempt = branch_attempts.get(branch)
            if attempt is not None:
                status = attempt["status"]
                # Keep branches that back active investigations or open PRs
                if status in ("investigating", "pr_open"):
                    continue
            # No attempt or terminal attempt — delete the orphaned branch
            logger.info("Deleting orphaned hotfix branch with no worktree: %s", branch)
            rc, _, stderr = await _run_git(
                "branch", "-D", branch,
                cwd=repo_root,
            )
            if rc != 0:
                logger.warning("Failed to delete orphaned branch %r: %s", branch, stderr)

    return reaped
