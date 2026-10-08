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

    <repo_root>/.healing-worktrees/self-healing/<butler_name>/<fingerprint_short>-<epoch>/

The branch name matches the relative path under ``.healing-worktrees/``:
``self-healing/<butler_name>/<fingerprint_short>-<epoch>``

``<fingerprint_short>`` is the first 12 hex characters of the SHA-256 fingerprint.

Spec reference
--------------
openspec/changes/butler-self-healing/specs/healing-worktree/spec.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from butlers.core.git_custody import CustodyUnavailable, branch_exclusion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Directory name under repo root that holds all healing worktrees.
_WORKTREE_BASE = ".healing-worktrees"

#: Default branch prefix for self-healing branches.
_BRANCH_PREFIX = "self-healing"

#: Branch prefix used by QA staffer investigations.
_QA_BRANCH_PREFIX = "qa"

#: Default branch prefixes managed by this module. ``reap_stale_worktrees``
#: scans worktree directories and branches for every prefix in this tuple by
#: default; callers can override or extend the set of prefixes via
#: ``prefixes=`` where supported.
_ALL_BRANCH_PREFIXES: tuple[str, ...] = (_BRANCH_PREFIX, _QA_BRANCH_PREFIX)

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


def _branch_name(butler_name: str, fingerprint: str, prefix: str = _BRANCH_PREFIX) -> str:
    """Compute the branch name for a healing/QA investigation attempt.

    Parameters
    ----------
    butler_name:
        Name of the butler (used as path component).
    fingerprint:
        64-character SHA-256 hex fingerprint.
    prefix:
        Branch path prefix.  Defaults to ``"self-healing"`` for per-butler
        healing.  Pass ``"qa"`` for QA staffer investigations.
    """
    short = fingerprint[:_FINGERPRINT_SHORT_LEN]
    epoch = int(time.time())
    return f"{prefix}/{butler_name}/{short}-{epoch}"


def _worktree_path(repo_root: Path, branch_name: str) -> Path:
    """Compute the worktree path from branch name."""
    return repo_root / _WORKTREE_BASE / branch_name


# Each ordinary native Git communication has a finite bound while custody is held.
_GIT_COMMAND_TIMEOUT_SECONDS = 60.0


async def _drain_and_reap(proc: asyncio.subprocess.Process) -> None:
    """Discard residual output without buffering it while reaping a killed child."""

    async def discard(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(65536):
                pass

    # Cancelled communicate() may leave either PIPE transport paused. Waiting
    # alone can then deadlock even after SIGKILL; drain both streams to EOF.
    await asyncio.gather(discard(proc.stdout), discard(proc.stderr), proc.wait())


async def _run_git(
    *args: str,
    cwd: Path,
    capture_stderr: bool = True,
) -> tuple[int, str, str]:
    """Run bounded Git; stop and reap an interrupted child before custody releases."""
    # Shield creation so cancellation cannot lose the actual child handle.
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    )
    proc = None
    completed = False
    interrupted = False
    try:
        proc = await asyncio.shield(spawn)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), _GIT_COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return 124, "", "Git command timed out"
        completed = True
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if capture_stderr else ""
        return proc.returncode or 0, stdout, stderr
    finally:
        # Even repeated cancellation must finish creation/cleanup before the
        # surrounding branch_exclusion can release its cooperating-writer lock.
        while not spawn.done():
            try:
                await asyncio.shield(spawn)
            except asyncio.CancelledError:
                interrupted = True
            except Exception:
                break  # Preserve the original spawn error or cancellation.
        if proc is None and not spawn.cancelled():
            try:
                proc = spawn.result()
            except Exception:
                pass  # Preserve the original spawn exception/cancellation.
        if proc is not None and not completed:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            reap = asyncio.create_task(_drain_and_reap(proc))
            while not reap.done():
                try:
                    await asyncio.shield(reap)
                except asyncio.CancelledError:
                    interrupted = True
            reap.result()
        if interrupted:
            raise asyncio.CancelledError


async def _list_worktree_branches(repo_root: Path) -> list[str]:
    """Return list of branches that have an active worktree (from git worktree list)."""
    rc, stdout, _ = await _run_git(
        "worktree",
        "list",
        "--porcelain",
        cwd=repo_root,
    )
    if rc != 0:
        return []
    branches: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("branch "):
            # "branch refs/heads/self-healing/..."
            ref = line[len("branch ") :].strip()
            if ref.startswith("refs/heads/"):
                branches.append(ref[len("refs/heads/") :])
    return branches


