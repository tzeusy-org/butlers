"""Production snapshot and grant providers for the owner-watch service (bu-4c7nvp.1.5.1; dark).

``OwnerWatchRuntime`` builds the one ``WatchService`` (PostgreSQL watch store, the one conditions
evaluator over ``public.condition_*``, the insight withdraw) and the two providers it needs. Both
providers read one place, ``public.owner_watch_sources()`` (core_368) plus the owner entity, through
the Switchboard pool, and they never write.

* Grant (``grant_for``, re-derived from the live read on every call, never model-supplied): the
  source set is ``perception:<kind>`` for every registered perception kind plus ``clock``; the
  subjects are the owner entity and the null subject. Nothing else is grantable yet (bundles,
  conditions, read checks and routines have no registry to derive from), and the goal-own
  decision, effect and inbox sources are never granted to a watch. A registry that moves changes
  the grant digest, so the existing ``grant_is_current`` check pauses a replayed watch and refuses
  an approval proposed on the old grant.
* Snapshot (``snapshot_for``): ``now`` plus ``unmeasurable`` = the kinds nothing has ever written.
  Occurrence leaves (M7, ``transition``) cannot be witnessed by a snapshot, so it carries no
  decisions, inputs or effects. It has no label: the snapshot holds no source rows, which is the
  honest provenance (a clock-only fire carries the registered label).
* Freshness: ``prepare`` re-reads the sources and caches them for ``SOURCES_TTL`` (60 s). A read
  that fails, an unreadable registry, or no single owner entity clears the cache, so both
  providers answer "unknown": ``grant_for`` raises ``GrantUnavailable`` (a refusal, so create and
  renew are refused), ``snapshot_for`` returns None (the tick is blind and the apply is
  unavailable), and nothing is paused, fired or changed. A tick prepares first, so a running watch
  is never judged against a stale grant.

``resolve_providers`` is the one wiring point. It returns the absent providers unless
``OWNER_WATCHES_LIVE`` is on and Switchboard registered its database.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.conditions import ConditionEvaluator, Grant, Snapshot
from butlers.conditions.predicate import SOURCE_CLOCK
from butlers.conditions.sql import PostgresConditionStore
from butlers.core.owner import resolve_owner_entity_id_two_step
from butlers.watches import tick
from butlers.watches.approval import PROVIDERS, Providers
from butlers.watches.delivery import InsightDelivery
from butlers.watches.feed import (
    ReadFeed,
    ReadTransport,
    SwitchboardReadTransport,
    registered_sources,
    validate_reads,
)
from butlers.watches.model import GrantUnavailable
from butlers.watches.service import WatchService
from butlers.watches.sql import PostgresWatchStore

logger = logging.getLogger(__name__)

SOURCES_TTL = timedelta(seconds=60)
LOCK_POOL_MAX = 3
_SOURCES_SQL = "SELECT public.owner_watch_sources()"


@dataclass(frozen=True)
class Sources:
    """One consistent read: the registered kinds, the unwritten ones and the owner entity."""

    fetched_at: datetime
    kinds: tuple[str, ...]
    unwritten: frozenset[str]
    owner: str


def parse_sources(raw: Any) -> tuple[tuple[str, ...], frozenset[str]]:
    """Validate the function's JSON; raise ValueError on any shape that is not exactly it."""
    rows = json.loads(raw) if isinstance(raw, str) else raw
    if type(rows) is not list or not rows:
        raise ValueError("owner watch sources: expected a non-empty list")
    kinds: list[str] = []
    unwritten: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"kind", "written"}:
            raise ValueError("owner watch sources: unexpected row shape")
        kind = row["kind"]
        if type(kind) is not str or not kind or kind in kinds:
            raise ValueError("owner watch sources: bad or repeated kind")
        kinds.append(kind)
        if type(row["written"]) is not bool:
            raise ValueError("owner watch sources: bad written flag")
        if not row["written"]:
            unwritten.add(f"perception:{kind}")
    return tuple(sorted(kinds)), frozenset(unwritten)


async def read_sources(pool: Any, now: datetime) -> Sources | None:
    """Read the sources and the owner entity; None (never an exception) when unreadable."""
    try:
        raw = await pool.fetchval(_SOURCES_SQL)
        kinds, unwritten = parse_sources(raw)
        owner = await resolve_owner_entity_id_two_step(pool)
    except Exception:
        logger.warning("owner_watch_sources_unreadable", exc_info=True)
        return None
    if owner is None:
        logger.warning("owner_watch_sources_no_owner_entity")
        return None
    return Sources(now, kinds, unwritten, str(owner))


