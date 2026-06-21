"""Contacts identity API — read-only typeahead search over person entities.

Provides:

- ``router`` — endpoints under ``/api/contacts``

Endpoints
---------
GET /api/contacts/search?q= — typeahead search returning person entities

The search reads the cross-butler identity layer. Person entities live in
``public.entities``; their NON-secret channel identifiers (email, phone, website,
handle) live as active ``has-*`` triples in ``relationship.entity_facts`` keyed
by ``subject = entities.id`` (the retired ``public.contacts`` /
``public.contact_info`` tables were dropped in core_134 / core_115 and
re-pointed here — see ``tests/contracts/test_contacts_schema_retired.py``).
Secret credentials live separately in ``public.entity_info`` (``secured = true``)
and are NEVER read by this endpoint, so secured values are never searched nor
returned.

Matching is deterministic ``ILIKE`` only — no LLM, no embeddings.

Spec: openspec/specs/contacts-identity/spec.md
§Requirement: Contact search endpoint for typeahead
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

_TELEGRAM_HANDLE_PREFIX = "telegram:"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MatchedIdentifier(BaseModel):
    """A non-secret channel identifier that matched the query.

    Surfaced so the frontend can render the matched identifier (email, phone,
    website, handle) on the contact chip. Only ever sourced from active
    ``has-*`` triples in ``relationship.entity_facts`` — the non-secret
    identifier store. Secret values (``public.entity_info`` with
    ``secured = true``) are never searched nor returned.
    """

    type: str = Field(..., description="Identifier kind, e.g. 'email', 'phone', 'handle'")
    value: str = Field(..., description="The non-secret identifier value that matched")


class ContactSearchResult(BaseModel):
    """A single person-entity match for the typeahead."""

    entity_id: str = Field(..., description="public.entities.id (UUID as string)")
    canonical_name: str = Field(..., description="public.entities.canonical_name")
    matched_identifier: MatchedIdentifier | None = Field(
        None,
        description=(
            "The non-secret identifier that triggered the match, or null when the "
            "entity matched by name/alias only."
        ),
    )


class ContactSearchResponse(BaseModel):
    """Envelope for contact typeahead search results."""

    results: list[ContactSearchResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ilike_pattern(q: str) -> str:
    """Build a substring ILIKE pattern, escaping LIKE wildcards in the query.

    Postgres LIKE/ILIKE treats ``\\`` as the default escape character, so a
    backslash-escaped ``%`` / ``_`` matches the literal character. Escaping the
    user's input means a typed ``%`` or ``_`` searches literally rather than
    acting as a wildcard.
    """
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _identifier_type(predicate: str) -> str:
    """Map a ``has-*`` predicate to the chip identifier kind (strip ``has-``)."""
    return predicate[len("has-") :] if predicate.startswith("has-") else predicate


def _identifier_display_value(predicate: str, obj: str) -> str:
    """Strip the ``telegram:`` storage prefix from handles; passthrough otherwise."""
    if predicate == "has-handle" and obj.startswith(_TELEGRAM_HANDLE_PREFIX):
        return obj[len(_TELEGRAM_HANDLE_PREFIX) :]
    return obj


# Person entities matching the query by canonical_name or an alias. Excludes
# organizations/places (entity_type filter) and tombstoned entities (merged or
# soft-deleted).
_NAME_MATCH_SQL = """
    SELECT e.id AS entity_id, e.canonical_name AS canonical_name
    FROM public.entities e
    WHERE e.entity_type = 'person'
      AND (e.metadata->>'merged_into') IS NULL
      AND (e.metadata->>'deleted_at') IS NULL
      AND (
            e.canonical_name ILIKE $1
            OR EXISTS (SELECT 1 FROM unnest(e.aliases) AS alias WHERE alias ILIKE $1)
          )
    ORDER BY lower(e.canonical_name), e.id
    LIMIT $2
