"""Shared owner-entity resolution helpers.

Centralises the repeated ``public.entities WHERE 'owner' = ANY(roles)`` lookup
pattern used across multiple butlers and API routers.  Call sites that
previously duplicated this query now import from here.

Two helpers are provided:

* ``fetch_owner_entity_id`` — simple single-step lookup on ``public.entities``.
  Returns the owner entity UUID or ``None``.  Swallows ``asyncpg.PostgresError``
  so that pre-migration databases (missing ``public.entities``) fail gracefully.

* ``resolve_owner_entity`` — direct ``public.entities WHERE 'owner' = ANY(roles)``
  lookup used by the memory preferences module.  Returns
  ``(entity_id, canonical_name)`` or raises ``ValueError`` when no owner can be
  found.
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
    """Resolve the owner entity UUID directly from ``public.entities``.

    Reads ``public.entities WHERE 'owner' = ANY(roles)`` — roles live on the
    entity (``public.contacts.roles`` was dropped in core_016 and the contact
    object is being retired). Returns ``None`` when no owner entity exists.

    The legacy ``public.contacts JOIN public.entities`` primary path was removed:
    it could only ever match the same owner entity the direct query finds, but
    additionally required a (now-vestigial) contact row to exist.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the shared database.

    Returns
    -------
    uuid.UUID | None
        The owner entity UUID, or ``None`` when the owner cannot be found.
    """
    row = await pool.fetchrow(_OWNER_ID_FROM_ENTITIES_SQL)
    return row["id"] if row is not None else None


async def resolve_owner_entity(pool: asyncpg.Pool) -> tuple[uuid.UUID, str]:
    """Resolve the owner entity directly from ``public.entities``.

    Reads ``public.entities WHERE 'owner' = ANY(roles)`` — roles live on the
    entity (``public.contacts.roles`` was dropped in core_016 and the contact
    object is being retired). The legacy ``public.contacts JOIN public.entities``
    primary path was removed: it could only match the same owner entity this
    query finds, but additionally required a (now-vestigial) contact row.

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
        When no owner entity exists.  Callers should surface a meaningful error
        to the user (e.g. "owner not bootstrapped").
    """
    row = await pool.fetchrow(_OWNER_FROM_ENTITIES_SQL)
    if row:
        return row["id"], row["canonical_name"]

    raise ValueError(
        "Owner entity could not be resolved. "
        "Ensure the butler has started up successfully (owner entity bootstrap) "
        "or create an owner contact via the identity setup workflow."
    )