Clock = Callable[[], datetime]


class OwnerWatchRuntime:
    """The service and the providers over one Switchboard pool and one lock pool."""

    def __init__(
        self,
        pool: Any,
        lock_pool: Any,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        ttl: timedelta = SOURCES_TTL,
        transport: ReadTransport | None = None,
    ) -> None:
        self._pool = pool
        self._transport = transport
        self._clock = clock
        self._ttl = ttl
        self._sources: Sources | None = None
        self.service = WatchService(
            PostgresWatchStore(pool, lock_pool=lock_pool),
            ConditionEvaluator(PostgresConditionStore(pool)),
            self.grant_for,
            withdraw=InsightDelivery(pool).withdraw,
            validate_reads=validate_reads,
        )

    async def prepare(self, now: datetime | None = None) -> bool:
        """Re-read the sources; False (and an empty cache) when they cannot be read."""
        self._sources = await read_sources(self._pool, now or self._clock())
        return self._sources is not None

    def _fresh(self) -> Sources:
        sources = self._sources
        if sources is None or self._clock() - sources.fetched_at > self._ttl:
            raise GrantUnavailable("the live grant cannot be read now")
        return sources

    def grant_for(self, watch_id: str) -> Grant:
        sources = self._fresh()
        return Grant(
            goal_id=watch_id,
            sources=frozenset(
                {SOURCE_CLOCK, *(f"perception:{k}" for k in sources.kinds), *registered_sources()}
            ),
            subjects=frozenset({sources.owner, None}),
        )

    def snapshot_for(self, now: datetime) -> Snapshot | None:
        try:
            sources = self._fresh()
        except GrantUnavailable:
            return None
        return Snapshot(now=now, unmeasurable=sources.unwritten)

    def feed(self) -> ReadFeed | None:
        return ReadFeed(self._transport) if self._transport is not None else None

    def providers(self) -> Providers:
        return Providers(
            service_for=lambda: self.service,
            snapshot_for=self.snapshot_for,
            prepare=self.prepare,
            feed_for=self.feed,
        )


LockPoolFactory = Callable[[], Awaitable[Any]]

_database: Any = None
_lock_pool_factory: LockPoolFactory | None = None
_runtime: tuple[Any, OwnerWatchRuntime, Any] | None = None
_build_lock = asyncio.Lock()


def register_database(database: Any, *, lock_pool_factory: LockPoolFactory | None = None) -> None:
    """Switchboard registers its database at module start; the lock pool is made from it."""
    global _database, _lock_pool_factory
    _database = database
    _lock_pool_factory = lock_pool_factory or _sibling_pool_factory(database)


def _sibling_pool_factory(database: Any) -> LockPoolFactory:
    async def make() -> Any:
        sibling = copy.copy(database)
        sibling.pool = None
        sibling.min_pool_size = 0
        sibling.max_pool_size = LOCK_POOL_MAX
        return await sibling.connect()

    return make


async def resolve_providers(pool: Any, *, live: bool | None = None) -> Providers:
    """The production providers, or the absent ones (refuse and blind) when not live or unwired."""
    global _runtime
    if not (tick.OWNER_WATCHES_LIVE if live is None else live):
        return PROVIDERS
    if _database is None or _lock_pool_factory is None or getattr(_database, "pool", None) is None:
        return PROVIDERS
    async with _build_lock:
        if _runtime is None or _runtime[0] is not pool:
            await _close_locked()
            try:
                lock_pool = await _lock_pool_factory()
            except Exception:
                logger.warning("owner_watch_lock_pool_unavailable", exc_info=True)
                return PROVIDERS
            _runtime = (
                pool,
                OwnerWatchRuntime(pool, lock_pool, transport=SwitchboardReadTransport(pool)),
                lock_pool,
            )
        return _runtime[1].providers()


async def _close_locked() -> None:
    global _runtime
    if _runtime is not None:
        lock_pool = _runtime[2]
        _runtime = None
        try:
            await lock_pool.close()
        except Exception:
            logger.warning("owner_watch_lock_pool_close_failed", exc_info=True)


async def close_runtime() -> None:
    """Close the lock pool (Switchboard shutdown) and forget the runtime."""
    async with _build_lock:
        await _close_locked()


__all__ = [
    "GrantUnavailable",
    "OwnerWatchRuntime",
    "Sources",
    "close_runtime",
    "parse_sources",
    "read_sources",
    "register_database",
    "resolve_providers",
]