"""

# Person entities matching the query by a NON-secret channel identifier — an
# active ``has-*`` literal triple in relationship.entity_facts. Joins back to the
# person entity and surfaces the matched identifier (predicate + object) for chip
# rendering. DISTINCT ON collapses multiple matching identifiers per entity,
# preferring the primary-of-kind. Secret values live in public.entity_info and
# are never touched here, so they cannot be searched or returned.
_IDENTIFIER_MATCH_SQL = """
    SELECT DISTINCT ON (e.id)
        e.id             AS entity_id,
        e.canonical_name AS canonical_name,
        ef.predicate     AS matched_predicate,
        ef.object        AS matched_value
    FROM relationship.entity_facts ef
    JOIN public.entities e ON e.id = ef.subject
    WHERE ef.object_kind = 'literal'
      AND ef.validity = 'active'
      AND ef.predicate LIKE 'has-%'
      AND ef.object ILIKE $1
      AND e.entity_type = 'person'
      AND (e.metadata->>'merged_into') IS NULL
      AND (e.metadata->>'deleted_at') IS NULL
    ORDER BY e.id, ef."primary" DESC NULLS LAST, ef.object
    LIMIT $2
"""


# ---------------------------------------------------------------------------
# GET /api/contacts/search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=ContactSearchResponse)
async def search_contacts(
    q: str = Query("", description="Typeahead query; matched case-insensitively"),
    limit: int = Query(20, ge=1, le=50, description="Max results to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactSearchResponse:
    """Search known person entities for a contact typeahead.

    Returns person entities (``entity_type='person'``, excluding merged and
    soft-deleted entities) from the identity layer whose ``canonical_name``, an
    alias, or a NON-secret channel identifier (active ``has-*`` triple in
    ``relationship.entity_facts``) matches ``q`` — deterministic ``ILIKE``
    substring matching, no LLM/embedding. When the match came through an
    identifier, the matched identifier (type + value) is surfaced for chip
    rendering. Organizations and places are never returned.

    A blank/whitespace ``q`` and a ``q`` with no matches both return HTTP 200
    with an empty list — never an error. Secret credentials (``public.entity_info``
    with ``secured = true``) are never searched nor returned.
    """
    if not q or not q.strip():
        return ContactSearchResponse(results=[])

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    pattern = _ilike_pattern(q.strip())

    # Name/alias matches over public.entities (always available).
    name_rows = await pool.fetch(_NAME_MATCH_SQL, pattern, limit)

    # Non-secret identifier matches over relationship.entity_facts. Degrade
    # gracefully (name/alias matches only) if the relationship schema is not
    # present — mirrors the resilience of the priority-contacts reader.
    try:
        id_rows = await pool.fetch(_IDENTIFIER_MATCH_SQL, pattern, limit)
    except Exception:  # noqa: BLE001
        logger.debug(
            "contacts.search: entity_facts identifier lookup failed "
            "(relationship schema may be absent); name/alias matches only",
            exc_info=True,
        )
        id_rows = []

    # Merge: dedupe by entity_id. A name match carries no matched_identifier; an
    # identifier match surfaces the matched identifier (and upgrades a name-only
    # entry that also has a matching identifier).
    merged: dict[str, ContactSearchResult] = {}
    for row in name_rows:
        eid = str(row["entity_id"])
        merged[eid] = ContactSearchResult(
            entity_id=eid,
            canonical_name=row["canonical_name"],
            matched_identifier=None,
        )
    for row in id_rows:
        eid = str(row["entity_id"])
        identifier = MatchedIdentifier(
            type=_identifier_type(row["matched_predicate"]),
            value=_identifier_display_value(row["matched_predicate"], row["matched_value"]),
        )
        existing = merged.get(eid)
        if existing is None:
            merged[eid] = ContactSearchResult(
                entity_id=eid,
                canonical_name=row["canonical_name"],
                matched_identifier=identifier,
            )
        elif existing.matched_identifier is None:
            existing.matched_identifier = identifier

    results = sorted(merged.values(), key=lambda r: (r.canonical_name.casefold(), r.entity_id))
    return ContactSearchResponse(results=results[:limit])
