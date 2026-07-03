"""Ingestion event endpoints — unified timeline over the ingestion event registry.

Provides:

- ``router`` — endpoints under ``/api/ingestion/events``
- ``rollup_router`` — endpoints under ``/api/ingestion/rollup``

Endpoints
---------
GET  /api/ingestion/events               — cursor-paginated unified timeline (supports ?q=);
                                            rows carry bulk-enriched tokens/cost/sessions/
                                            sender_display (bu-4utdw.3, no per-row queries)
GET  /api/ingestion/events/{requestId}   — single event detail
GET  /api/ingestion/events/{requestId}/sessions  — cross-butler lineage
GET  /api/ingestion/events/{requestId}/rollup    — token/cost/butler topology
POST /api/ingestion/events/retry/bulk    — bulk retry for both ingestion + filtered tables (max 100)
POST /api/ingestion/events/{id}/replay   — request replay of a filtered event
GET  /api/ingestion/events/{id}/replays  — replay attempt history from public.audit_log
GET  /api/ingestion/events/{id}/sender-contact  — resolve sender_identity to contact name

GET  /api/ingestion/rollup               — aggregate event/session/cost for a filter window
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, timedelta
from datetime import datetime as _datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.models import ApiResponse, CursorPaginatedResponse, CursorPaginationMeta
from butlers.api.models.ingestion_event import (
    IngestionEventDetail,
    IngestionEventListSessionSummary,
    IngestionEventPayload,
    IngestionEventRollup,
    IngestionEventSession,
    IngestionEventSummary,
    IngestionHistogramResponse,
    IngestionWindowRollup,
    ReplayHistoryEntry,
    SenderContactResolution,
)
from butlers.api.pricing import PricingConfig
from butlers.api.routers.audit import append as _audit_append
from butlers.core.ingestion_events import (
    ingestion_event_get,
    ingestion_event_get_inbox_lifecycle,
    ingestion_event_get_payload,
    ingestion_event_replay_history,
    ingestion_event_replay_request,
    ingestion_event_rollup,
    ingestion_event_sessions,
    ingestion_event_set_cost_usd,
    ingestion_events_histogram,
    ingestion_events_list,
    ingestion_events_list_enrichment,
    ingestion_events_received_at_bounds,
    ingestion_events_request_ids_for_trace,
    ingestion_events_sessions_for_ids,
    ingestion_window_rollup,
)
from butlers.identity import (
    ResolvedContact,
    resolve_contact_by_channel,
    resolve_contacts_by_channel_bulk,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/events", tags=["ingestion"])
rollup_router = APIRouter(prefix="/api/ingestion/rollup", tags=["ingestion"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _get_pricing_optional() -> PricingConfig | None:
    """Return the PricingConfig singleton, or None when not yet initialized."""
    try:
        return get_pricing()
    except RuntimeError:
        return None


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
    channels: str | None = Query(
        None,
        description=(
            "Comma-separated source_channel values (e.g. 'email,telegram'). "
            "When set, overrides source_channel."
        ),
    ),
    source_channel: str | None = Query(
        None,
        description="DEPRECATED: use channels instead. Filter by single source channel.",
    ),
    status: Literal[
        "ingested",
        "skipped",
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
            "Filter by event status. 'ingested'/'skipped'/'failed'/'replay_failed' "
            "query public.ingestion_events; 'filtered'/'error'/'replay_complete' "
            "query connectors.filtered_events; 'replay_pending'/'replay_failed' may "
            "appear in both tables. 'skipped' is derived: ingested events whose "
            "triage_decision is 'skip' (stored but deliberately not dispatched). "
            "Ignored when 'statuses' is set. Omit for unified stream."
        ),
    ),
    statuses: str | None = Query(
        None,
        description=(
            "Comma-separated status values to include (e.g. 'ingested,error'). "
            "Takes precedence over 'status'. Use to exclude noise statuses "
            "such as 'skipped' server-side so pagination is not dominated by them."
        ),
    ),
    q: str | None = Query(
        None,
        max_length=200,
        description=(
            "Freetext search (ILIKE %%q%%) against event id, source_channel, "
            "source_sender_identity, source_endpoint_identity, external_event_id, "
            "triage_target (butler routing destination), triage_decision, "
            "filter_reason, and error_detail. "
            "Parameterized — safe against SQL injection."
        ),
    ),
    from_: str | None = Query(
        None,
        alias="from",
        description="ISO-8601 inclusive lower bound on received_at (e.g. '2026-01-01T00:00:00Z').",
    ),
    to: str | None = Query(
        None,
        description="ISO-8601 exclusive upper bound on received_at.",
    ),
    sort: Literal["recent", "cost"] | None = Query(
        None,
        description=(
            "Sort order. Omit or 'recent' → newest first (keyset pagination). "
            "'cost' → highest cost_usd first (offset pagination, NULLS LAST). "
            "When sort='cost', the cursor encodes a page offset rather than a "
            "keyset position — do not mix cursor types across sort modes."
        ),
    ),
    trace_id: str | None = Query(
        None,
        description=(
            "Filter to ingestion events with at least one linked butler session "
            "carrying this OpenTelemetry trace_id — the drill-down spine that "
            "lets a trace survive the hop from a session/notification detail "
            "view into the timeline. trace_id lives on `sessions`, not on the "
            "ingestion event itself, so this is resolved via a cross-butler "
            "session fan-out first, then pushed into the unified timeline "
            "query as an `id = ANY(...)` filter (same SQL-pushdown pattern as "
            "`event_type`/`statuses`). A trace_id that matches no session "
            "returns an empty page, not an error."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> CursorPaginatedResponse[IngestionEventSummary]:
    """Return a cursor-paginated unified timeline of ingestion events, newest first.

    Uses keyset (cursor) pagination via ``(received_at DESC, id DESC)`` — no ``total``
    count is computed per request.  Pass the ``next_cursor`` from a previous response
    as the ``cursor`` query param to fetch the next page.

    When ``sort=cost``, results are ordered by ``cost_usd DESC NULLS LAST`` and
    the cursor encodes a page offset (not a keyset position).  Switch between sort
    modes by dropping the cursor (start a fresh first page).

    Merges ``public.ingestion_events`` (status=ingested/skipped, filter_reason=null)
    with ``connectors.filtered_events`` (status/filter_reason from their own columns).
    Supports optional filtering by ``channels`` (CSV), ``source_channel`` (deprecated),
    ``statuses`` (CSV), ``status`` (single), freetext ``q``, ``from``/``to``
    (ISO-8601 time bounds on received_at), and ``trace_id`` (drill-down spine —
    resolved via a cross-butler session fan-out, then pushed into SQL).

    Channel filter precedence: ``channels`` wins over ``source_channel``.
    Status filter precedence: ``statuses`` wins over ``status``.

    Each item is enriched (bu-4utdw.3) with ``tokens_in``, ``tokens_out``,
    ``session_count``, a capped ``sessions`` summary, and a bulk-resolved
    ``sender_display`` — computed via exactly one grouped session fan-out and
    one grouped sender-contact query for the whole page, not a per-row fetch.
    See :func:`_enrich_list_summaries`.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    if cursor is not None:
        try:
            if sort == "cost":
                from butlers.core.ingestion_events import decode_cost_cursor

                decode_cost_cursor(cursor)
            else:
                from butlers.core.ingestion_events import decode_cursor

                decode_cursor(cursor)  # Validate the cursor early; raises ValueError if malformed.
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid cursor: {exc}") from exc

    # Resolve channel filter: channels CSV wins; fall back to legacy source_channel.
    if channels is not None:
        channel_list: list[str] | None = [c.strip() for c in channels.split(",") if c.strip()]
        channel_list = channel_list or None  # treat empty string as no filter
    elif source_channel is not None:
        channel_list = [source_channel]
    else:
        channel_list = None

    # Resolve status filter: statuses CSV wins; fall back to single status.
    status_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None

    # Parse optional time-range bounds.
    from_dt: _datetime | None = None
    to_dt: _datetime | None = None
    if from_ is not None:
        try:
            from_dt = _datetime.fromisoformat(from_).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'from' value: {exc}") from exc
    if to is not None:
        try:
            to_dt = _datetime.fromisoformat(to).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'to' value: {exc}") from exc

    # Resolve trace_id -> matching event ids BEFORE the main query (the trace
    # lives on `sessions`, not on the ingestion event itself — see
    # ingestion_events_request_ids_for_trace). `is not None` (not truthy) so an
    # explicit empty match set still restricts to zero rows.
    event_ids: list[str] | None = None
    if trace_id is not None and trace_id.strip():
        event_ids = await ingestion_events_request_ids_for_trace(db, trace_id.strip())

    result = await ingestion_events_list(
        pool,
        limit=limit,
        cursor=cursor,
        channels=channel_list,
        status=status,
        statuses=status_list,
        q=q,
        from_dt=from_dt,
        to_dt=to_dt,
        sort=sort,
        event_ids=event_ids,
    )

    summaries = [IngestionEventSummary(**row) for row in result["items"]]

    if summaries:
        await _enrich_list_summaries(summaries, db)

    return CursorPaginatedResponse[IngestionEventSummary](
        data=summaries,
        meta=CursorPaginationMeta(
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        ),
    )


