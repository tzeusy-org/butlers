"""Priority contacts API — CRUD over public.priority_contacts.

Provides:

- ``router`` — endpoints under ``/api/ingestion/priority-contacts``

Endpoints
---------
GET    /api/ingestion/priority-contacts                       — list (optional ?butler=)
POST   /api/ingestion/priority-contacts                       — add assignment (201)
DELETE /api/ingestion/priority-contacts/{contact_id}/{butler} — remove assignment (204)

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/ingestion-priority-contacts/
§Requirement: Priority contacts REST API
§Requirement: Audit emission for priority contact mutations
§Requirement: No credentials in priority-contact API responses
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.api.models.ingestion_event import (
    PriorityContactAddRequest,
    PriorityContactAddResponse,
    PriorityContactEntry,
)
from butlers.api.routers.audit import append as _audit_append

# ---------------------------------------------------------------------------
# entity_facts helpers (bu-hjo3i — replaces public.contact_info reads)
# ---------------------------------------------------------------------------

_TELEGRAM_HANDLE_PREFIX = "telegram:"


def _ef_display_value(predicate: str, obj: str) -> str:
    """Strip the ``telegram:`` prefix for display; passthrough for all others."""
    if predicate == "has-handle" and obj.startswith(_TELEGRAM_HANDLE_PREFIX):
        return obj[len(_TELEGRAM_HANDLE_PREFIX) :]
    return obj


class _EntityFactsResult:
    """Combined result of a batch entity_facts fetch.

    Attributes
    ----------
    values:
        Map of contact_id → list of display-ready channel value strings.
    has_email:
        Set of contact_ids that have at least one active ``has-email`` fact.
        The Gmail policy evaluator only resolves priority senders via ``has-email``
        triples; contacts absent from this set will silently match nothing.
    """

    __slots__ = ("values", "has_email")

    def __init__(self) -> None:
        self.values: dict[UUID, list[str]] = {}
        self.has_email: set[UUID] = set()


async def _entity_facts_values_by_contact(
    pool: object,
    contact_entity_pairs: list[tuple[UUID, UUID]],
) -> _EntityFactsResult:
    """Batch-fetch active channel values from relationship.entity_facts.

    Parameters
    ----------
    pool:
        asyncpg connection pool with access to relationship.entity_facts.
    contact_entity_pairs:
        List of ``(contact_id, entity_id)`` pairs for the contacts to look up.

    Returns
    -------
    ``_EntityFactsResult`` with:
    - ``values``: contact_id → list of display-ready channel value strings.
      Contacts with no linked entity or no facts map to an empty list.
    - ``has_email``: set of contact_ids that have at least one active
      ``has-email`` fact (the predicate the Gmail policy evaluator uses).
    """
    out = _EntityFactsResult()
    if not contact_entity_pairs:
        return out

    entity_to_contacts: dict[UUID, list[UUID]] = {}
    for cid, eid in contact_entity_pairs:
        out.values[cid] = []
        entity_to_contacts.setdefault(eid, []).append(cid)
    entity_ids = list(entity_to_contacts)

    try:
        rows = await pool.fetch(
            """
            SELECT ef.subject AS entity_id, ef.predicate, ef.object
            FROM relationship.entity_facts ef
            WHERE ef.subject = ANY($1)
              AND ef.predicate LIKE 'has-%'
              AND ef.validity = 'active'
              AND ef.object_kind = 'literal'
            ORDER BY ef.subject, ef."primary" DESC NULLS LAST, ef.created_at ASC
            """,
            entity_ids,
        )
    except Exception:  # noqa: BLE001
        return out

    for r in rows:
        eid = r["entity_id"]
        cids = entity_to_contacts.get(eid)
        if not cids:
            continue
        display_val = _ef_display_value(r["predicate"], r["object"])
        for cid in cids:
            out.values[cid].append(display_val)
            if r["predicate"] == "has-email":
                out.has_email.add(cid)
    return out


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/priority-contacts", tags=["ingestion"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# GET /api/ingestion/priority-contacts
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[PriorityContactEntry])
async def list_priority_contacts(
    butler: str | None = Query(None, description="Filter by butler name"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[PriorityContactEntry]:
    """List priority contact assignments, optionally filtered by butler.

    Joins through public.contacts for canonical contact name.  Channel
    identifiers (email, phone, handles) are fetched from
    relationship.entity_facts via the contact's linked entity (bu-hjo3i —
    replaces the old public.contact_info LEFT JOIN).

    Returns paginated list of priority contact entries.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if butler is not None:
        conditions.append(f"pc.butler = ${idx}")
        args.append(butler)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT count(*) FROM public.priority_contacts pc{where_clause}"
    total = await pool.fetchval(count_sql, *args) or 0

    # Main query: join contacts for name; channel identifiers fetched separately
    # from relationship.entity_facts (bu-hjo3i).
    data_sql = f"""
        SELECT
            pc.contact_id,
            pc.butler,
            pc.added_at,
            pc.added_by,
            c.name    AS contact_name,
            c.entity_id AS entity_id
        FROM public.priority_contacts pc
        LEFT JOIN public.contacts c ON c.id = pc.contact_id
        {where_clause}
        ORDER BY pc.added_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
    """
    args.extend([offset, limit])

    rows = await pool.fetch(data_sql, *args)

    # Batch-fetch channel values from relationship.entity_facts.
    contact_entity_pairs: list[tuple[UUID, UUID]] = [
        (row["contact_id"], row["entity_id"]) for row in rows if row["entity_id"] is not None
    ]
    ef_result = await _entity_facts_values_by_contact(pool, contact_entity_pairs)

    entries = [
        PriorityContactEntry(
            contact_id=row["contact_id"],
            butler=row["butler"],
            added_at=row["added_at"],
            added_by=row["added_by"],
            name=row["contact_name"],
            contact_info_values=ef_result.values.get(row["contact_id"], []),
            # A contact is inert when it would silently match nothing at runtime.
            # For Gmail: the 3-hop join requires both a linked entity_id and an
            # active has-email fact; either absence means the row matches nothing.
            # For other butlers: only a missing entity_id makes the row inert
            # (they use has-handle or other predicates, not has-email).
            is_inert=(
                row["entity_id"] is None
                or (row["butler"] == "gmail" and row["contact_id"] not in ef_result.has_email)
            ),
        )
        for row in rows
    ]

    return PaginatedResponse[PriorityContactEntry](
        data=entries,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/priority-contacts
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PriorityContactAddResponse)
async def add_priority_contact(
    body: PriorityContactAddRequest,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> PriorityContactAddResponse:
    """Add a priority contact assignment for a butler.

    Rejects payloads that include a ``roles`` field — role mutations
    are the sole responsibility of PATCH /api/contacts.

    Emits an audit entry with action='ingestion.priority_contact.add' on success.

    Returns HTTP 201 on success.
    Returns HTTP 400 if the contact_id does not exist in public.contacts.
    Returns HTTP 409 if the (contact_id, butler) pair already exists.
    """
    # Reject any payload that includes a 'roles' field.
    # Role mutations belong at PATCH /api/contacts — not here.
    try:
        import json as _json

        raw = await request.body()
        raw_obj = _json.loads(raw) if raw else {}
        if isinstance(raw_obj, dict) and "roles" in raw_obj:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Role mutations are not accepted here. "
                    "Use PATCH /api/contacts to update contact roles."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Body re-parse is best-effort; Pydantic already validated the required fields.

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # Verify the contact exists
    contact_exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.contacts WHERE id = $1)",
        body.contact_id,
    )
    if not contact_exists:
        raise HTTPException(
            status_code=400,
            detail=f"Contact '{body.contact_id}' not found in public.contacts",
        )

    # Insert — conflict on PK raises HTTP 409
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.priority_contacts (contact_id, butler, added_by)
            VALUES ($1, $2, $3)
            RETURNING contact_id, butler, added_at, added_by
            """,
            body.contact_id,
            body.butler,
            "dashboard",
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Priority contact ({body.contact_id}, {body.butler}) already exists",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to insert priority contact") from exc

    # Emit audit entry
    client_host = getattr(request.client, "host", None) if request.client else None
    try:
        await _audit_append(
            pool,
            actor="dashboard",
            action="ingestion.priority_contact.add",
            target=f"{body.contact_id}:{body.butler}",
            note=f"Added priority contact for butler '{body.butler}'",
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "priority_contacts: failed to append audit_log entry for add %s/%s",
            body.contact_id,
            body.butler,
            exc_info=True,
        )

    return PriorityContactAddResponse(
        contact_id=row["contact_id"],
        butler=row["butler"],
        added_at=row["added_at"],
        added_by=row["added_by"],
    )


# ---------------------------------------------------------------------------
# DELETE /api/ingestion/priority-contacts/{contact_id}/{butler}
# ---------------------------------------------------------------------------


@router.delete(
    "/{contact_id}/{butler}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_priority_contact(
    contact_id: UUID,
    butler: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Remove a priority contact assignment.

    Emits an audit entry with action='ingestion.priority_contact.remove' on success.

    Returns HTTP 204 on success.
    Returns HTTP 404 if the (contact_id, butler) pair does not exist.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    result = await pool.execute(
        "DELETE FROM public.priority_contacts WHERE contact_id = $1 AND butler = $2",
        contact_id,
        butler,
    )

    # asyncpg execute returns a status string like "DELETE 1" or "DELETE 0"
    deleted_count = int(result.split()[-1]) if result else 0
    if deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Priority contact ({contact_id}, {butler}) not found",
        )

    # Emit audit entry
    client_host = getattr(request.client, "host", None) if request.client else None
    try:
        await _audit_append(
            pool,
            actor="dashboard",
            action="ingestion.priority_contact.remove",
            target=f"{contact_id}:{butler}",
            note=f"Removed priority contact for butler '{butler}'",
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "priority_contacts: failed to append audit_log entry for remove %s/%s",
            contact_id,
            butler,
            exc_info=True,
        )
