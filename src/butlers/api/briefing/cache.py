"""Per-owner LRU+TTL cache for dashboard briefings.

Caches one Briefing per owner contact for 5 minutes. The cache lives in
process (an ordered dict acting as an LRU eviction store). The max_size
cap prevents unbounded growth if the system ever gains multiple contacts.

Cache key: owner contact id (str or UUID).
TTL: 300 seconds (5 minutes).
On cache hit: returns the original Briefing including its original generated_at.
On TTL expiry: the entry is evicted and a fresh Briefing is composed.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D3.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default TTL matches the 5-minute spec contract (D3)
# ---------------------------------------------------------------------------
DEFAULT_TTL_SECONDS: float = 300.0
DEFAULT_MAX_SIZE: int = 64  # generous cap for a single-tenant system


@dataclass
class _CacheEntry:
    briefing: dict  # serialised Briefing dict
    expires_at: float  # monotonic clock value


class BriefingCache:
    """Thread-safe (within asyncio event-loop) LRU+TTL briefing cache.

    This is an in-process cache. It is reset on dashboard restart (no
    persistence to DB). Cache keys are owner contact ids.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        # OrderedDict preserves insertion order for LRU eviction.
        self._store: OrderedDict[Any, _CacheEntry] = OrderedDict()

    def get(self, owner_id: Any) -> dict | None:
        """Return the cached Briefing dict for owner_id, or None on miss/expiry."""
        entry = self._store.get(owner_id)
        if entry is None:
            return None

        now = time.monotonic()
        if now >= entry.expires_at:
            # Expired: evict and report miss.
            del self._store[owner_id]
            return None

        # LRU: move to most-recently-used end.
        self._store.move_to_end(owner_id)
        return entry.briefing

    def set(self, owner_id: Any, briefing: dict) -> None:
        """Insert or update the cached Briefing for owner_id."""
        expires_at = time.monotonic() + self._ttl

        if owner_id in self._store:
            self._store.move_to_end(owner_id)
            self._store[owner_id] = _CacheEntry(briefing=briefing, expires_at=expires_at)
            return

        # Evict the oldest entry when at capacity.
        if len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[owner_id] = _CacheEntry(briefing=briefing, expires_at=expires_at)

    def invalidate(self, owner_id: Any) -> None:
        """Remove the cached entry for owner_id (no-op if not present)."""
        self._store.pop(owner_id, None)

    def invalidate_all(self) -> None:
        """Remove all cached entries.

        Use this when the caller cannot resolve a specific owner_id (e.g. the
        audit middleware) but knows that some owner-relevant state changed.
        This is semantically correct for single-tenant deployments where every
        entry belongs to the same owner.
        """
        self._store.clear()


# ---------------------------------------------------------------------------
# Owner-contact resolution helper
# ---------------------------------------------------------------------------


async def resolve_owner_id(pool: asyncpg.Pool) -> object | None:
    """Return the owner contact id from the public schema, or None.

    Used by mutation endpoints to perform precise per-owner cache invalidation.
    Errors are swallowed so that a missing or stale contacts table does not
    block the primary operation.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT c.id
            FROM public.contacts c
            JOIN public.entities e ON c.entity_id = e.id
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1
            """
        )
        return row["id"] if row is not None else None
    except Exception as exc:
        logger.debug("Could not resolve owner id for cache invalidation: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Module-level singleton used by the router.
# Tests may replace this with a BriefingCache(ttl_seconds=...) instance.
# ---------------------------------------------------------------------------
_cache: BriefingCache = BriefingCache()


def get_cache() -> BriefingCache:
    """Return the module-level BriefingCache singleton."""
    return _cache


def replace_cache(cache: BriefingCache) -> None:
    """Replace the module-level cache (used in tests to inject a zero-TTL cache)."""
    global _cache
    _cache = cache
