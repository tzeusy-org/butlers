"""Search read-model v1 — versioned read boundary for the cross-butler search endpoint.

Centralises the SQL column projections and per-domain query functions for
``GET /api/search``, which fans out ILIKE searches across entities (shared
schema), contacts (shared schema), sessions (per-butler fan-out), and state
(per-butler fan-out).

A breaking schema change (new required column, renamed column, type change)
should produce a new ``search_v2`` module rather than silently altering
this one.

Public surface
--------------
Column constants:
    ENTITY_COLUMNS
    CONTACT_COLUMNS
    ENTITY_FACTS_SNIPPET_COLUMNS
    SESSION_COLUMNS
    STATE_COLUMNS

Row DTOs:
    EntitySearchRow
    ContactSearchRow
    SessionSearchRow
    StateSearchRow

Query functions (all async):
    query_entity_search(pool, pattern, limit) -> list[EntitySearchRow]
    query_contact_search(pool, pattern, limit) -> list[ContactSearchRow]
    query_session_search(db, pattern, limit) -> dict[str, list[SessionSearchRow]]
    query_state_search(db, pattern, limit) -> dict[str, list[StateSearchRow]]

Version marker:
    READ_MODEL_VERSION
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``search_v2`` for breaking changes.
READ_MODEL_VERSION = "search_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Columns projected from ``public.entities`` for the entity search.
ENTITY_COLUMNS: str = "e.id, e.canonical_name, e.entity_type, e.aliases"

#: Columns projected from ``public.contacts`` for the contact search.
CONTACT_COLUMNS: str = "c.id, c.name, c.entity_id"

#: Columns projected from ``relationship.entity_facts`` for contact snippet assembly.
ENTITY_FACTS_SNIPPET_COLUMNS: str = "ef.subject AS entity_id, ef.predicate, ef.object"

#: Columns projected from ``sessions`` (per-butler) for the session search.
SESSION_COLUMNS: str = (
    "id, prompt, result, trigger_source, success, started_at, duration_ms,"
    " CASE WHEN prompt ILIKE $1 THEN 'prompt' ELSE 'result' END AS matched_field"
)

#: Columns projected from ``state`` (per-butler) for the state search.
STATE_COLUMNS: str = (
    "key, value::text AS value_text, updated_at,"
    " CASE WHEN key ILIKE $1 THEN 'key' ELSE 'value' END AS matched_field"
)

# ---------------------------------------------------------------------------
# Typed row DTOs
# ---------------------------------------------------------------------------


@dataclass
class EntitySearchRow:
    """Typed DTO for a ``public.entities`` search result (v1)."""

    id: UUID
    canonical_name: str
    entity_type: str | None
    aliases: list[str]


@dataclass
class ContactSearchRow:
    """Typed DTO for a ``public.contacts`` search result (v1)."""

    id: UUID
    name: str | None
    entity_id: UUID | None
    #: First email address found in ``relationship.entity_facts`` for snippet display.
    email: str | None = None
    #: First phone number found in ``relationship.entity_facts`` for snippet display.
    phone: str | None = None


@dataclass
class SessionSearchRow:
    """Typed DTO for a ``sessions`` search result row (v1)."""

    id: UUID
    prompt: str | None
    result: str | None
    trigger_source: str | None
    success: bool | None
    started_at: datetime
    duration_ms: int | None
    matched_field: str  # 'prompt' | 'result'


@dataclass
class StateSearchRow:
    """Typed DTO for a ``state`` search result row (v1)."""

    key: str
    value_text: str | None
    updated_at: datetime | None
    matched_field: str  # 'key' | 'value'


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def row_to_entity(row: asyncpg.Record) -> EntitySearchRow:
    """Convert an asyncpg Record to an :class:`EntitySearchRow`.

    This is the single place that knows the column names from
    :data:`ENTITY_COLUMNS`.
    """
    aliases = list(row["aliases"]) if row["aliases"] else []
    return EntitySearchRow(
        id=row["id"],
        canonical_name=row["canonical_name"],
        entity_type=row["entity_type"],
        aliases=aliases,
    )


def row_to_contact(row: asyncpg.Record) -> ContactSearchRow:
    """Convert an asyncpg Record to a :class:`ContactSearchRow` (without snippets).

    Snippet fields (``email``, ``phone``) are populated separately by
    :func:`query_contact_search` after the batch entity_facts fetch.
    """
    return ContactSearchRow(
        id=row["id"],
        name=row["name"],
        entity_id=row["entity_id"],
    )


def row_to_session(row: asyncpg.Record) -> SessionSearchRow:
    """Convert an asyncpg Record to a :class:`SessionSearchRow`.

    This is the single place that knows the column names from
    :data:`SESSION_COLUMNS`.
    """
    return SessionSearchRow(
        id=row["id"],
        prompt=row["prompt"],
        result=row["result"],
        trigger_source=row["trigger_source"],
        success=row["success"],
        started_at=row["started_at"],
        duration_ms=row["duration_ms"],
        matched_field=row["matched_field"],
    )


def row_to_state(row: asyncpg.Record) -> StateSearchRow:
    """Convert an asyncpg Record to a :class:`StateSearchRow`.

    This is the single place that knows the column names from
    :data:`STATE_COLUMNS`.
    """
    return StateSearchRow(
        key=row["key"],
        value_text=row["value_text"],
        updated_at=row["updated_at"],
        matched_field=row["matched_field"],
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_entity_search(
    pool: asyncpg.Pool,
    pattern: str,
    limit: int,
) -> list[EntitySearchRow]:
    """Search ``public.entities`` by canonical name or alias.

    Excludes merged and deleted entities.  Results are ordered by
    ``canonical_name``.

    Parameters
    ----------
    pool:
        Any asyncpg pool (shared-schema query; any butler's pool works).
    pattern:
        SQL ILIKE pattern (e.g. ``%foo%``).
    limit:
        Maximum rows to return.

    Returns
    -------
    list[EntitySearchRow]
        Matched entity rows, or an empty list on error.
    """
    try:
        rows = await pool.fetch(
            f"SELECT {ENTITY_COLUMNS}"
            " FROM public.entities e"
            " WHERE (e.metadata->>'merged_into') IS NULL"
            "   AND (e.metadata->>'deleted_at') IS NULL"
            "   AND ("
            "     e.canonical_name ILIKE $1"
            "     OR EXISTS ("
            "       SELECT 1 FROM unnest(e.aliases) AS a WHERE a ILIKE $1"
            "     )"
            "   )"
            " ORDER BY e.canonical_name"
            " LIMIT $2",
            pattern,
            limit,
        )
        return [row_to_entity(r) for r in rows]
    except Exception:
        logger.warning("Entity search failed", exc_info=True)
        return []


async def query_contact_search(
    pool: asyncpg.Pool,
    pattern: str,
    limit: int,
) -> list[ContactSearchRow]:
    """Search ``public.contacts`` by name or entity_facts channel value.

    Two-phase: first fetches matching contacts, then batch-fetches email and
    phone from ``relationship.entity_facts`` for snippet display.

    Parameters
    ----------
    pool:
        Any asyncpg pool (shared-schema query; any butler's pool works).
    pattern:
        SQL ILIKE pattern (e.g. ``%foo%``).
    limit:
        Maximum rows to return.

    Returns
    -------
    list[ContactSearchRow]
        Matched contact rows with ``email``/``phone`` snippet fields populated
        where available, or an empty list on error.
    """
    try:
        contact_rows = await pool.fetch(
            f"""
            SELECT DISTINCT ON (c.id) {CONTACT_COLUMNS}
            FROM public.contacts c
            WHERE c.archived_at IS NULL
              AND (
                c.name ILIKE $1
                OR (
                  c.entity_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1
                    FROM relationship.entity_facts ef
                    WHERE ef.subject     = c.entity_id
                      AND ef.predicate  LIKE 'has-%'
                      AND ef.validity    = 'active'
                      AND ef.object_kind = 'literal'
                      AND ef.object ILIKE $1
                  )
                )
              )
            ORDER BY c.id, c.name
            LIMIT $2
            """,
            pattern,
            limit,
        )

        contacts = [row_to_contact(r) for r in contact_rows]

        # Batch-fetch email and phone snippets from entity_facts.
        entity_ids: list[Any] = list({c.entity_id for c in contacts if c.entity_id is not None})
        email_by_entity: dict[Any, str] = {}
        phone_by_entity: dict[Any, str] = {}
        if entity_ids:
            ef_rows = await pool.fetch(
                f"""
                SELECT {ENTITY_FACTS_SNIPPET_COLUMNS}
                FROM relationship.entity_facts ef
                WHERE ef.subject = ANY($1)
                  AND ef.predicate IN ('has-email', 'has-phone')
                  AND ef.validity    = 'active'
                  AND ef.object_kind = 'literal'
                ORDER BY ef.subject, ef."primary" DESC NULLS LAST, ef.created_at ASC
                """,
                entity_ids,
            )
            for efr in ef_rows:
                eid = efr["entity_id"]
                if efr["predicate"] == "has-email" and eid not in email_by_entity:
                    email_by_entity[eid] = efr["object"]
                elif efr["predicate"] == "has-phone" and eid not in phone_by_entity:
                    phone_by_entity[eid] = efr["object"]

        # Attach snippet data to each contact DTO.
        for contact in contacts:
            eid = contact.entity_id
            if eid is not None:
                contact.email = email_by_entity.get(eid)
                contact.phone = phone_by_entity.get(eid)

        return contacts
    except Exception:
        logger.warning("Contact search failed", exc_info=True)
        return []


async def query_session_search(
    db: DatabaseManager,
    pattern: str,
    limit: int,
) -> dict[str, list[SessionSearchRow]]:
    """Fan-out ILIKE search across all butler ``sessions`` tables.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    pattern:
        SQL ILIKE pattern (e.g. ``%foo%``).
    limit:
        Maximum rows per butler.

    Returns
    -------
    dict[str, list[SessionSearchRow]]
        ``{butler_name: [SessionSearchRow, ...]}`` for each butler that
        responded.  Empty if no butler has data or an error occurred.
    """
    sql = (
        f"SELECT {SESSION_COLUMNS}"
        " FROM sessions"
        " WHERE prompt ILIKE $1 OR result ILIKE $1"
        " ORDER BY started_at DESC"
        " LIMIT $2"
    )
    try:
        raw = await db.fan_out(sql, (pattern, limit))
    except Exception:
        logger.warning("Session search fan-out failed", exc_info=True)
        return {}

    return {butler_name: [row_to_session(r) for r in rows] for butler_name, rows in raw.items()}


async def query_state_search(
    db: DatabaseManager,
    pattern: str,
    limit: int,
) -> dict[str, list[StateSearchRow]]:
    """Fan-out ILIKE search across all butler ``state`` tables.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    pattern:
        SQL ILIKE pattern (e.g. ``%foo%``).
    limit:
        Maximum rows per butler.

    Returns
    -------
    dict[str, list[StateSearchRow]]
        ``{butler_name: [StateSearchRow, ...]}`` for each butler that
        responded.  Empty if no butler has data or an error occurred.
    """
    sql = (
        f"SELECT {STATE_COLUMNS}"
        " FROM state"
        " WHERE key ILIKE $1 OR value::text ILIKE $1"
        " ORDER BY updated_at DESC"
        " LIMIT $2"
    )
    try:
        raw = await db.fan_out(sql, (pattern, limit))
    except Exception:
        logger.warning("State search fan-out failed", exc_info=True)
        return {}

    return {butler_name: [row_to_state(r) for r in rows] for butler_name, rows in raw.items()}
