"""Ingestion event endpoints — read-only view of the ingestion event registry.

Provides:

- ``router`` — four read-only endpoints under ``/api/ingestion/events``

Endpoints
---------
GET /api/ingestion/events               — paginated list (limit, offset, source_channel)
GET /api/ingestion/events/{requestId}   — single event detail
GET /api/ingestion/events/{requestId}/sessions  — cross-butler lineage
GET /api/ingestion/events/{requestId}/rollup    — token/cost/butler topology
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.ingestion_event import (
    IngestionEventDetail,
    IngestionEventRollup,
    IngestionEventSession,
    IngestionEventSummary,
)
from butlers.api.pricing import PricingConfig
from butlers.core.ingestion_events import (
    ingestion_event_get,
    ingestion_event_rollup,
    ingestion_event_sessions,
    ingestion_events_list,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/events", tags=["ingestion"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# GET /api/ingestion/events
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[IngestionEventSummary])
async def list_ingestion_events(
    limit: int = Query(20, ge=1, le=200, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    source_channel: str | None = Query(None, description="Filter by source channel"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[IngestionEventSummary]:
    """Return a paginated list of ingestion events, newest first.

    Uses the shared ingestion_events pool (``DatabaseManager.shared_pool()``)
    to query ``shared.ingestion_events``.  Supports optional filtering by
    ``source_channel``.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    rows = await ingestion_events_list(
        pool, limit=limit, offset=offset, source_channel=source_channel
    )

    # Determine total count for pagination metadata
    if source_channel is not None:
        total = await pool.fetchval(
            "SELECT count(*) FROM shared.ingestion_events WHERE source_channel = $1",
            source_channel,
        )
    else:
        total = await pool.fetchval("SELECT count(*) FROM shared.ingestion_events")

    summaries = [IngestionEventSummary(**row) for row in rows]

    return PaginatedResponse[IngestionEventSummary](
        data=summaries,
        meta=PaginationMeta(total=total or 0, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}
# ---------------------------------------------------------------------------


@router.get("/{request_id}", response_model=ApiResponse[IngestionEventDetail])
async def get_ingestion_event(
    request_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[IngestionEventDetail]:
    """Return a single ingestion event by its UUID.

    Returns 404 when no event with that ``request_id`` exists.
    """
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

    return ApiResponse[IngestionEventDetail](data=IngestionEventDetail(**event))


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
