"""Managed git clone for QA investigation worktrees.

The QA staffer maintains a local clone of the configured repository URL.
Each investigation creates a git worktree from this clone rather than from
the daemon's working directory, which may not be a git repository in
deployed environments.

Clone lifecycle:
  1. ``ensure_cloned()`` — reads ``repo_url`` from ``public.qa_repo_config``,
     creates a shallow clone at ``~/.cache/butlers/qa-repo/`` if absent.
  2. ``refresh()`` — fetches latest ``origin/main`` and hard-resets the clone.
     Called before each patrol dispatch phase.
  3. ``set_repo_url()`` — updates the DB and deletes the old clone so the next
     ``ensure_cloned()`` picks up the new URL.

All git operations are serialized via an ``asyncio.Lock`` to prevent races
between patrol cycles and API-triggered syncs.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_DEFAULT_REPO_URL = "https://github.com/Tzeusy/butlers"
_CLONE_DIR = Path.home() / ".cache" / "butlers" / "qa-repo"


async def _run_git(
    *args: str,
    cwd: Path,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, stdout, stderr


class ManagedRepoClone:
    """Manages a local git clone for QA investigation worktrees."""

    def __init__(self, pool: asyncpg.Pool | None, clone_dir: Path | None = None) -> None:
        self._pool = pool
        self._clone_dir = clone_dir or _CLONE_DIR
        self._lock = asyncio.Lock()
        self._clone_path: Path | None = None

    @property
    def clone_path(self) -> Path | None:
        """Return the current clone path, or None if not yet cloned."""
        return self._clone_path

    @property
    def repo_root(self) -> Path:
        """Return the clone path for use as repo_root."""
        return self._clone_path or Path(".")

    async def _read_repo_url(self) -> str:
        """Read repo_url from DB, falling back to default."""
        if self._pool is None:
            return _DEFAULT_REPO_URL
        row = await self._pool.fetchrow("SELECT repo_url FROM public.qa_repo_config LIMIT 1")
        if row is None:
            return _DEFAULT_REPO_URL
        return row["repo_url"]

    async def _update_clone_path(self, clone_path: str | None) -> None:
        """Update clone_path column on the config row."""
        if self._pool is None:
            return
        await self._pool.execute(
            "UPDATE public.qa_repo_config SET clone_path = $1, updated_at = now()",
            clone_path,
        )

    async def _update_sync_error(self, error: str | None) -> None:
        """Update last_sync_error column on the config row."""
        if self._pool is None:
            return
        await self._pool.execute(
            "UPDATE public.qa_repo_config SET last_sync_error = $1, updated_at = now()",
            error,
        )

    async def _mark_synced(self) -> None:
        """Mark a successful sync (clear error, set timestamp)."""
        if self._pool is None:
            return
        await self._pool.execute(
            "UPDATE public.qa_repo_config "
            "SET last_synced_at = now(), last_sync_error = NULL, updated_at = now()"
        )

    async def ensure_cloned(self) -> Path:
        """Ensure the repo is cloned. Returns the clone path."""
        async with self._lock:
            return await self._ensure_cloned_unlocked()

    async def _ensure_cloned_unlocked(self) -> Path:
        """Internal: clone if .git is missing."""
        repo_url = await self._read_repo_url()

        if (self._clone_dir / ".git").is_dir():
            self._clone_path = self._clone_dir
            await self._update_clone_path(str(self._clone_dir))
            return self._clone_dir

        # Clone fresh
        self._clone_dir.parent.mkdir(parents=True, exist_ok=True)

        # Remove any partial clone remnants
        if self._clone_dir.exists():
            shutil.rmtree(self._clone_dir, ignore_errors=True)

        logger.info("Cloning %s to %s", repo_url, self._clone_dir)
        rc, _stdout, stderr = await _run_git(
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            "main",
            repo_url,
            str(self._clone_dir),
            cwd=self._clone_dir.parent,
        )
        if rc != 0:
            error_msg = f"git clone failed (rc={rc}): {stderr}"
            logger.error(error_msg)
            await self._update_sync_error(error_msg)
            await self._update_clone_path(None)
            raise RuntimeError(error_msg)

        self._clone_path = self._clone_dir
        await self._update_clone_path(str(self._clone_dir))
        await self._update_sync_error(None)
        logger.info("Clone complete: %s", self._clone_dir)
        return self._clone_dir

    async def refresh(self) -> Path:
        """Fetch latest origin/main and hard-reset. Returns clone path."""
        async with self._lock:
            clone = await self._ensure_cloned_unlocked()

            # Unshallow if needed so worktree branches can be created
            rc, _stdout, stderr = await _run_git("fetch", "origin", "main", cwd=clone)
            if rc != 0:
                error_msg = f"git fetch failed (rc={rc}): {stderr}"
                logger.warning("Repo refresh fetch failed: %s", error_msg)
                await self._update_sync_error(error_msg)
                # Non-fatal: continue with stale clone
                return clone

            rc, _stdout, stderr = await _run_git("checkout", "main", cwd=clone)
            if rc != 0:
                logger.warning("git checkout main failed (rc=%d): %s", rc, stderr)

            rc, _stdout, stderr = await _run_git("reset", "--hard", "origin/main", cwd=clone)
            if rc != 0:
                error_msg = f"git reset failed (rc={rc}): {stderr}"
                logger.warning("Repo refresh reset failed: %s", error_msg)
                await self._update_sync_error(error_msg)
                return clone

            await self._mark_synced()

            logger.info("Repo refreshed: %s", clone)
            return clone

    async def get_config(self) -> dict:
        """Return current repo config from DB."""
        if self._pool is None:
            return {
                "repo_url": _DEFAULT_REPO_URL,
                "clone_path": str(self._clone_path) if self._clone_path else None,
                "last_synced_at": None,
                "last_sync_error": None,
            }
        row = await self._pool.fetchrow(
            "SELECT repo_url, clone_path, last_synced_at, last_sync_error, "
            "created_at, updated_at FROM public.qa_repo_config LIMIT 1"
        )
        if row is None:
            return {"repo_url": _DEFAULT_REPO_URL, "clone_path": None}
        return dict(row)

    async def set_repo_url(self, url: str) -> dict:
        """Update repo_url. If changed, deletes old clone for re-clone on next ensure."""
        if self._pool is None:
            raise RuntimeError("No DB pool available")

        current_url = await self._read_repo_url()
        url = url.strip()

        async with self._lock:
            await self._pool.execute(
                "UPDATE public.qa_repo_config SET repo_url = $1, updated_at = now()",
                url,
            )

            if url != current_url and self._clone_dir.exists():
                logger.info(
                    "Repo URL changed from %s to %s — removing old clone",
                    current_url,
                    url,
                )
                shutil.rmtree(self._clone_dir, ignore_errors=True)
                self._clone_path = None
                await self._pool.execute(
                    "UPDATE public.qa_repo_config "
                    "SET clone_path = NULL, last_synced_at = NULL, "
                    "last_sync_error = NULL, updated_at = now()"
                )

        return await self.get_config()
