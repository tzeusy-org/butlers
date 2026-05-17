"""Ingestion event endpoints — unified timeline over the ingestion event registry.

Provides:

- ``router`` — endpoints under ``/api/ingestion/events``

Endpoints
---------
GET  /api/ingestion/events               — cursor-paginated unified timeline
GET  /api/ingestion/events/{requestId}   — single event detail
GET  /api/ingestion/events/{requestId}/sessions  — cross-butler lineage
GET  /api/ingestion/events/{requestId}/rollup    — token/cost/butler topology
POST /api/ingestion/events/{id}/replay   — request replay of a filtered event
GET  /api/ingestion/events/{id}/replays  — replay attempt history from public.audit_log
GET  /api/ingestion/events/{id}/sender-contact  — resolve sender_identity to contact name
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.models import ApiResponse, CursorPaginatedResponse, CursorPaginationMeta
from butlers.api.models.ingestion_event import (
    IngestionEventDetail,
    IngestionEventRollup,
    IngestionEventSession,
    IngestionEventSummary,
    ReplayHistoryEntry,
    SenderContactResolution,
)
from butlers.api.pricing import PricingConfig
from butlers.api.routers.audit import append as _audit_append
from butlers.core.ingestion_events import (
    ingestion_event_get,
    ingestion_event_get_inbox_lifecycle,
    ingestion_event_replay_history,
    ingestion_event_replay_request,
    ingestion_event_rollup,
    ingestion_event_sessions,
    ingestion_events_list,
)
from butlers.identity import resolve_contact_by_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/events", tags=["ingestion"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# GET /api/ingestion/events
# ---------------------------------------------------------------------------


@router.get("", response_model=CursorPaginatedResponse[IngestionEventSummary])
async def list_ingestion_events(
    limit: int = Query(20, ge=1, le=200, description="Max records to return"),
    cursor: str | None = Query(
        None,
        description=(
            "Opaque cursor from the previous page's ``next_cursor`` field. "
            "Omit to fetch the first page."
        ),
    ),
    source_channel: str | None = Query(None, description="Filter by source channel"),
    status: Literal[
        "ingested",
        "failed",
        "filtered",
        "error",
        "replay_pending",
        "replay_complete",
        "replay_failed",
    ]
    | None = Query(
        None,
        description=(
            "Filter by event status. 'ingested'/'failed'/'replay_failed' query "
            "public.ingestion_events; 'filtered'/'error'/'replay_complete' query "
            "connectors.filtered_events; 'replay_pending'/'replay_failed' may "
            "appear in both tables. Omit for unified stream."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> CursorPaginatedResponse[IngestionEventSummary]:
    """Return a cursor-paginated unified timeline of ingestion events, newest first.

    Uses keyset (cursor) pagination via ``(received_at DESC, id DESC)`` — no ``total``
    count is computed per request.  Pass the ``next_cursor`` from a previous response
    as the ``cursor`` query param to fetch the next page.

    Merges ``public.ingestion_events`` (status=ingested, filter_reason=null) with
    ``connectors.filtered_events`` (status/filter_reason from their own columns).
    Supports optional filtering by ``source_channel`` and ``status``.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    if cursor is not None:
        try:
            from butlers.core.ingestion_events import decode_cursor

            decode_cursor(cursor)  # Validate the cursor early; raises ValueError if malformed.
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid cursor: {exc}") from exc

    result = await ingestion_events_list(
        pool, limit=limit, cursor=cursor, source_channel=source_channel, status=status
    )

    summaries = [IngestionEventSummary(**row) for row in result["items"]]

    return CursorPaginatedResponse[IngestionEventSummary](
        data=summaries,
        meta=CursorPaginationMeta(
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}
# ---------------------------------------------------------------------------


@router.get("/{request_id}", response_model=ApiResponse[IngestionEventDetail])
async def get_ingestion_event(
    request_id: str,
    request: Request,
    include: list[str] = Query(
        default=[],
        description=(
            "Optional fields to include in the response. "
            "Pass ``include=decomposition`` to include ``decomposition_output`` "
            "(LLM classification output derived from inbound message content). "
            "Omitting this flag returns only metadata fields."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[IngestionEventDetail]:
    """Return a single ingestion event by its UUID.

    Returns 404 when no event with that ``request_id`` exists.

    By default, ``decomposition_output`` is **omitted** from the response to
    avoid inadvertently disclosing inbound message content (PII / user data).
    Pass ``?include=decomposition`` to opt in; doing so emits an additional
    audit log entry with ``reason='decomposition_disclosed'``.

    The ``lifecycle_state`` field is sourced from ``message_inbox``
    (switchboard schema) when the switchboard pool is registered.  If the
    switchboard pool is unavailable or the ``message_inbox`` row has been
    pruned, both lifecycle fields are ``null``.
    """
    request_path = f"/api/ingestion/events/{request_id}"
    include_decomposition = "decomposition" in include

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    try:
        event = await ingestion_event_get(pool, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request_id: {exc}") from exc

    if event is None:
        raise HTTPException(status_code=404, detail=f"Ingestion event '{request_id}' not found")

    # Emit audit log BEFORE returning the payload (fail-closed: auditing is
    # recorded before any PII-bearing data leaves the server).
    audit_reason = "decomposition_disclosed" if include_decomposition else "detail_view"
    await emit_dashboard_audit(
        db,
        butler="switchboard",
        operation="ingestion.event.payload_fetch",
        method="GET",
        path=request_path,
        path_params={"request_id": request_id},
        body={"reason": audit_reason},
        response_status=200,
        request=request,
    )

    # Augment with lifecycle fields from message_inbox (switchboard schema).
    # Best-effort: if the switchboard pool is not registered or the inbox row
    # has been pruned, lifecycle fields remain None.
    try:
        switchboard_pool = db.pool("switchboard")
        inbox_lifecycle = await ingestion_event_get_inbox_lifecycle(switchboard_pool, request_id)
        if inbox_lifecycle is not None:
            event.update(inbox_lifecycle)
    except (KeyError, Exception):
        # KeyError → switchboard pool not registered; other exceptions → DB error.
        # Both are non-fatal: lifecycle fields default to None.
        logger.debug(
            "Could not fetch message_inbox lifecycle for %s "
            "(switchboard pool unavailable or row pruned)",
            request_id,
        )

    detail = IngestionEventDetail(**event)
    # Gate decomposition_output: strip it unless the caller explicitly opted in.
    if not include_decomposition:
        detail.decomposition_output = None

    return ApiResponse[IngestionEventDetail](data=detail)


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}/sessions
# ---------------------------------------------------------------------------


@router.get("/{request_id}/sessions", response_model=ApiResponse[list[IngestionEventSession]])
async def get_ingestion_event_sessions(
    request_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[IngestionEventSession]]:
    """Return cross-butler sessions linked to this ingestion event.

    Fans out to all registered butler databases concurrently and collects
    sessions whose ``request_id`` matches.  Results are sorted by
    ``started_at`` ascending so the lineage reads chronologically.
    """
    sessions_data = await ingestion_event_sessions(db, request_id)
    sessions = [IngestionEventSession(**s) for s in sessions_data]
    return ApiResponse[list[IngestionEventSession]](data=sessions)


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}/rollup
# ---------------------------------------------------------------------------


@router.get("/{request_id}/rollup", response_model=ApiResponse[IngestionEventRollup])
async def get_ingestion_event_rollup(
    request_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[IngestionEventRollup]:
    """Return aggregated token and cost totals for this ingestion event.

    Fetches the cross-butler session lineage, then aggregates input/output
    token counts and USD costs broken down by butler.  Costs are estimated
    from token counts and model via the pricing config.
    """
    sessions_data = await ingestion_event_sessions(db, request_id)
    rollup_data = ingestion_event_rollup(request_id, sessions_data, pricing=pricing)
    return ApiResponse[IngestionEventRollup](data=IngestionEventRollup(**rollup_data))


# ---------------------------------------------------------------------------
# POST /api/ingestion/events/{id}/replay
# ---------------------------------------------------------------------------


@router.post("/{event_id}/replay")
async def replay_ingestion_event(
    event_id: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """Request replay of a failed or filtered event.

    Checks ``public.ingestion_events`` first (for routing-failed events with
    status ``'failed'``), then falls back to ``connectors.filtered_events``
    (for events with status ``filtered``, ``error``, or ``replay_failed``).

    Appends an entry to ``public.audit_log`` with ``action='ingestion.event.replay'``
    and ``target=<event_id>`` on success.

    Returns:
        200 — ``{"status": "replay_pending", "id": "<uuid>"}``
        404 — event not found in either table
        409 — event exists but is not in a replayable state
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # Obtain the switchboard pool for resetting message_inbox on replay.
    switchboard_pool = None
    try:
        switchboard_pool = db.pool("switchboard")
    except (KeyError, Exception):
        pass  # Non-fatal: replay of ingested events will log a warning.

    try:
        result = await ingestion_event_replay_request(
            pool, event_id, switchboard_pool=switchboard_pool
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid event_id: {exc}") from exc

    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

    if result["outcome"] == "conflict":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Event is not replayable",
                "current_status": result["current_status"],
            },
        )

    # Record the replay request in public.audit_log.
    # Note field stores JSON payload for the replay-history endpoint to read back.
    actor = "dashboard"
    client_host = getattr(request.client, "host", None) if request.client else None
    try:
        await _audit_append(
            pool,
            actor=actor,
            action="ingestion.event.replay",
            target=str(result["id"]),
            note=json.dumps({"result": "pending", "source": result.get("source")}),
            ip=client_host,
        )
    except Exception:
        # Audit failure is non-fatal — the replay has already been queued.
        logger.warning(
            "replay: failed to append audit_log entry for event %s",
            event_id,
            exc_info=True,
        )

    return {"status": "replay_pending", "id": result["id"]}


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{id}/replays
# ---------------------------------------------------------------------------


@router.get("/{event_id}/replays", response_model=ApiResponse[list[ReplayHistoryEntry]])
async def get_ingestion_event_replays(
    event_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ReplayHistoryEntry]]:
    """Return the replay attempt history for an ingestion event.

    Queries ``public.audit_log`` for entries with
    ``action='ingestion.event.replay'`` and ``target=<event_id>``,
    returned in chronological order (oldest first).

    Only metadata is returned — no raw event payload or PII.

    Returns:
        200 — list of replay history entries (may be empty)
        503 — shared database pool unavailable
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    entries = await ingestion_event_replay_history(pool, event_id)
    return ApiResponse[list[ReplayHistoryEntry]](data=[ReplayHistoryEntry(**e) for e in entries])


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{id}/sender-contact
# ---------------------------------------------------------------------------


@router.get("/{event_id}/sender-contact", response_model=ApiResponse[SenderContactResolution])
async def get_ingestion_event_sender_contact(
    event_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SenderContactResolution]:
    """Resolve the sender_identity for an ingestion event to a contact name.

    Fetches the event to obtain ``source_channel`` and ``source_sender_identity``,
    then calls ``resolve_contact_by_channel`` against ``public.contacts`` /
    ``public.contact_info``.

    Always returns 200; ``resolved=False`` when no contact is found or when
    resolution fails (fail-open, no error toast on the frontend).

    Returns:
        200 — ``{resolved, name, raw}``
        404 — event not found
        503 — shared database pool unavailable
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    try:
        event = await ingestion_event_get(pool, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid event_id: {exc}") from exc

    if event is None:
        raise HTTPException(status_code=404, detail=f"Ingestion event '{event_id}' not found")

    raw_sender = event.get("source_sender_identity")
    source_channel = event.get("source_channel")

    if not raw_sender or not source_channel:
        return ApiResponse[SenderContactResolution](
            data=SenderContactResolution(resolved=False, name=None, raw=raw_sender)
        )

    try:
        contact = await resolve_contact_by_channel(pool, source_channel, raw_sender)
    except Exception:
        logger.debug(
            "sender-contact: resolution failed for event %s (fail-open)",
            event_id,
            exc_info=True,
        )
        contact = None

    if contact is None:
        return ApiResponse[SenderContactResolution](
            data=SenderContactResolution(resolved=False, name=None, raw=raw_sender)
        )

    return ApiResponse[SenderContactResolution](
        data=SenderContactResolution(resolved=True, name=contact.name, raw=raw_sender)
    )
