"""Search read-model v1 — versioned read boundary for the cross-butler search endpoint.

Centralises the SQL column projections and per-domain query functions for
``GET /api/search``, which fans out ILIKE searches across entities (shared
schema), contacts (shared schema), sessions (per-butler fan-out), and state
(per-butler fan-out).

Migration note (bu-tzyuh): the contact search now queries ``public.entities``
directly rather than ``public.contacts``. The ``public.contacts`` table is
intentionally NOT dropped by this change — it remains for other consumers —
but ``query_contact_search`` no longer references it.

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
    query_entity_search(pool, pattern, limit)
        -> tuple[list[EntitySearchRow], list[str]]
    query_contact_search(pool, pattern, limit)
        -> tuple[list[ContactSearchRow], list[str]]
    query_session_search(db, pattern, limit)
        -> tuple[dict[str, list[SessionSearchRow]], list[str]]
    query_state_search(db, pattern, limit)
        -> tuple[dict[str, list[StateSearchRow]], list[str]]

Every query returns a ``(results, degraded_sources)`` tuple so the endpoint
never mistakes a failed source for "no results": the second element names the
sources whose query failed (per-butler names from ``fan_out_with_status`` for
the fan-outs, or the sentinel category name -- ``["entities"]`` / ``["contacts"]``
/ ``["sessions"]`` / ``["state"]`` -- when the source as a whole failed). See
``degraded.py`` and CLAUDE.md "Degraded-Mode Response Envelope".

Classify before flagging: the shared-schema entity/contact queries touch
``public.entities`` and ``relationship.entity_facts``, which are legitimately
absent on a pre-migration DB or a pool that never provisioned them. Such a
missing-schema error (:func:`_is_missing_schema_error`) is NOT a degraded
source -- it yields an empty result with no flag. Only a genuine failure
(dropped connection, timeout, permission) flags the source. ``sessions`` and
``state`` are core tables in every butler schema, so their fan-outs have no
such exemption (any failure is genuine).

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
from asyncpg.exceptions import UndefinedTableError

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

#: Columns projected from ``public.entities`` for the contact search (bu-tzyuh).
#: After bu-tzyuh, ``public.contacts`` is no longer referenced here; both ``id``
#: and ``entity_id`` hold the entity UUID.
CONTACT_COLUMNS: str = "e.id, e.canonical_name AS name, e.id AS entity_id"

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
    """Typed DTO for a ``public.entities`` contact-search result (v1).

    After bu-tzyuh, both ``id`` and ``entity_id`` hold the entity UUID.
    """

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

    After bu-tzyuh rows come from ``public.entities``; both ``id`` and
    ``entity_id`` map to the entity UUID.

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


def _is_missing_schema_error(exc: BaseException) -> bool:
    """Return whether *exc* means the queried table/schema simply does not exist.

    Legitimately absent -- a pre-migration DB, or a pool whose schema was never
    provisioned (a butler without the relationship module) -- NOT a degraded
    source. Mirrors ``memory.py::_is_missing_memory_schema_error`` (extended to
    a missing schema as well as a missing relation). Any OTHER error (a dropped
    connection, a timeout, a permission error, a malformed query) is a genuine
    failure and MUST be flagged as degraded, never folded into the same
    "no such table" skip.
    """
    if isinstance(exc, UndefinedTableError):
        return True
    msg = str(exc).lower()
    return "does not exist" in msg and ("relation" in msg or "table" in msg or "schema" in msg)


async def query_entity_search(
    pool: asyncpg.Pool,
    pattern: str,
    limit: int,
) -> tuple[list[EntitySearchRow], list[str]]:
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
    tuple[list[EntitySearchRow], list[str]]
        ``(rows, degraded_sources)``.  ``rows`` is the matched entities (empty
        on error).  ``degraded_sources`` is ``["entities"]`` when the query
        failed for a genuine reason (transport/permission), or ``[]`` on success
        OR when ``public.entities`` is legitimately absent
        (:func:`_is_missing_schema_error`) -- so a missing schema never reads as
        a degraded source (classify before flagging).
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
        return [row_to_entity(r) for r in rows], []
    except Exception as exc:
        if _is_missing_schema_error(exc):
            logger.debug(
                "Entity search: entities schema absent (legitimately absent, not degraded)"
            )
            return [], []
        logger.warning("Entity search failed", exc_info=True)
        return [], ["entities"]


async def query_contact_search(
    pool: asyncpg.Pool,
    pattern: str,
    limit: int,
) -> tuple[list[ContactSearchRow], list[str]]:
    """Search ``public.entities`` by canonical name, alias, or entity_facts channel value.

    After bu-tzyuh this function no longer references ``public.contacts``;
    it queries ``public.entities`` directly and assembles contact snippets
    from ``relationship.entity_facts``.

    Two-phase: first fetches matching entities, then batch-fetches email and
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
    tuple[list[ContactSearchRow], list[str]]
        ``(rows, degraded_sources)``.  ``rows`` is the matched contacts with
        ``email``/``phone`` snippets (empty on error).  ``degraded_sources`` is
        ``["contacts"]`` when the query failed for a genuine reason, or ``[]`` on
        success OR when ``public.entities`` / ``relationship.entity_facts`` is
        legitimately absent (:func:`_is_missing_schema_error`) -- classify
        before flagging.
    """
    try:
        entity_rows = await pool.fetch(
            f"""
            SELECT {CONTACT_COLUMNS}
            FROM public.entities e
            WHERE e.entity_type = 'person'
              AND (e.metadata->>'merged_into') IS NULL
              AND (e.metadata->>'deleted_at') IS NULL
              AND (
                e.canonical_name ILIKE $1
                OR EXISTS (
                  SELECT 1 FROM unnest(e.aliases) AS a WHERE a ILIKE $1
                )
                OR EXISTS (
                  SELECT 1
                  FROM relationship.entity_facts ef
                  WHERE ef.subject     = e.id
                    AND ef.predicate  LIKE 'has-%'
                    AND ef.validity    = 'active'
                    AND ef.object_kind = 'literal'
                    AND ef.object ILIKE $1
                )
              )
            ORDER BY e.canonical_name
            LIMIT $2
            """,
            pattern,
            limit,
        )
        contacts = [row_to_contact(r) for r in entity_rows]
        # Batch-fetch email/phone snippets from entity_facts
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
        for contact in contacts:
            eid = contact.entity_id
            if eid is not None:
                contact.email = email_by_entity.get(eid)
                contact.phone = phone_by_entity.get(eid)
        return contacts, []
    except Exception as exc:
        if _is_missing_schema_error(exc):
            logger.debug(
                "Contact search: entities/entity_facts schema absent "
                "(legitimately absent, not degraded)"
            )
            return [], []
        logger.warning("Contact search failed", exc_info=True)
        return [], ["contacts"]


async def query_session_search(
    db: DatabaseManager,
    pattern: str,
    limit: int,
) -> tuple[dict[str, list[SessionSearchRow]], list[str]]:
    """Fan-out ILIKE search across all butler ``sessions`` tables.

    ``sessions`` is a core table present in every butler schema, so any
    per-butler fan-out failure is a genuine transport/permission error, never
    a legitimately-absent schema — there is no "classify before flagging"
    exemption here (contrast the memory-module reads that must skip absent
    tables).  Every failed source is therefore reported as degraded.

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
    tuple[dict[str, list[SessionSearchRow]], list[str]]
        ``({butler_name: [SessionSearchRow, ...]}, degraded_sources)``.  The
        first element holds rows for every butler that responded (empty list
        on a per-butler failure).  ``degraded_sources`` names the sources
        whose query failed: the per-butler names from
        :meth:`~butlers.api.db.DatabaseManager.fan_out_with_status` on the
        normal path, or the single sentinel ``["sessions"]`` when the whole
        fan-out raised structurally (so a zero-result response can never read
        as a clean "no results").
    """
    sql = (
        f"SELECT {SESSION_COLUMNS}"
        " FROM sessions"
        " WHERE prompt ILIKE $1 OR result ILIKE $1"
        " ORDER BY started_at DESC"
        " LIMIT $2"
    )
    try:
        raw, failed = await db.fan_out_with_status(sql, (pattern, limit))
    except Exception:
        # Structural fan-out failure (not a single butler): we cannot tell
        # which butlers have data, so flag the whole source degraded rather
        # than swallow into a deceptive empty result. Keeps the always-200
        # contract while refusing to fabricate calm.
        logger.warning("Session search fan-out failed", exc_info=True)
        return {}, ["sessions"]

    mapped = {butler_name: [row_to_session(r) for r in rows] for butler_name, rows in raw.items()}
    return mapped, failed


async def query_state_search(
    db: DatabaseManager,
    pattern: str,
    limit: int,
) -> tuple[dict[str, list[StateSearchRow]], list[str]]:
    """Fan-out ILIKE search across all butler ``state`` tables.

    ``state`` is a core table present in every butler schema, so any
    per-butler fan-out failure is a genuine transport/permission error, never
    a legitimately-absent schema — every failed source is reported as
    degraded (see :func:`query_session_search`).

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
    tuple[dict[str, list[StateSearchRow]], list[str]]
        ``({butler_name: [StateSearchRow, ...]}, degraded_sources)``.  The
        first element holds rows for every butler that responded (empty list
        on a per-butler failure).  ``degraded_sources`` names the per-butler
        sources that failed, or the sentinel ``["state"]`` when the whole
        fan-out raised structurally.
    """
    sql = (
        f"SELECT {STATE_COLUMNS}"
        " FROM state"
        " WHERE key ILIKE $1 OR value::text ILIKE $1"
        " ORDER BY updated_at DESC"
        " LIMIT $2"
    )
    try:
        raw, failed = await db.fan_out_with_status(sql, (pattern, limit))
    except Exception:
        logger.warning("State search fan-out failed", exc_info=True)
        return {}, ["state"]

    mapped = {butler_name: [row_to_state(r) for r in rows] for butler_name, rows in raw.items()}
    return mapped, failed
