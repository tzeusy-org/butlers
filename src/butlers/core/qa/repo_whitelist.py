"""Repository whitelist cache for QA PR creation enforcement.

Loads ``public.qa_allowed_repositories`` from the DB with a 60-second TTL
(matching the ingestion_policy.py pattern).  The whitelist is fail-closed:

- When the table is *empty*, ALL PR creation is blocked.
- When the DB is *unreachable*, the last good cache is retained (fail-open on
  DB error, but fail-closed on empty table — same semantics as ingestion_policy).

URL parsing handles both HTTPS and SSH remote formats::

    https://github.com/owner/repo.git   → ("owner", "repo")
    git@github.com:owner/repo.git       → ("owner", "repo")
    https://github.com/owner/repo       → ("owner", "repo")

Usage::

    whitelist = RepoWhitelist(db_pool=pool)
    await whitelist.ensure_loaded()

    allowed, reason = await whitelist.is_allowed("owner/repo")
    if not allowed:
        # block PR creation and notify owner
        ...

Spec reference
--------------
bu-tqvw9: Add repository whitelist to QA staffer PR creation
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------

# HTTPS: https://github.com/owner/repo[.git]
_HTTPS_RE = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)

# SSH: git@github.com:owner/repo[.git]
_SSH_RE = re.compile(
    r"git@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    re.IGNORECASE,
)

# Bare owner/repo (already normalised)
_OWNER_REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$")


def parse_repo_url(remote: str) -> tuple[str, str] | None:
    """Parse a GitHub remote URL or ``owner/repo`` string into ``(owner, repo)``.

    Supports:
    - ``https://github.com/owner/repo[.git]``
    - ``git@github.com:owner/repo[.git]``
    - ``owner/repo`` (already normalised)

    Returns ``None`` if the string cannot be parsed.
    """
    remote = remote.strip()
    for pattern in (_HTTPS_RE, _SSH_RE, _OWNER_REPO_RE):
        m = pattern.match(remote)
        if m:
            owner = m.group("owner").lower()
            repo = m.group("repo").lower()
            if owner and repo:
                return owner, repo
    return None


def normalise_owner_repo(owner: str, repo: str) -> str:
    """Return the canonical ``owner/repo`` string (lowercased)."""
    return f"{owner.lower()}/{repo.lower()}"


# ---------------------------------------------------------------------------
# RepoWhitelist
# ---------------------------------------------------------------------------

#: TTL in seconds before the whitelist is refreshed from the DB.
_DEFAULT_TTL_S: float = 60.0


class RepoWhitelist:
    """In-memory cache of the QA repository whitelist with 60s TTL.

    Parameters
    ----------
    db_pool:
        asyncpg connection pool. When ``None`` the whitelist cannot be loaded;
        the cache stays empty and all PRs are blocked (fail-closed).
    refresh_interval_s:
        Seconds between background refreshes (default 60).
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool | None,
        refresh_interval_s: float = _DEFAULT_TTL_S,
    ) -> None:
        self._db_pool = db_pool
        self._refresh_interval_s = refresh_interval_s

        # Set of "owner/repo" strings (lowercased) for enabled entries.
        self._allowed: frozenset[str] = frozenset()
        # True once the first DB load has been attempted (even if it failed).
        self._loaded: bool = False
        self._last_loaded_at: float | None = None
        self._load_lock = asyncio.Lock()
        self._background_refresh_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # DB loading
    # ------------------------------------------------------------------

    async def _load(self) -> None:
        """Query public.qa_allowed_repositories and refresh the in-memory set.

        On DB error: log WARNING and retain previous cache (fail-open on error,
        fail-closed on empty table).
        """
        if self._db_pool is None:
            logger.warning(
                "repo_whitelist: no DB pool configured — all PR creation blocked (fail-closed)"
            )
            self._loaded = True
            self._last_loaded_at = time.monotonic()
            return

        try:
            rows = await self._db_pool.fetch(
                """
                SELECT owner, repo
                FROM public.qa_allowed_repositories
                WHERE enabled = TRUE
                ORDER BY owner ASC, repo ASC
                """
            )
            new_allowed: frozenset[str] = frozenset(
                normalise_owner_repo(row["owner"], row["repo"]) for row in rows
            )
            is_initial = not self._loaded
            self._allowed = new_allowed
            self._loaded = True
            self._last_loaded_at = time.monotonic()

            log_fn = logger.info if is_initial else logger.debug
            log_fn(
                "repo_whitelist: loaded %d allowed repo(s)",
                len(new_allowed),
            )
            if len(new_allowed) == 0:
                logger.warning(
                    "repo_whitelist: whitelist is empty — ALL QA PR creation blocked (fail-closed)"
                )

        except Exception as exc:
            logger.warning("repo_whitelist: failed to load from DB (retaining cache): %s", exc)
            # Still mark as loaded / update timestamp to avoid hammering the DB.
            self._loaded = True
            self._last_loaded_at = time.monotonic()

    # ------------------------------------------------------------------
    # TTL refresh
    # ------------------------------------------------------------------

    def _is_stale(self) -> bool:
        """Return True if the cache has never been loaded or has expired."""
        if self._last_loaded_at is None:
            return True
        return (time.monotonic() - self._last_loaded_at) >= self._refresh_interval_s

    def _maybe_schedule_refresh(self) -> None:
        """Schedule a background refresh task if the cache is stale."""
        if not self._is_stale():
            return
        if self._background_refresh_task is not None and not self._background_refresh_task.done():
            return

        async def _refresh() -> None:
            async with self._load_lock:
                if self._is_stale():
                    await self._load()

        try:
            loop = asyncio.get_running_loop()
            self._background_refresh_task = loop.create_task(_refresh())
        except RuntimeError:
            # No running event loop — skip background refresh.
            pass

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Perform the initial load from DB.

        Must be called once before using ``is_allowed``.  Subsequent refreshes
        are triggered lazily via background tasks.
        """
        async with self._load_lock:
            if not self._loaded:
                await self._load()

    def is_allowed(self, owner_repo: str) -> tuple[bool, str]:
        """Check whether *owner_repo* is in the whitelist.

        Parameters
        ----------
        owner_repo:
            Either ``"owner/repo"`` or a full GitHub remote URL (HTTPS or SSH).
            URL parsing is applied automatically.

        Returns
        -------
        tuple[bool, str]
            ``(allowed, reason)`` where ``reason`` explains the decision.
        """
        self._maybe_schedule_refresh()

        if not self._loaded:
            # Should not happen if ensure_loaded() was called, but guard anyway.
            return False, "whitelist_not_loaded"

        # Parse URL → owner/repo if needed
        parsed = parse_repo_url(owner_repo)
        if parsed is None:
            # owner_repo already normalised?  Try direct lookup after lower().
            key = owner_repo.strip().lower()
        else:
            key = normalise_owner_repo(*parsed)

        if len(self._allowed) == 0:
            return False, "whitelist_empty"

        if key in self._allowed:
            return True, "allowed"

        return False, "not_in_whitelist"

    def get_allowed_repos(self) -> frozenset[str]:
        """Return the current set of allowed ``owner/repo`` strings (snapshot)."""
        return self._allowed
