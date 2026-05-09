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

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

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