async def _enrich_list_summaries(
    summaries: list[IngestionEventSummary],
    db: DatabaseManager,
) -> None:
    """Mutate ``summaries`` in place with row-level session + sender fields.

    Kills the timeline N+1 request storm (bu-4utdw.3): a 50-row page used to
    fire one ``/rollup`` and one ``/sender-contact`` request per row (up to
    100 extra HTTP requests). This does the equivalent work with exactly:
    - one grouped session fan-out (one query per registered butler schema,
      not per event — see :func:`ingestion_events_sessions_for_ids`), and
    - one grouped sender-contact resolution query
      (:func:`resolve_contacts_by_channel_bulk`).

    Fail-open: any DB error here degrades individual fields to their defaults
    (None/0/[]) rather than failing the whole list request — the fan-out and
    bulk resolver already fail open internally, and pool lookups here are
    guarded defensively for the same reason.
    """
    event_ids = [s.id for s in summaries]

    try:
        pricing = _get_pricing_optional()
        sessions_by_id = await ingestion_events_sessions_for_ids(db, event_ids, pricing=pricing)
        enrichment = ingestion_events_list_enrichment(sessions_by_id)
    except Exception:
        logger.debug("_enrich_list_summaries: session fan-out failed (non-fatal)", exc_info=True)
        enrichment = {}

    channel_pairs: list[tuple[str, str]] = list(
        {
            (s.source_channel, s.source_sender_identity)
            for s in summaries
            if s.source_channel and s.source_sender_identity
        }
    )
    sender_map: dict[tuple[str, str], ResolvedContact | None] = {}
    if channel_pairs:
        try:
            pool = db.credential_shared_pool()
            sender_map = await resolve_contacts_by_channel_bulk(pool, channel_pairs)
        except Exception:
            logger.debug(
                "_enrich_list_summaries: sender-contact bulk resolution failed (non-fatal)",
                exc_info=True,
            )
            sender_map = {}

    for summary in summaries:
        enr = enrichment.get(summary.id)
        if enr is not None:
            summary.tokens_in = enr["tokens_in"]
            summary.tokens_out = enr["tokens_out"]
            summary.session_count = enr["session_count"]
            summary.sessions = [
                IngestionEventListSessionSummary(**sess) for sess in enr["sessions"]
            ]
            if summary.cost_usd is None and enr["session_cost_usd"] is not None:
                summary.cost_usd = enr["session_cost_usd"]

        if summary.source_channel and summary.source_sender_identity:
            resolved = sender_map.get((summary.source_channel, summary.source_sender_identity))
            if resolved is not None:
                summary.sender_display = resolved.name


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/histogram
#
# Registered BEFORE /{request_id} — Starlette matches routes in registration
# order and /{request_id} is a single-path-segment catch-all that would
# otherwise swallow /histogram (treating "histogram" as a request_id).
# ---------------------------------------------------------------------------


