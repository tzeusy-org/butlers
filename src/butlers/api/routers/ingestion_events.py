"""Ingestion event endpoints — unified timeline over the ingestion event registry.

Provides:

- ``router`` — endpoints under ``/api/ingestion/events``
- ``rollup_router`` — endpoints under ``/api/ingestion/rollup``

Endpoints
---------
GET  /api/ingestion/events               — cursor-paginated unified timeline (supports ?q=)
GET  /api/ingestion/events/{requestId}   — single event detail
GET  /api/ingestion/events/{requestId}/sessions  — cross-butler lineage
GET  /api/ingestion/events/{requestId}/rollup    — token/cost/butler topology
POST /api/ingestion/events/replay/bulk   — bulk replay handler (max 50 events, email blocked)
POST /api/ingestion/events/retry/bulk    — bulk retry for both ingestion + filtered tables (max 100)
POST /api/ingestion/events/{id}/replay   — request replay of a filtered event
GET  /api/ingestion/events/{id}/replays  — replay attempt history from public.audit_log
GET  /api/ingestion/events/{id}/sender-contact  — resolve sender_identity to contact name

GET  /api/ingestion/rollup               — aggregate event/session/cost for a filter window
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from datetime import datetime as _datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from prometheus_client import Counter

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.models import ApiResponse, CursorPaginatedResponse, CursorPaginationMeta
from butlers.api.models.ingestion_event import (
    IngestionEventDetail,
    IngestionEventRollup,
    IngestionEventSession,
    IngestionEventSummary,
    IngestionWindowRollup,
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
    ingestion_window_rollup,
)
from butlers.identity import resolve_contact_by_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/events", tags=["ingestion"])
rollup_router = APIRouter(prefix="/api/ingestion/rollup", tags=["ingestion"])


# ---------------------------------------------------------------------------
# Prometheus counters — bulk_replay 5xx error observability.
# Without these, structural DB errors (e.g. FOR UPDATE + LEFT JOIN) silently
# returned 503s in production for an entire day before being noticed through
# a different channel.  Counter names follow the pattern:
# ingestion_bulk_replay_<class>_total (labels: code=<http_status_code>).
# ---------------------------------------------------------------------------

ingestion_bulk_replay_errors_total = Counter(
    "ingestion_bulk_replay_errors_total",
    "Number of 5xx error responses from POST /api/ingestion/events/replay/bulk.",
    ["code"],
)


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
    q: str | None = Query(
        None,
        max_length=200,
        description=(
            "Freetext search (ILIKE %%q%%) against source_channel, "
            "source_sender_identity, and error_detail. "
            "Parameterized — safe against SQL injection."
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
    Supports optional filtering by ``source_channel``, ``status``, and freetext ``q``.
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
        pool, limit=limit, cursor=cursor, source_channel=source_channel, status=status, q=q
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
# POST /api/ingestion/events/replay/bulk
# ---------------------------------------------------------------------------

_MAX_BULK_REPLAY_BATCH = 50

# Channels that are classified as replay-unsafe per the
# connector-replay-idempotency-policy spec.
_UNSAFE_CHANNELS: frozenset[str] = frozenset({"email"})


@router.post("/replay/bulk")
async def bulk_replay_ingestion_events(
    request: Request,
    body: Annotated[dict, Body(...)],
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """Bulk-replay up to 50 filtered events.

    Accepts ``{"event_ids": [...], "reason": "..."}`` where ``event_ids`` is a
    list of UUID strings (max 50).  Events with ``source_channel = 'email'`` (or
    whose ``connector_registry.replay_safe = false``) are rejected with HTTP 409.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` to avoid racing against the
    connector drain loop.

    Returns:
        200 — ``{"accepted": [...], "capped": [...]}``
        400 — missing or malformed ``event_ids``
        409 — batch contains replay-unsafe events (email or replay_safe=false)
        503 — shared database pool unavailable
    """
    event_ids_raw: list = body.get("event_ids", [])
    reason: str = str(body.get("reason", "")).strip() or "bulk replay"

    if not isinstance(event_ids_raw, list) or not event_ids_raw:
        raise HTTPException(status_code=400, detail="event_ids must be a non-empty list")

    # Cap at max batch size; track overflow
    capped: list[str] = []
    if len(event_ids_raw) > _MAX_BULK_REPLAY_BATCH:
        capped = [str(e) for e in event_ids_raw[_MAX_BULK_REPLAY_BATCH:]]
        event_ids_raw = event_ids_raw[:_MAX_BULK_REPLAY_BATCH]

    # Validate UUID format
    from uuid import UUID

    try:
        event_ids: list[UUID] = [UUID(str(e)) for e in event_ids_raw]
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID in event_ids: {exc}") from exc

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        ingestion_bulk_replay_errors_total.labels(code="503").inc()
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    client_host = getattr(request.client, "host", None) if request.client else None

    # Acquire a single connection and run the entire lock → update → audit sequence
    # inside one explicit transaction.  This ensures the FOR UPDATE lock is held until
    # commit (§6.2 mandate-5 concurrency guarantee) and the audit insert is atomic with
    # the state mutation (§6.2 mandate-1 audit-on-mutation semantics).
    #
    # ingestion_bulk_replay_errors_total counter is incremented on every 5xx return path
    # to provide Prometheus-queryable observability over structural failures (e.g. SQL bugs).
    #
    # Previously each pool.fetch() ran in its own implicit transaction, so the FOR UPDATE
    # lock was released the moment the SELECT completed — the UPDATE then had no lock
    # protection, and the audit append was entirely non-atomic.  See Gemini review on
    # PR #1803 lines 136 and 186.
    accepted_ids: list[str] = []
    locked_ids: set = set()
    locked_rows: list = []

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Phase 1: Lock candidate rows inside the open transaction.
                # FOR UPDATE SKIP LOCKED holds the row locks until the transaction commits.
                try:
                    locked_rows = await conn.fetch(
                        """
                        SELECT fe.id, fe.source_channel,
                               COALESCE(cr.replay_safe, TRUE) AS replay_safe
                        FROM connectors.filtered_events fe
                        LEFT JOIN connector_registry cr
                          ON cr.connector_type = fe.connector_type
                         AND cr.endpoint_identity = fe.endpoint_identity
                        WHERE fe.id = ANY($1::uuid[])
                          AND fe.status IN ('filtered', 'error', 'replay_failed', 'replay_complete')
                        FOR UPDATE OF fe SKIP LOCKED
                        """,
                        event_ids,
                    )
                except Exception:
                    logger.warning(
                        "bulk_replay: failed to lock filtered_events rows", exc_info=True
                    )
                    ingestion_bulk_replay_errors_total.labels(code="503").inc()
                    raise HTTPException(
                        status_code=503, detail="Database error while locking events"
                    )

                locked_ids = {row["id"] for row in locked_rows}

                # Phase 2: Channel safety gate — reject the ENTIRE batch if any event is unsafe.
                # Per spec: "The handler SHALL NOT partially process the batch when at least one
                # event is unsafe — the entire batch is rejected to preserve atomic semantics."
                # Raising HTTPException here aborts the transaction and releases all row locks.
                unsafe_events: list[dict] = []
                for row in locked_rows:
                    channel = row["source_channel"]
                    replay_safe = row["replay_safe"]
                    if channel in _UNSAFE_CHANNELS or not replay_safe:
                        unsafe_events.append(
                            {
                                "id": str(row["id"]),
                                "source_channel": channel,
                                "reason": (
                                    f"source_channel='{channel}' is not replay-safe"
                                    if channel in _UNSAFE_CHANNELS
                                    else "connector_registry.replay_safe=false"
                                ),
                            }
                        )

                if unsafe_events:
                    # Emit rejection audit entry on pool (outside the transaction) so the
                    # audit record is committed even when HTTPException rolls back the tx.
                    # Fail-closed auditing: the rejection must be persisted regardless of
                    # whether the surrounding transaction succeeds or is aborted.
                    try:
                        await _audit_append(
                            pool,
                            actor="dashboard",
                            action="ingestion.replay.bulk_reject",
                            target=json.dumps([str(e) for e in event_ids]),
                            note=json.dumps(
                                {
                                    "reason": "unsafe_channel",
                                    "unsafe_events": unsafe_events,
                                    "submitted_reason": reason,
                                }
                            ),
                            ip=client_host,
                        )
                    except Exception:
                        logger.warning(
                            "bulk_replay: failed to write bulk_reject audit entry", exc_info=True
                        )
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "Batch contains replay-unsafe events",
                            "unsafe_events": unsafe_events,
                        },
                    )

                # Phase 3: Mark locked events as replay_pending (same connection, same tx).
                if locked_rows:
                    try:
                        updated = await conn.fetch(
                            """
                            UPDATE connectors.filtered_events
                            SET status = 'replay_pending',
                                replay_requested_at = now(),
                                error_detail = NULL
                            WHERE id = ANY($1::uuid[])
                              AND status IN (
                                  'filtered', 'error', 'replay_failed', 'replay_complete'
                              )
                            RETURNING id
                            """,
                            list(locked_ids),
                        )
                        accepted_ids = [str(row["id"]) for row in updated]
                    except Exception:
                        logger.warning(
                            "bulk_replay: failed to update filtered_events", exc_info=True
                        )
                        ingestion_bulk_replay_errors_total.labels(code="503").inc()
                        raise HTTPException(
                            status_code=503, detail="Database error during replay marking"
                        )

                # Phase 4: Audit successful batch (same connection, same tx — atomic with update).
                try:
                    await _audit_append(
                        conn,
                        actor="dashboard",
                        action="ingestion.replay.bulk_submit",
                        target=json.dumps(accepted_ids),
                        note=json.dumps(
                            {
                                "reason": reason,
                                "accepted_count": len(accepted_ids),
                                "capped_count": len(capped),
                                "skipped_locked": len(event_ids) - len(locked_ids),
                            }
                        ),
                        ip=client_host,
                    )
                except Exception:
                    logger.warning(
                        "bulk_replay: failed to write bulk_submit audit entry", exc_info=True
                    )
    except HTTPException:
        raise
    except Exception:
        logger.warning("bulk_replay: unexpected error during transaction", exc_info=True)
        ingestion_bulk_replay_errors_total.labels(code="503").inc()
        raise HTTPException(status_code=503, detail="Database error during bulk replay")

    return {
        "accepted": accepted_ids,
        "capped": capped,
        "skipped_locked": [str(e) for e in event_ids if e not in locked_ids],
    }


# ---------------------------------------------------------------------------
# POST /api/ingestion/events/retry/bulk
# ---------------------------------------------------------------------------

_MAX_BULK_RETRY_BATCH = 100


@router.post("/retry/bulk")
async def bulk_retry_ingestion_events(
    request: Request,
    body: Annotated[dict, Body(...)],
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """Bulk retry/replay up to 100 events across both ingestion and filtered tables.

    Unlike ``POST /api/ingestion/events/replay/bulk`` (which targets only
    ``connectors.filtered_events`` and uses SELECT … FOR UPDATE SKIP LOCKED),
    this endpoint calls the same per-event replay logic as
    ``POST /api/ingestion/events/{id}/replay`` for each event.  This allows
    retrying events from both ``public.ingestion_events`` and
    ``connectors.filtered_events`` in a single request.

    Each event is attempted independently — partial failures do NOT abort the
    batch.  The caller receives per-event results so it can identify exactly
    which events need follow-up.

    Accepts ``{"event_ids": [...]}`` where ``event_ids`` is a list of UUID
    strings (max 100).

    Returns:
        200 — ``{"results": [{event_id, status, error?}], "succeeded": N, "failed": N}``
        400 — missing/empty ``event_ids``, or batch exceeds max size
        503 — shared database pool unavailable
    """
    event_ids_raw: list = body.get("event_ids", [])

    if not isinstance(event_ids_raw, list) or not event_ids_raw:
        raise HTTPException(status_code=400, detail="event_ids must be a non-empty list")

    if len(event_ids_raw) > _MAX_BULK_RETRY_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size {len(event_ids_raw)} exceeds maximum of {_MAX_BULK_RETRY_BATCH}",
        )

    from uuid import UUID

    # Validate all UUIDs up front — fail fast with a clear error rather than
    # silently skipping invalid entries mid-batch.
    try:
        event_ids: list[str] = [str(UUID(str(e))) for e in event_ids_raw]
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID in event_ids: {exc}") from exc

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

    client_host = getattr(request.client, "host", None) if request.client else None

    results: list[dict] = []
    succeeded = 0
    failed = 0

    for event_id in event_ids:
        try:
            result = await ingestion_event_replay_request(
                pool, event_id, switchboard_pool=switchboard_pool
            )
        except Exception as exc:
            # Unexpected error (e.g. DB connectivity mid-batch) — record as failure
            # and continue processing remaining events.
            logger.warning(
                "bulk_retry: unexpected error processing event %s", event_id, exc_info=True
            )
            results.append(
                {
                    "event_id": event_id,
                    "status": "error",
                    "error": f"Unexpected error: {exc}",
                }
            )
            failed += 1
            continue

        outcome = result["outcome"]
        if outcome == "ok":
            results.append({"event_id": event_id, "status": "replay_pending"})
            succeeded += 1
            # Record each accepted retry in public.audit_log (best-effort, non-fatal).
            try:
                await _audit_append(
                    pool,
                    actor="dashboard",
                    action="ingestion.event.retry",
                    target=event_id,
                    note=json.dumps({"source": result.get("source")}),
                    ip=client_host,
                )
            except Exception:
                logger.warning(
                    "bulk_retry: failed to append audit_log entry for event %s",
                    event_id,
                    exc_info=True,
                )
        elif outcome == "not_found":
            results.append(
                {
                    "event_id": event_id,
                    "status": "not_found",
                    "error": "Event not found in any table",
                }
            )
            failed += 1
        else:
            # outcome == "conflict" — event exists but is not in a retryable state
            results.append(
                {
                    "event_id": event_id,
                    "status": "conflict",
                    "error": (
                        f"Event is not retryable (current status: {result.get('current_status')})"
                    ),
                }
            )
            failed += 1

    return {"results": results, "succeeded": succeeded, "failed": failed}


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


# ---------------------------------------------------------------------------
# Rollup router dependency stub — wired at app startup same as the events router
# ---------------------------------------------------------------------------


def _get_rollup_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# GET /api/ingestion/rollup
# ---------------------------------------------------------------------------


@rollup_router.get("", response_model=IngestionWindowRollup)
async def get_ingestion_window_rollup(
    from_: str | None = Query(None, alias="from", description="ISO-8601 lower bound (inclusive)"),
    to: str | None = Query(None, description="ISO-8601 upper bound (exclusive)"),
    channels: str | None = Query(
        None, description="Comma-separated source_channel values (e.g. email,telegram)"
    ),
    statuses: str | None = Query(
        None, description="Comma-separated status values (e.g. ingested,error)"
    ),
    q: str | None = Query(
        None,
        max_length=200,
        description="Freetext search (ILIKE %%q%%) against channel, sender, error_detail",
    ),
    db: DatabaseManager = Depends(_get_rollup_db_manager),
) -> IngestionWindowRollup:
    """Return aggregate event/session/cost counts for the active filter window.

    Accepts the same filter shape as GET /api/ingestion/events.  The ``cost``
    field is always ``null`` — cost-per-event aggregation is not yet available
    at the window level (see follow-up bead for cost-per-event backend).

    Returns:
        200 — ``{events, sessions, cost, window: {from, to}}``
        503 — shared database unavailable
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    from_dt = None
    to_dt = None
    if from_ is not None:
        try:
            from_dt = _datetime.fromisoformat(from_).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'from' timestamp: {exc}") from exc
    if to is not None:
        try:
            to_dt = _datetime.fromisoformat(to).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'to' timestamp: {exc}") from exc

    channel_list = [c.strip() for c in channels.split(",") if c.strip()] if channels else None
    status_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None

    result = await ingestion_window_rollup(
        pool,
        from_dt=from_dt,
        to_dt=to_dt,
        channels=channel_list,
        statuses=status_list,
        q=q,
        db=db,
    )

    return IngestionWindowRollup(
        events=result["events"],
        sessions=result["sessions"],
        cost=result["cost"],
        window=result["window"],
    )