async def _list_healing_branches(
    repo_root: Path,
    prefix: str = _BRANCH_PREFIX,
) -> list[str]:
    """Return list of all local branches matching ``<prefix>/*/*``.

    Parameters
    ----------
    repo_root:
        Absolute path to the repository root.
    prefix:
        Branch prefix to enumerate.  Defaults to ``"self-healing"`` for
        backward compatibility.  Pass ``"qa"`` to enumerate QA branches.
    """
    rc, stdout, _ = await _run_git(
        "branch",
        "--list",
        f"{prefix}/*/*",
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

    Queries ``public.healing_attempts`` for all rows whose ``branch_name`` is
    in the provided list.
    """
    if not branch_names:
        return {}
    rows = await pool.fetch(
        """
        SELECT branch_name, status, closed_at, updated_at, healing_session_id
        FROM public.healing_attempts
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
    prefix: str = _BRANCH_PREFIX,
    base_ref: str | None = None,
) -> tuple[Path, str]:
    """Create a git branch and worktree for a healing or QA investigation.

    Branches from *base_ref* when provided, otherwise from the local ``main``
    HEAD.  Initial QA callers pass the immutable commit resolved after a successful
    remote-main refresh. A moving ref is not an exact receipt identity.

    Parameters
    ----------
    repo_root:
        Absolute path to the root of the git repository (the daemon's working
        tree, NOT an existing worktree).
    butler_name:
        Name of the butler whose session failed (e.g. ``"email"``).
    fingerprint:
        64-character SHA-256 hex fingerprint for the error being investigated.
    prefix:
        Branch path prefix.  Defaults to ``"self-healing"`` for per-butler
        self-healing.  Pass ``"qa"`` for QA staffer investigations.
        The worktree path follows the pattern:
        ``{repo_root}/.healing-worktrees/{prefix}/{butler_name}/{fp_short}-{epoch}/``
    base_ref:
        Git commit/ref to branch from. Initial QA preparation passes the exact
        immutable commit from its successful origin/main refresh.
        When ``None`` (the default), falls back to local ``"main"``.

    Returns
    -------
    tuple[Path, str]
        ``(worktree_path, branch_name)`` where *worktree_path* is the absolute
        path to the new worktree directory and *branch_name* is the full branch
        reference (e.g. ``"self-healing/email/abc123def456-1710700000"`` or
        ``"qa/email/abc123def456-1710700000"`` when *prefix* is ``"qa"``).

    Raises
    ------
    WorktreeCreationError
        If branch creation fails (e.g. collision) or ``git worktree add`` fails
        (e.g. disk full, permissions, git lock).  Partial state is cleaned up
        before the exception propagates.
    """
    resolved_base = base_ref if base_ref is not None else "main"
    branch = _branch_name(butler_name, fingerprint, prefix=prefix)
    wt_path = _worktree_path(repo_root, branch)

    try:
        with branch_exclusion(repo_root):
            # Ensure parent directory exists
            wt_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: Create branch from the resolved base ref
            logger.info(
                "Creating investigation branch %r from base_ref=%r",
                branch,
                resolved_base,
            )
            rc, _, stderr = await _run_git(
                "branch",
                branch,
                resolved_base,
                cwd=repo_root,
            )
            if rc != 0:
                raise WorktreeCreationError(
                    f"Failed to create branch {branch!r}: {stderr}",
                    git_output=stderr,
                )

            # Step 2: Create worktree at the computed path
            rc, _, stderr = await _run_git(
                "worktree",
                "add",
                str(wt_path),
                branch,
                cwd=repo_root,
            )
            if rc != 0:
                # Clean up the orphaned branch we just created
                delete_rc, _, delete_stderr = await _run_git(
                    "branch",
                    "-D",
                    branch,
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
                "Created investigation worktree: path=%s branch=%s prefix=%s base_ref=%s",
                wt_path,
                branch,
                prefix,
                resolved_base,
            )
            return wt_path, branch

    except CustodyUnavailable as exc:
        raise WorktreeCreationError("Repository custody unavailable") from exc


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
        The branch name (e.g. ``"self-healing/email/abc123def456-1710700000"``).
        Used to derive the worktree path and to delete the branch.
    delete_branch:
        When ``True`` (default), delete the local branch after removing the
        worktree.  Set to ``False`` when the branch backs an open PR.
    delete_remote:
        When ``True``, also delete the remote branch via
        ``git push origin --delete <branch>``.  Used for
        ``anonymization_failed`` cleanup where the branch was already pushed.
    """
    try:
        with branch_exclusion(repo_root):
            wt_path = _worktree_path(repo_root, branch_name)

            # Step 1: Remove worktree
            if wt_path.exists():
                rc, _, stderr = await _run_git(
                    "worktree",
                    "remove",
                    str(wt_path),
                    cwd=repo_root,
                )
                if rc != 0:
                    # Try force-remove for dirty worktrees (uncommitted changes)
                    rc2, _, stderr2 = await _run_git(
                        "worktree",
                        "remove",
                        "--force",
                        str(wt_path),
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
                    "push",
                    "origin",
                    "--delete",
                    branch_name,
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
                    "branch",
                    "-D",
                    branch_name,
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

    except CustodyUnavailable:
        logger.warning("Healing cleanup deferred: repository custody unavailable")


async def reap_stale_worktrees(
    repo_root: Path,
    pool: asyncpg.Pool,
    prefixes: tuple[str, ...] = _ALL_BRANCH_PREFIXES,
) -> int:
    """Reap stale and orphaned healing worktrees on dispatcher startup.

    Scans ``.healing-worktrees/`` for:

    1. **Terminal + aged**: worktrees for attempts with a terminal status
       (``failed``, ``timeout``, etc.) and ``closed_at`` older than 24 hours.
    2. **Orphaned worktrees**: directories in ``.healing-worktrees/`` with
       no matching ``healing_attempts`` row.
    3. **Orphaned branches**: local ``<prefix>/*/*`` branches with no worktree
       and no active (``investigating`` / ``pr_open``) attempt.

    By default all known prefixes (``self-healing`` and ``qa``) are processed.
    Pass a custom *prefixes* tuple to restrict or extend scanning.

    Parameters
    ----------
    repo_root:
        Absolute path to the repository root.
    pool:
        asyncpg connection pool for ``public.healing_attempts`` queries.
    prefixes:
        Branch/directory prefixes to scan.  Defaults to
        ``_ALL_BRANCH_PREFIXES`` (``self-healing`` + ``qa``).

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
    # Phase 1: scan worktree directories for each known prefix
    # -----------------------------------------------------------------------
    if worktree_base.exists():
        # Worktrees are stored at:
        #   .healing-worktrees/<prefix>/<butler_name>/<short>-<epoch>/
        # which is 3 levels under worktree_base.
        # Collect candidate paths across all configured prefixes.
        candidate_paths: list[tuple[str, Path]] = []  # (prefix, wt_dir)
        for prefix in prefixes:
            prefix_dir = worktree_base / prefix
            if not prefix_dir.is_dir():
                continue
            for butler_dir in prefix_dir.iterdir():
                if not butler_dir.is_dir():
                    continue
                for wt_dir in butler_dir.iterdir():
                    if wt_dir.is_dir():
                        candidate_paths.append((prefix, wt_dir))

        # Reconstruct branch names from paths:
        #   branch = "<prefix>/<butler_name>/<slug>"
        def _branch_from_wt(prefix: str, wt_dir: Path) -> str:
            return f"{prefix}/{wt_dir.parent.name}/{wt_dir.name}"

        # Fetch all matching attempt rows in one query
        candidate_branches = [_branch_from_wt(prefix, wt_dir) for prefix, wt_dir in candidate_paths]
        attempt_map = await _healing_attempts_for_branches(pool, candidate_branches)

        for prefix, wt_dir in candidate_paths:
            branch = _branch_from_wt(prefix, wt_dir)
            attempt = attempt_map.get(branch)

            if attempt is None:
                # Orphaned worktree — no DB row
                logger.warning(
                    "Removing orphaned healing worktree with no matching attempt: %s",
                    branch,
                )
                await remove_healing_worktree(
                    repo_root,
                    branch,
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
                repo_root,
                branch,
                delete_branch=delete_branch,
                delete_remote=False,
            )
            reaped += 1

    # -----------------------------------------------------------------------
    # Phase 2: orphaned branches (no worktree, no active attempt) for each prefix
    # -----------------------------------------------------------------------
    worktree_branches = set(await _list_worktree_branches(repo_root))

    for prefix in prefixes:
        all_prefix_branches = await _list_healing_branches(repo_root, prefix=prefix)
        orphan_branches = [b for b in all_prefix_branches if b not in worktree_branches]
        if not orphan_branches:
            continue
        branch_attempts = await _healing_attempts_for_branches(pool, orphan_branches)
        for branch in orphan_branches:
            attempt = branch_attempts.get(branch)
            if attempt is not None:
                status = attempt["status"]
                # Keep branches that back active investigations or open PRs
                if status in ("investigating", "pr_open"):
                    continue
            # No attempt or terminal attempt — delete the orphaned branch
            logger.info("Deleting orphaned %s branch with no worktree: %s", prefix, branch)
            try:
                with branch_exclusion(repo_root):
                    rc, _, stderr = await _run_git(
                        "branch",
                        "-D",
                        branch,
                        cwd=repo_root,
                    )
            except CustodyUnavailable:
                logger.warning("Healing branch cleanup deferred: repository custody unavailable")
                continue
            if rc != 0:
                logger.warning("Failed to delete orphaned branch %r: %s", branch, stderr)

    return reaped
