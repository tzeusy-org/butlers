"""Connector-neutral priority-contact snapshot with loaded/failed/stale semantics.

Lifted out of ``GmailPolicyEvaluator`` so every connector that marks drops of known
contacts (Gmail by email, WhatsApp by phone, Drive by email) shares one TTL, one
state machine and one failure policy. Spec: ingestion-priority-contacts; the q43
availability contract in ``butlers.core.known_contact_state``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from butlers.core.known_contact_state import KnownContactSnapshot

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

#: TTL in seconds for the DB-backed priority contacts cache.
PRIORITY_CONTACTS_TTL = 900  # 15 minutes

# Stored phone numbers keep source formatting and often omit the country code, so a
# JID phone is compared on digits with a bounded suffix match (mirrors identity.py).
PHONE_SUFFIX_MIN_DIGITS = 8
PHONE_SUFFIX_MAX_DELTA = 2

_NON_DIGITS = re.compile(r"\D")

# Fixed predicate literals only: never interpolate caller input into the query.
_SQL_TEMPLATE = """
    SELECT DISTINCT ef.object AS value
    FROM public.priority_contacts pc
    JOIN relationship.entity_facts ef ON ef.subject = pc.entity_id
    WHERE ef.predicate = '{predicate}'
      AND ef.object_kind = 'literal'
      AND ef.validity = 'active'
      AND ef.object IS NOT NULL
      AND pc.entity_id IS NOT NULL
    """


def normalize_phone_digits(value: str) -> str:
    """Digits-only form of a phone number or WhatsApp user JID part."""
    return _NON_DIGITS.sub("", value or "")


def phone_is_known(digits: str, contacts: frozenset[str]) -> bool:
    """True when ``digits`` matches a stored phone (exact, or bounded suffix match)."""
    if not digits:
        return False
    if digits in contacts:
        return True
    if len(digits) < PHONE_SUFFIX_MIN_DIGITS:
        return False
    for stored in contacts:
        if (
            len(stored) >= PHONE_SUFFIX_MIN_DIGITS
            and abs(len(stored) - len(digits)) <= PHONE_SUFFIX_MAX_DELTA
            and (stored.endswith(digits) or digits.endswith(stored))
        ):
            return True
    return False


class PriorityContactSnapshotSource:
    """DB-backed priority-contact set with a TTL cache and an atomic snapshot state.

    ``predicate`` selects which identifier kind is loaded (``has-email`` or
    ``has-phone``); ``normalize`` canonicalizes each stored value. If the DB is
    unreachable during a refresh the previous cache is retained and the snapshot
    reports ``failed``; on the very first call an empty set is returned.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool | None = None,
        ttl: float = PRIORITY_CONTACTS_TTL,
        *,
        predicate: Literal["has-email", "has-phone"] = "has-email",
        normalize: Callable[[str], str],
        label: str = "PriorityContactSnapshotSource",
        provider: str = "Connector",
    ) -> None:
        self._provider = provider
        self._db_pool = db_pool
        self._ttl = ttl
        self._sql = _SQL_TEMPLATE.format(predicate=predicate)
        self._normalize = normalize
        self._label = label
        self._cache: frozenset[str] = frozenset()
        # float('-inf') ensures the cache is always expired on the very first call,
        # regardless of how long the process has been running. Using 0.0 caused a
        # subtle bug: on freshly-booted CI runners (uptime < TTL seconds), the
        # expression `time.monotonic() - 0.0 < ttl` evaluated to False, silently
        # skipping the first DB refresh and returning an empty frozenset.
        self._cache_loaded_at: float = float("-inf")
        self._snapshot = KnownContactSnapshot()
        self._refresh_lock = asyncio.Lock()

    def _cache_expired(self) -> bool:
        return (time.monotonic() - self._cache_loaded_at) >= self._ttl

    def _transition(self, state: str, reason: str, *, success: datetime | None = None) -> None:
        self._snapshot = KnownContactSnapshot(
            contacts=self._cache,
            state=state,
            reason=reason,
            generation=self._snapshot.generation + 1,
            last_success_at=success if success is not None else self._snapshot.last_success_at,
        )

    def peek_snapshot(self) -> KnownContactSnapshot:
        """Read one atomic generation, aging it without initiating a query."""
        if self._snapshot.state == "loaded" and self._cache_expired():
            self._transition("stale", "ttl_expired")
        return self._snapshot

    async def get_snapshot(
        self,
        observer: Callable[[], Awaitable[None]] | None = None,
    ) -> KnownContactSnapshot:
        """Refresh atomically; publication never changes the query's truth.

        The optional observer owns a bounded publication path. Cancellation of
        an unfinished query is unknown; cancellation after its success leaves
        that success intact and propagates to the owning caller.
        """

        async def notify() -> None:
            if observer is not None:
                try:
                    await observer()
                except Exception:
                    logger.warning("%s classification publication unavailable", self._provider)

        async with self._refresh_lock:
            if not self._cache_expired():
                return self.peek_snapshot()
            self._transition("unloaded", "refreshing")
            try:
                await notify()
                if self._db_pool is None:
                    self._transition("unloaded", "no_pool")
                else:
                    rows = await self._db_pool.fetch(self._sql)
                    self._cache = frozenset(self._normalize(row["value"]) for row in rows)
                    self._cache_loaded_at = time.monotonic()
                    self._transition("loaded", "none", success=datetime.now(UTC))
            except asyncio.CancelledError:
                self._transition("failed", "refresh_cancelled")
                raise
            except Exception:
                self._transition("failed", "refresh_failed")
                # Never log DB error tails or contact identities.
                logger.warning("%s: DB refresh failed; retaining previous cache", self._label)
            # Deliberately outside the query exception/cancellation boundary.
            await notify()
            return self.peek_snapshot()
