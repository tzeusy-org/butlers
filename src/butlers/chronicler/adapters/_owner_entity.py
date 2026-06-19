"""Shared helper: resolve the owner entity_id from ``public.entities``.

Owner-only adapters (focus, sessions, spotify, steam, meals, owntracks,
reading, google_health) need to stamp the owner entity on every episode they
project. This helper provides a single resolution point so the lookup
pattern is consistent across all adapters.

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
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


async def resolve_owner_entity_id(pool: asyncpg.Pool) -> UUID | None:
    """Return the owner ``entity_id`` from ``public.entities``, or ``None``.

    Queries ``public.entities WHERE 'owner' = ANY(roles)`` and returns the
    ``id`` column value. Returns ``None`` gracefully on any failure:
    missing table, no owner entity, or unexpected type.

    This should be called once per ``project()`` invocation (not per row)
    to avoid N+1 DB round-trips.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id
                FROM public.entities
                WHERE 'owner' = ANY(roles)
                LIMIT 1
                """
            )
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
    Runs as a plain execute (no surrounding transaction) because the caller
    may already be inside a connection context; callers that need atomicity
    wrap both the episode upsert and this call inside their own transaction.
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


__all__ = ["resolve_owner_entity_id", "upsert_owner_episode_entity"]
