"""Shared helper: resolve the owner entity_id from ``public.entities``.

Owner-only adapters (focus, sessions, spotify, steam, meals, owntracks,
reading, google_health, comms) need to stamp the owner entity on every
episode they project. This helper provides a single resolution point so the
lookup pattern is consistent across all adapters.

Lookup path:
  ``public.entities WHERE 'owner' = ANY(roles)`` → ``entities.id``

Returns ``None`` and logs at DEBUG level when:
- ``public.entities`` is absent.
- No entity has ``'owner' = ANY(roles)`` (owner not bootstrapped yet — fine in
  test environments or early-stage deployments).

The caller (each adapter's ``project()`` method) should call this once per
adapter run (not per row) and pass the result into each projection call.
NULL is a valid outcome; adapters must write NULL entity_id and log at
DEBUG, not ERROR.

Issue: bu-4c1ks
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


async def resolve_owner_entity_id(
    db: asyncpg.Pool | asyncpg.Connection,
) -> UUID | None:
    """Return the owner ``entity_id`` from ``public.entities``, or ``None``.

    Queries ``public.entities WHERE 'owner' = ANY(roles)`` and returns the
    ``id`` column value. Returns ``None`` gracefully on any failure:
    missing table, no owner entity, or unexpected type.

    This should be called once per ``project()`` invocation (not per row)
    to avoid N+1 DB round-trips.
    """
    try:
        if isinstance(db, asyncpg.Pool):
            async with db.acquire() as conn:
                row = await _fetch_owner_row(conn)
        else:
            row = await _fetch_owner_row(db)
    except asyncpg.PostgresError:
        logger.debug(
            "resolve_owner_entity_id: query failed (table absent or DB error) "
            "— entity_id will be NULL",
            exc_info=True,
        )
        return None

    if row is None:
        logger.debug(
            "resolve_owner_entity_id: no entity with role 'owner' found — entity_id will be NULL"
        )
        return None

    raw = row["id"]
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            logger.debug(
                "resolve_owner_entity_id: entity_id %r is not a valid UUID "
                "— entity_id will be NULL",
                raw,
            )
            return None

    logger.debug(
        "resolve_owner_entity_id: unexpected entity_id type %r — entity_id will be NULL",
        type(raw).__name__,
    )
    return None


async def _fetch_owner_row(conn: asyncpg.Connection) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id
        FROM public.entities
        WHERE 'owner' = ANY(roles)
        LIMIT 1
        """
    )


@asynccontextmanager
async def owner_episode_transaction(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """Make an episode upsert and its owner ``episode_entities`` link one unit.

    Every owner-only adapter writes the episode and then its owner link. If the link
    write fails after the episode upsert committed, the episode would exist without its
    owner (or an existing episode would be updated while its link is stale). Wrapping both
    statements in this block commits or rolls back the pair together
    (``butler-chronicler`` "Owner-Only Adapter Entity Attribution").

    It opens ``conn.transaction()``, which is a savepoint when the caller is already inside
    a transaction (comms, sessions, owntracks_ssid), so nesting changes nothing for them.
    Put the ``upsert_episode`` call and ``upsert_owner_episode_entity`` in the same block;
    ``tests/chronicler/test_owner_episode_atomicity_guard.py`` fails if a call is outside it.
    """
    factory = getattr(conn, "transaction", None)
    transaction = factory() if callable(factory) else None
    if transaction is None or not (
        hasattr(transaction, "__aenter__") and hasattr(transaction, "__aexit__")
    ):
        # An asyncpg connection always has a real transaction(). Hand-written stand-ins
        # that model only fetch/execute have none, and run the pair unwrapped.
        close = getattr(transaction, "close", None)
        if callable(close):
            close()  # an unawaited coroutine from an AsyncMock stand-in
        yield
        return
    async with transaction:
        yield


async def upsert_owner_episode_entity(
    conn: asyncpg.Connection,
    episode_id: UUID | None,
    *,
    owner_id: UUID | None,
) -> None:
    """Write a single ``episode_entities`` row for the owner of an episode.

    Owner-only adapters call this once per upserted episode with
    ``role='owner'``.  When ``owner_id`` or ``episode_id`` is None, no row
    is written (owner_id is None when the entity is not yet in the graph;
    episode_id is None only in test contexts where upsert_episode is mocked
    without returning a real DB row).

    Uses ``ON CONFLICT DO UPDATE`` so the call is idempotent on replays.
    This is a plain execute and is NOT atomic with the episode upsert on its own: call it
    inside ``async with owner_episode_transaction(conn):`` together with the episode upsert.
    """
    if owner_id is None or episode_id is None:
        return
    await conn.execute(
        """
        INSERT INTO episode_entities (episode_id, entity_id, role)
        VALUES ($1, $2, 'owner')
        ON CONFLICT (episode_id, entity_id)
        DO UPDATE SET role = EXCLUDED.role
        """,
        episode_id,
        owner_id,
    )


__all__ = [
    "owner_episode_transaction",
    "resolve_owner_entity_id",
    "upsert_owner_episode_entity",
]