@router.get("/histogram", response_model=IngestionHistogramResponse)
async def get_ingestion_events_histogram(
    from_: str | None = Query(
        None,
        alias="from",
        description=(
            "ISO-8601 inclusive lower bound on received_at. Required unless "
            "trace_id is set, in which case the window auto-widens to the "
            "trace's own event bounds instead (see trace_id) and any "
            "explicit from/to passed alongside it is ignored."
        ),
    ),
    to: str | None = Query(
        None,
        description=(
            "ISO-8601 exclusive upper bound on received_at. Required unless "
            "trace_id is set (see trace_id)."
        ),
    ),
    bucket: Literal["1m", "5m", "1h"] = Query(
        "1m",
        description=(
            "Bucket granularity. '1m' (default) is capped at 48h ranges; wider "
            "ranges must use '5m' (up to 10 days) or '1h' (up to 120 days) — "
            "see the guardrail note below."
        ),
    ),
    channels: str | None = Query(
        None,
        description="Comma-separated source_channel values (e.g. 'email,telegram').",
    ),
    statuses: str | None = Query(
        None,
        description="Comma-separated status values to include (e.g. 'ingested,error').",
    ),
    q: str | None = Query(
        None,
        max_length=200,
        description=("Freetext search (ILIKE %%q%%), same fields as GET /api/ingestion/events."),
    ),
    trace_id: str | None = Query(
        None,
        description=(
            "Filter to ingestion events with at least one linked butler session "
            "carrying this OpenTelemetry trace_id — same drill-down spine as "
            "GET /api/ingestion/events. Resolved via a cross-butler session "
            "fan-out (ingestion_events_request_ids_for_trace), then pushed into "
            "the histogram query as an `id = ANY(...)` filter so a trace-scoped "
            "hour strip stays consistent with the trace-scoped ledger. Makes "
            "`from`/`to` optional: the window auto-widens to the trace's own "
            "``received_at`` bounds (bu-1f81d) instead of requiring the caller "
            "to already know a window wide enough to contain it — the same "
            "problem the ledger solves by dropping the window bound entirely "
            "for trace-scoped queries. A trace_id that matches no session "
            "returns an empty histogram, not an error."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> IngestionHistogramResponse:
    """Return per-minute (or coarser) ingestion event counts by status.

    Powers a status-aware timeline hour strip: ONE grouped ``date_bin`` query
    over the same union GET /api/ingestion/events reads (``public.ingestion_events``
    UNION ALL ``connectors.filtered_events``) — no per-bucket queries, no LLM
    calls, no Prometheus dependency. This is DB truth, distinct from the
    ``aggregates_available`` degraded-mode surface used by the Prometheus-backed
    pipeline endpoints.

    Accepts the same ``channels``/``statuses``/``q``/``trace_id`` filters as
    the events list so the strip respects active filters (including a
    trace-scoped view).

    Zero-count buckets are omitted — a bucket only appears in the response when
    at least one event fell into it during the requested window; present
    buckets always carry all seven status keys (zero-filled for statuses with
    no events in that bucket).

    Guardrail: bucket count is capped at 2880 regardless of granularity (48h
    at 1m, 10 days at 5m, 120 days at 1h) to bound the scan. Requesting a
    range/bucket combination that would exceed the cap returns 422 — retry
    with a coarser bucket. In trace-scoped auto-widen mode this is handled
    automatically: the requested bucket escalates to the next coarser
    granularity (1m → 5m → 1h) until the trace's actual span fits, since the
    caller picked `bucket` for the range-picker's visible window, not the
    trace's own span.

    Returns:
        200 — ``{"buckets": [{"ts": "...", "counts": {...}}], "bucket": "1m"}``
              (``bucket`` echoes whichever granularity was actually used,
              which may be coarser than requested in trace-scoped auto-widen
              mode)
        422 — invalid ``from``/``to``/``bucket``, missing ``from``/``to``
              without ``trace_id``, or the range exceeds the guardrail cap
              even at the coarsest bucket
        503 — shared database pool unavailable
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    channel_list = [c.strip() for c in channels.split(",") if c.strip()] if channels else None
    status_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None

    # Resolve trace_id -> matching event ids BEFORE the main query, same
    # resolve-then-filter pattern as GET /api/ingestion/events (see
    # ingestion_events_request_ids_for_trace).
    event_ids: list[str] | None = None
    if trace_id is not None and trace_id.strip():
        event_ids = await ingestion_events_request_ids_for_trace(db, trace_id.strip())

    if event_ids is not None:
        # Trace-scoped: drop the caller's from/to in favor of the trace's own
        # received_at bounds (bu-1f81d) — parity with the ledger, which drops
        # the window bound entirely for trace_id queries. Honoring a
        # window the caller supplied for the *unfiltered* view here would
        # silently zero out an otherwise-populated trace-scoped histogram
        # whenever the trace's events fall outside it (the bug this closes).
        if not event_ids:
            # No session anywhere carries this trace — same "empty, not an
            # error" contract as the ledger and the window rollup.
            return IngestionHistogramResponse(buckets=[], bucket=bucket)
        min_ts, max_ts = await ingestion_events_received_at_bounds(pool, event_ids)
        if min_ts is None or max_ts is None:
            # Defensive: the session fan-out matched but the events
            # themselves are gone by the time we queried for bounds.
            return IngestionHistogramResponse(buckets=[], bucket=bucket)
        from_dt = min_ts
        to_dt = max_ts + timedelta(seconds=1)  # `to` is exclusive; include the last event
    else:
        if from_ is None or to is None:
            raise HTTPException(
                status_code=422,
                detail="'from' and 'to' are required unless 'trace_id' is set",
            )
        try:
            from_dt = _datetime.fromisoformat(from_).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'from' value: {exc}") from exc
        try:
            to_dt = _datetime.fromisoformat(to).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'to' value: {exc}") from exc

    # Bucket candidates to try, finest-requested-first. Only trace-scoped
    # auto-widen mode escalates to coarser buckets on a guardrail miss — the
    # non-trace path keeps its existing "422, retry coarser yourself" contract.
    _BUCKET_ORDER = ("1m", "5m", "1h")
    bucket_candidates = (
        _BUCKET_ORDER[_BUCKET_ORDER.index(bucket) :] if event_ids is not None else (bucket,)
    )

    result: dict | None = None
    last_error: ValueError | None = None
    for candidate in bucket_candidates:
        try:
            result = await ingestion_events_histogram(
                pool,
                from_dt=from_dt,
                to_dt=to_dt,
                bucket=candidate,
                channels=channel_list,
                statuses=status_list,
                q=q,
                event_ids=event_ids,
            )
            break
        except ValueError as exc:
            last_error = exc

    if result is None:
        assert last_error is not None  # bucket_candidates is always non-empty
        raise HTTPException(status_code=422, detail=str(last_error)) from last_error

    return IngestionHistogramResponse(**result)


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
    pricing: PricingConfig | None = Depends(_get_pricing_optional),
) -> ApiResponse[list[IngestionEventSession]]:
    """Return cross-butler sessions linked to this ingestion event.

    Fans out to all registered butler databases concurrently and collects
    sessions whose ``request_id`` matches.  Results are sorted by
    ``started_at`` ascending so the lineage reads chronologically.

    Each session includes a ``cost_usd`` field: the estimated USD cost derived
    from token counts and the pricing catalog when available, with a fallback to
    the legacy ``cost`` JSONB column.  ``cost_usd`` is ``None`` when neither
    source yields a value.
    """
    sessions_data = await ingestion_event_sessions(db, request_id, pricing=pricing)
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

    Side effect: writes ``total_cost`` back to ``public.ingestion_events.cost_usd``
    (lazy write-through, core_126) when at least one session is found.  This
    populates the denormalized cost column used by the Spend sort view.
    """
    sessions_data = await ingestion_event_sessions(db, request_id)
    rollup_data = ingestion_event_rollup(request_id, sessions_data, pricing=pricing)

    if rollup_data["total_sessions"] > 0:
        try:
            pool = db.credential_shared_pool()
            await ingestion_event_set_cost_usd(pool, request_id, rollup_data["total_cost"])
        except Exception:
            logger.debug(
                "cost_usd write-back failed for event %s (non-fatal)", request_id, exc_info=True
            )

    return ApiResponse[IngestionEventRollup](data=IngestionEventRollup(**rollup_data))


# ---------------------------------------------------------------------------
# POST /api/ingestion/events/retry/bulk
# ---------------------------------------------------------------------------

_MAX_BULK_RETRY_BATCH = 100

# Channels that are classified as replay-unsafe per the
# connector-replay-idempotency-policy spec.  Referenced by both /retry/bulk
# (safety gate added in PR #2357) and kept here after /replay/bulk removal.
_UNSAFE_CHANNELS: frozenset[str] = frozenset({"email"})


@router.post("/retry/bulk")
async def bulk_retry_ingestion_events(
    request: Request,
    body: Annotated[dict, Body(...)],
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """Bulk retry/replay up to 100 events across both ingestion and filtered tables.

    Calls the same per-event replay logic as
    ``POST /api/ingestion/events/{id}/replay`` for each event.  This allows
    retrying events from both ``public.ingestion_events`` and
    ``connectors.filtered_events`` in a single request.

    Each event is attempted independently — partial failures do NOT abort the
    batch.  The caller receives per-event results so it can identify exactly
    which events need follow-up.

    Accepts ``{"event_ids": [...]}`` where ``event_ids`` is a list of UUID
    strings (max 100).

    Email events and events with ``connector_registry.replay_safe = false`` are
    rejected with HTTP 409 before any replay is attempted.

    Returns:
        200 — ``{"results": [{event_id, status, error?}], "succeeded": N, "failed": N}``
        400 — missing/empty ``event_ids``, or batch exceeds max size
        409 — batch contains replay-unsafe events (email or replay_safe=false)
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

    client_host = getattr(request.client, "host", None) if request.client else None

    # ---------------------------------------------------------------------------
    # Pre-flight safety gate: reject the entire batch if any event is unsafe.
    #
    # /retry/bulk covers both public.ingestion_events and connectors.filtered_events,
    # so we query BOTH tables to identify any email (or replay_safe=false) events
    # before touching any rows.  A single unsafe event rejects the entire batch with
    # HTTP 409 — fail-closed semantics ensures no partial unsafe replay.
    # ---------------------------------------------------------------------------
    try:
        # Collect (id, source_channel, replay_safe) from both tables in one query.
        # ingestion_events does not have a connector_registry join, so replay_safe is
        # treated as TRUE for those rows (only source_channel is checked).
        channel_rows = await pool.fetch(
            """
            SELECT id::text, source_channel, TRUE AS replay_safe
            FROM public.ingestion_events
            WHERE id = ANY($1::uuid[])
            UNION ALL
            SELECT fe.id::text, fe.source_channel,
                   COALESCE(cr.replay_safe, TRUE) AS replay_safe
            FROM connectors.filtered_events fe
            LEFT JOIN switchboard.connector_registry cr
              ON cr.connector_type = fe.connector_type
             AND cr.endpoint_identity = fe.endpoint_identity
            WHERE fe.id = ANY($1::uuid[])
            """,
            [UUID(e) for e in event_ids],
        )
    except Exception:
        logger.warning("bulk_retry: pre-flight channel safety check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Database error during safety pre-flight check")

    unsafe_events: list[dict] = []
    for row in channel_rows:
        channel = row["source_channel"]
        replay_safe = row["replay_safe"]
        if channel in _UNSAFE_CHANNELS or not replay_safe:
            unsafe_events.append(
                {
                    "id": row["id"],
                    "source_channel": channel,
                    "reason": (
                        f"source_channel='{channel}' is not replay-safe"
                        if channel in _UNSAFE_CHANNELS
                        else "connector_registry.replay_safe=false"
                    ),
                }
            )

    if unsafe_events:
        try:
            await _audit_append(
                pool,
                actor="dashboard",
                action="ingestion.retry.bulk_reject",
                target=json.dumps(event_ids),
                note=json.dumps(
                    {
                        "reason": "unsafe_channel",
                        "unsafe_events": unsafe_events,
                    }
                ),
                ip=client_host,
            )
        except Exception:
            logger.warning("bulk_retry: failed to write bulk_reject audit entry", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Batch contains replay-unsafe events",
                "unsafe_events": unsafe_events,
            },
        )

    # Obtain the switchboard pool for resetting message_inbox on replay.
    switchboard_pool = None
    try:
        switchboard_pool = db.pool("switchboard")
    except (KeyError, Exception):
        pass  # Non-fatal: replay of ingested events will log a warning.

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
            # Use the same action string and note shape as the single-event replay
            # endpoint so these entries appear in replay history timelines.
            try:
                await _audit_append(
                    pool,
                    actor="dashboard",
                    action="ingestion.event.replay",
                    target=event_id,
                    note=json.dumps({"result": "pending", "source": result.get("source")}),
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
    then calls ``resolve_contact_by_channel`` against ``relationship.entity_facts``
    (migration bead 7).

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
# GET /api/ingestion/events/{id}/payload
# ---------------------------------------------------------------------------


@router.get("/{event_id}/payload", response_model=ApiResponse[IngestionEventPayload])
async def get_ingestion_event_payload(
    event_id: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[IngestionEventPayload]:
    """Return the raw inbound payload for an ingestion event.

    Access is audit-gated: every successful payload read is recorded in the
    dashboard audit log with ``operation='ingestion.event.payload_read'``.

    Returns:
        200 — ``{content, bytes, truncated, channel}``
        404 — event not found (not in public.ingestion_events or filtered_events)
        422 — invalid event_id format
        503 — shared or switchboard database pool unavailable
    """
    request_path = f"/api/ingestion/events/{event_id}/payload"

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # The switchboard pool is required to read message_inbox.raw_payload.
    try:
        switchboard_pool = db.pool("switchboard")
    except (KeyError, Exception) as exc:
        raise HTTPException(
            status_code=503, detail=f"Switchboard database unavailable: {exc}"
        ) from exc

    try:
        result = await ingestion_event_get_payload(pool, switchboard_pool, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid event_id: {exc}") from exc

    if result is None:
        raise HTTPException(status_code=404, detail=f"Ingestion event '{event_id}' not found")

    # Emit audit entry BEFORE returning payload data (fail-closed: audit is
    # recorded before any PII-bearing data leaves the server).
    await emit_dashboard_audit(
        db,
        butler="switchboard",
        operation="ingestion.event.payload_read",
        method="GET",
        path=request_path,
        path_params={"event_id": event_id},
        body={"reason": "raw_payload_view"},
        response_status=200,
        request=request,
    )

    if result.get("missing"):
        # Event exists but message_inbox row was pruned or never written
        # (e.g. filtered event). Return a truthful empty payload rather than
        # a misleading 404 — the event did exist.
        return ApiResponse[IngestionEventPayload](
            data=IngestionEventPayload(
                content="",
                bytes=0,
                truncated=False,
                channel=None,
            )
        )

    return ApiResponse[IngestionEventPayload](
        data=IngestionEventPayload(
            content=result["content"],
            bytes=result["bytes"],
            truncated=result["truncated"],
            channel=result["channel"],
        )
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
        description=(
            "Freetext search (ILIKE %%q%%) against event id, channel, sender, "
            "endpoint identity, external event id, triage target, triage decision, "
            "filter reason, and error detail."
        ),
    ),
    trace_id: str | None = Query(
        None,
        description=(
            "Filter to ingestion events with at least one linked butler session "
            "carrying this OpenTelemetry trace_id — same drill-down spine as "
            "GET /api/ingestion/events. Resolved via a cross-butler session "
            "fan-out (ingestion_events_request_ids_for_trace), then pushed into "
            "the rollup query as an `id = ANY(...)` filter so a trace-scoped "
            "footer rollup stays consistent with the trace-scoped ledger. Any "
            "`from`/`to` passed alongside `trace_id` is ignored — the window "
            "bound is dropped entirely (bu-1f81d), matching the ledger's own "
            "trace-scoped behavior, since a traced event's received_at may "
            "fall outside whatever window the range picker happens to have "
            "active. A trace_id that matches no session returns a zeroed "
            "rollup, not an error."
        ),
    ),
    db: DatabaseManager = Depends(_get_rollup_db_manager),
    pricing: PricingConfig | None = Depends(_get_pricing_optional),
) -> IngestionWindowRollup:
    """Return aggregate event/session/cost counts for the active filter window.

    Accepts the same filter shape as GET /api/ingestion/events (including
    ``trace_id``).  When pricing data is available, ``cost`` is populated with
    the estimated USD total for all sessions linked to matching events.

    When ``trace_id`` is set, ``from``/``to`` are ignored and the window is
    unbounded (bu-1f81d) — parity with the ledger, which drops the window
    bound entirely for trace-scoped queries so a trace older than the active
    range picker window still rolls up correctly.

    Returns:
        200 — ``{events, sessions, cost, window: {from, to}}``
        503 — shared database unavailable
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    is_trace_scoped = trace_id is not None and trace_id.strip()

    from_dt = None
    to_dt = None
    if from_ is not None and not is_trace_scoped:
        try:
            from_dt = _datetime.fromisoformat(from_).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'from' timestamp: {exc}") from exc
    if to is not None and not is_trace_scoped:
        try:
            to_dt = _datetime.fromisoformat(to).astimezone(UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'to' timestamp: {exc}") from exc

    channel_list = [c.strip() for c in channels.split(",") if c.strip()] if channels else None
    status_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None

    # Resolve trace_id -> matching event ids BEFORE the main query, same
    # resolve-then-filter pattern as GET /api/ingestion/events (see
    # ingestion_events_request_ids_for_trace). from_dt/to_dt are already
    # forced to None above when trace-scoped, so the window is dropped
    # entirely rather than requiring the caller to already know a window
    # wide enough to contain the trace.
    event_ids: list[str] | None = None
    if is_trace_scoped:
        event_ids = await ingestion_events_request_ids_for_trace(db, trace_id.strip())

    result = await ingestion_window_rollup(
        pool,
        from_dt=from_dt,
        to_dt=to_dt,
        channels=channel_list,
        statuses=status_list,
        q=q,
        db=db,
        pricing=pricing,
        event_ids=event_ids,
    )

    return IngestionWindowRollup(
        events=result["events"],
        sessions=result["sessions"],
        cost=result["cost"],
        window=result["window"],
    )
