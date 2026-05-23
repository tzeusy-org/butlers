"""Shared owner-entity resolution helpers.

Centralises the repeated ``public.entities WHERE 'owner' = ANY(roles)`` lookup
pattern used across multiple butlers and API routers.  Call sites that
previously duplicated this query now import from here.

Two helpers are provided:

* ``fetch_owner_entity_id`` — simple single-step lookup on ``public.entities``.
  Returns the owner entity UUID or ``None``.  Swallows ``asyncpg.PostgresError``
  so that pre-migration databases (missing ``public.entities``) fail gracefully.

* ``resolve_owner_entity`` — two-step lookup used by the memory preferences
  module: primary path via ``public.contacts JOIN public.entities`` (for
  installs that have a registered owner contact), fallback to the direct
  entities query.  Returns ``(entity_id, canonical_name)`` or raises
  ``ValueError`` when no owner can be found.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

logger = logging.getLogger(__name__)

# SQL fragments used by both helpers and their callers.
_OWNER_FROM_ENTITIES_SQL = (
    "SELECT id, canonical_name FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
)
_OWNER_ID_FROM_ENTITIES_SQL = "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"


async def fetch_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's UUID from ``public.entities``.

    Uses the canonical ``'owner' = ANY(roles)`` predicate on
    ``public.entities`` (post-core_016 path — roles live on the entity, not on
    the contact row).

    Returns ``None`` gracefully when:

    - ``public.entities`` does not exist yet (pre-migration database).
    - No entity with the ``owner`` role is found.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the shared database.

    Returns
    -------
    uuid.UUID | None
        The owner entity UUID, or ``None`` if not found.
    """
    try:
        row = await pool.fetchrow(_OWNER_ID_FROM_ENTITIES_SQL)
        return row["id"] if row else None
    except asyncpg.PostgresError as exc:
        # Catches UndefinedTableError, PostgresConnectionError, etc. so that
        # pre-migration databases (missing public.entities) fail gracefully.
        logger.debug(
            "fetch_owner_entity_id: public.entities query failed (table may not exist yet): %s",
            exc,
        )
        return None


async def resolve_owner_entity_id_two_step(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity UUID via a two-step fallback strategy.

    Primary path
        ``public.contacts JOIN public.entities`` filtered by
        ``'owner' = ANY(e.roles)`` — works for fully-bootstrapped installs where
        the owner entity has a linked contact row.

    Fallback path
        ``public.entities WHERE 'owner' = ANY(roles)`` — works for installs
        where the owner entity was seeded directly (e.g. early bootstrap or
        post-migration state without a contact row yet).

    Returns ``None`` when neither path finds an owner entity.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the shared database.

    Returns
    -------
    uuid.UUID | None
        The owner entity UUID, or ``None`` when the owner cannot be found.
    """
    # Primary path: contacts table with entity_id FK.
    # Note: public.contacts.roles was dropped in core_016; roles are on public.entities.
    row = await pool.fetchrow(
        """
        SELECT e.id
        FROM public.contacts c
        JOIN public.entities e ON c.entity_id = e.id
        WHERE 'owner' = ANY(e.roles)
          AND c.entity_id IS NOT NULL
        LIMIT 1
        """
    )
    if row is not None:
        return row["id"]

    # Fallback: entities with owner role directly (no contact row required).
    row = await pool.fetchrow(_OWNER_ID_FROM_ENTITIES_SQL)
    return row["id"] if row is not None else None


async def resolve_owner_entity(pool: asyncpg.Pool) -> tuple[uuid.UUID, str]:
    """Resolve the owner entity via a two-step fallback strategy.

    Primary path
        ``public.contacts JOIN public.entities`` filtered by
        ``'owner' = ANY(e.roles)`` — works for fully-bootstrapped installs where
        the owner entity has a linked contact row.

    Fallback path
        ``public.entities WHERE 'owner' = ANY(roles)`` — works for installs
        where the owner entity was seeded directly (e.g. early bootstrap or
        post-migration state without a contact row yet).

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the shared database.

    Returns
    -------
    tuple[uuid.UUID, str]
        ``(entity_id, canonical_name)`` for the owner entity.

    Raises
    ------
    ValueError
        When neither path finds an owner entity.  Callers should surface a
        meaningful error to the user (e.g. "owner not bootstrapped").
    """
    # Primary path: contacts table with entity_id FK.
    # Note: public.contacts.roles was dropped in core_016; roles are on public.entities.
    row = await pool.fetchrow(
        """
        SELECT e.id, e.canonical_name
        FROM public.contacts c
        JOIN public.entities e ON c.entity_id = e.id
        WHERE 'owner' = ANY(e.roles)
          AND c.entity_id IS NOT NULL
        LIMIT 1
        """
    )
    if row:
        return row["id"], row["canonical_name"]

    # Fallback: entities with owner role directly (no contact row required).
    row = await pool.fetchrow(_OWNER_FROM_ENTITIES_SQL)
    if row:
        return row["id"], row["canonical_name"]

    raise ValueError(
        "Owner entity could not be resolved. "
        "Ensure the butler has started up successfully (owner entity bootstrap) "
        "or create an owner contact via the identity setup workflow."
    )
