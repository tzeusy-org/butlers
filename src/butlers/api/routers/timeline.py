"""Timeline endpoint — cross-butler unified event stream.

Provides:

- ``router`` — timeline endpoint at ``GET /api/timeline``
- ``GET /api/timeline/attention`` — recent current-status failures

Merges sessions and notifications from all butler databases into a single
time-ordered event stream using ``DatabaseManager.fan_out_with_status()``. Supports
composite ``(timestamp, id)`` keyset pagination (``before`` cursor + ``limit``)
and SQL-level filtering by butler and event type.

Cross-butler fan-out reads go through the versioned read-model boundary in
``butlers.api.read_models.timeline_v1`` rather than constructing ad-hoc SQL
inline in this router.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.timeline import (
    TimelineAttentionItem,
    TimelineAttentionMeta,
    TimelineAttentionResponse,
    TimelineEvent,
    TimelineHeartbeatRollup,
    TimelineHistogramBucket,
    TimelineHistogramMeta,
    TimelineHistogramResponse,
    TimelineMeta,
    TimelineResponse,
)
from butlers.api.read_models.timeline_v1 import (
    TIMELINE_ATTENTION_LIMIT,
    TimelineNotificationRow,
    TimelineSessionRow,
    decode_cursor,
    encode_cursor,
    query_timeline_attention_notifications_single,
    query_timeline_attention_sessions_fan_out,
    query_timeline_notification_histogram_single,
    query_timeline_notifications_single,
    query_timeline_session_histogram_fan_out,
    query_timeline_sessions_fan_out,
)
from butlers.api.session_presentation import derive_session_machine_class, derive_session_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/timeline", tags=["timeline"])

_MAX_INTERVAL = timedelta(hours=24)


def _validate_interval(
    since: datetime | None, until: datetime | None, *, required: bool
) -> tuple[datetime | None, datetime | None]:
    if since is None or until is None:
        if required or since is not None or until is not None:
            raise HTTPException(
                status_code=422, detail="'since' and 'until' must be supplied together"
            )
        return None, None
    if (
        since.tzinfo is None
        or since.utcoffset() is None
        or until.tzinfo is None
        or until.utcoffset() is None
    ):
        raise HTTPException(
            status_code=422, detail="Timeline interval bounds must be timezone-aware"
        )
    since_utc = since.astimezone(UTC)
    until_utc = until.astimezone(UTC)
    if since_utc.second or since_utc.microsecond or until_utc.second or until_utc.microsecond:
        raise HTTPException(
            status_code=422, detail="Timeline interval bounds must align to whole UTC minutes"
        )
    duration = until_utc - since_utc
    if duration <= timedelta(0) or duration > _MAX_INTERVAL:
        raise HTTPException(
            status_code=422, detail="Timeline interval must be ordered and no longer than 24 hours"
        )
    return since_utc, until_utc


def _source_selection(event_type: list[str] | None) -> tuple[bool, bool, bool | None, bool]:
    want_session_type = event_type is None or "session" in event_type
    want_error_type = event_type is None or "error" in event_type
    want_sessions = want_session_type or want_error_type
    want_notification_type = event_type is None or "notification" in event_type
    want_notifications = want_notification_type or want_error_type
    only_failed_notifications = want_error_type and not want_notification_type
    only_errors: bool | None = None
    if want_session_type and not want_error_type:
        only_errors = False
    elif want_error_type and not want_session_type:
        only_errors = True
    return want_sessions, want_notifications, only_errors, only_failed_notifications


def _availability(expected: int, healthy: int) -> str:
    if healthy == expected:
        return "complete"
    if healthy == 0:
        return "unavailable"
    return "partial"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Event builders — convert read-model DTOs to TimelineEvent response models
# ---------------------------------------------------------------------------


def _session_dto_to_event(dto: TimelineSessionRow) -> TimelineEvent:
    """Convert a TimelineSessionRow DTO (timeline_v1) to a TimelineEvent."""
    event_type = "error" if dto.success is False else "session"
    summary = derive_session_summary(dto.prompt, trigger_source=dto.trigger_source)
    machine_class = derive_session_machine_class(dto.trigger_source)

    return TimelineEvent(
        id=dto.id,
        type=event_type,
        butler=dto.butler or "",
        timestamp=dto.started_at,
        summary=summary,
        machine_class=machine_class,
        is_heartbeat=machine_class == "heartbeat",
        data={
            "trigger_source": dto.trigger_source,
            "success": dto.success,
            "duration_ms": dto.duration_ms,
            "completed_at": dto.completed_at.isoformat() if dto.completed_at else None,
        },
    )


def _notification_dto_to_event(dto: TimelineNotificationRow) -> TimelineEvent:
    """Convert a TimelineNotificationRow DTO (timeline_v1) to a TimelineEvent."""
    message = dto.message or ""
    summary = message[:120] + ("..." if len(message) > 120 else "")

    return TimelineEvent(
        id=dto.id,
        type="notification",
        butler=dto.source_butler,
        timestamp=dto.created_at,
        summary=summary,
        data={
            "channel": dto.channel,
            "recipient": dto.recipient,
            "status": dto.status,
            "source_butler": dto.source_butler,
        },
    )


# ---------------------------------------------------------------------------
# Backward-compatible shims for tests that import the old function names
# ---------------------------------------------------------------------------


def _session_to_event(row, *, butler: str) -> TimelineEvent:  # noqa: ANN001
    """Legacy shim — raw asyncpg Record accepted.  New code: use :func:`_session_dto_to_event`."""
    from butlers.api.read_models.timeline_v1 import _row_to_session  # noqa: PLC0415

    dto = _row_to_session(row, butler=butler)
    return _session_dto_to_event(dto)


# ---------------------------------------------------------------------------
# GET /api/timeline — cross-butler event stream
# ---------------------------------------------------------------------------


@router.get("", response_model=TimelineResponse)
async def list_timeline(
    event: UUID | None = Query(
        None,
        description=(
            "Resolve one persisted event identifier independently of the ordinary head page. "
            "The active butler, type, and trace filters still apply."
        ),
    ),
    before: str | None = Query(
        None,
        description=(
            "Pagination cursor from the previous page's ``meta.cursor`` field: "
            "an opaque composite (timestamp, id) keyset position. A bare "
            "ISO-8601 timestamp is also accepted for backward compatibility, "
            "without the same-timestamp tiebreak the composite cursor provides."
        ),
    ),
    limit: int = Query(50, ge=1, le=200, description="Max events to return"),
    butler: list[str] | None = Query(None, description="Filter by butler name(s)"),
    event_type: list[str] | None = Query(None, description="Filter by event type(s)"),
    trace: str | None = Query(
        None,
        description=(
            "Filter to events carrying this OpenTelemetry trace ID. "
            "Trace-scoped results include matching sessions and notifications "
            "that carry the trace."
        ),
    ),
    since: datetime | None = Query(None, description="Inclusive UTC minute interval bound"),
    until: datetime | None = Query(None, description="Exclusive UTC minute interval bound"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TimelineResponse:
    """Return a cursor-paginated cross-butler event stream.

    Fans out to all butler databases to fetch sessions and to the
    Switchboard database for notifications, then merges and sorts them
    by ``(timestamp, id)`` descending.

    Cursor-based pagination: pass ``before`` (the previous page's
    ``meta.cursor``) to fetch the next page. The response includes
    ``meta.cursor`` for the subsequent page and ``meta.has_more`` to
    indicate if more exist. The composite ``(timestamp, id)`` keyset avoids
    dropping events that share the cursor's exact boundary timestamp (e.g.
    simultaneous heartbeat ticks across butlers).

    ``event_type`` filtering (``session``/``error``/``notification``) is
    applied in SQL, not after the fact — so ``has_more``/pagination are
    always computed over the actually-matching set, and filtering to
    ``error`` can reach every error, not just those among the newest
    unfiltered page.

    ``trace`` filters both sessions and notifications by OpenTelemetry trace
    ID, so the trace scope never mixes unrelated timeline rows into the
    response.

    ``meta.degraded_sources`` lists any of ``sessions``/``notifications``
    whose query failed this request, while ``meta.degraded_butlers`` names
    failed session pools. The returned page for either state is partial, not a
    truthful empty or complete-fleet result (mirrors the
    ``aggregates_available`` degraded-mode convention, applied per source).
    """
    since, until = _validate_interval(since, until, required=False)
    before_ts: datetime | None = None
    before_id: UUID | None = None
    if before is not None:
        try:
            before_ts, before_id = decode_cursor(before)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'before' cursor: {exc}") from exc

    trace_id = trace.strip() if trace is not None and trace.strip() else None

    # Determine which event sources/types to query.
    # Errors lens widening: a failed delivery is an error the owner must see, so
    # ``event_type=error`` now selects failed notifications alongside failed
    # sessions (previously "error" mapped solely to sessions with success=False,
    # leaving a multi-hour bounced-alert outage invisible to the Errors view).
    want_sessions, want_notifications, only_errors, only_failed_notifications = _source_selection(
        event_type
    )
    # When notifications are pulled ONLY because of the error lens (not an
    # explicit notification request), restrict them to failed deliveries so the
    # Errors view stays errors-only.
    # Push the session/error split into SQL (only_errors) instead of fetching
    # the newest sessions and filtering the derived type afterward — the old
    # post-query filter under-reported ("error" only ever saw failures among
    # the newest unfiltered page) and computed has_more over the wrong set.
    target_butlers = butler if butler else None
    events: list[TimelineEvent] = []
    degraded_sources: list[str] = []
    degraded_butlers: list[str] = []

    # --- Sessions — via versioned timeline read-model boundary (timeline_v1) ---
    if want_sessions:
        # Fetch more than limit per butler to account for merging; trim after merge
        session_dtos, degraded_butlers = await query_timeline_sessions_fan_out(
            db,
            event_id=event,
            before=before_ts,
            before_id=before_id,
            limit=limit + 1,
            butler_names=target_butlers,
            only_errors=only_errors,
            trace_id=trace_id,
            since=since,
            until=until,
        )
        if degraded_butlers:
            logger.warning("Timeline session fan-out degraded for butlers: %s", degraded_butlers)
            degraded_sources.append("sessions")
        for dto in session_dtos:
            events.append(_session_dto_to_event(dto))

    # --- Notifications — via versioned timeline read-model boundary (timeline_v1) ---
    if want_notifications:
        # Notifications live in the switchboard DB (single-pool, not fan-out)
        try:
            pool = db.pool("switchboard")
            notif_dtos = await query_timeline_notifications_single(
                pool,
                event_id=event,
                before=before_ts,
                before_id=before_id,
                limit=limit + 1,
                source_butlers=target_butlers,
                only_failed=only_failed_notifications,
                trace_id=trace_id,
                since=since,
                until=until,
            )
            for dto in notif_dtos:
                events.append(_notification_dto_to_event(dto))
        except KeyError:
            # Switchboard DB is not configured in this deployment; benign — skip
            # notifications for the legacy head list. An exact persisted-ID
            # lookup cannot honestly turn that missing source into not-found.
            if event is not None:
                degraded_sources.append("notifications")
                logger.warning("Switchboard pool not available for exact Timeline lookup")
            else:
                logger.debug("Switchboard pool not available; skipping notifications")
        except Exception:
            # A real notification-query failure: the timeline still returns its
            # other event sources (partial, non-breaking), but the failure must
            # not be invisible to the caller — surface it via degraded_sources
            # (in addition to the existing warning log).
            logger.warning(
                "Notification sub-query failed; returning timeline without notifications",
                exc_info=True,
            )
            degraded_sources.append("notifications")

    # --- Merge and sort — (timestamp, id) descending so the composite cursor's
    # tiebreak is consistent with the order rows were actually returned in ---
    events.sort(key=lambda e: (e.timestamp, e.id), reverse=True)

    # Apply limit + compute pagination metadata
    has_more = len(events) > limit
    page = events[:limit]

    cursor: str | None = None
    if has_more and page:
        cursor = encode_cursor(page[-1].timestamp, page[-1].id)

    heartbeat_events = [e for e in page if e.is_heartbeat]
    heartbeat_rollup = TimelineHeartbeatRollup(
        ticks=len(heartbeat_events),
        butlers=len({e.butler for e in heartbeat_events}),
        failed=sum(1 for e in heartbeat_events if e.data.get("success") is False),
    )

    return TimelineResponse(
        data=page,
        meta=TimelineMeta(
            cursor=cursor,
            has_more=has_more,
            heartbeat_rollup=heartbeat_rollup,
            degraded_sources=degraded_sources,
            degraded_butlers=degraded_butlers,
        ),
    )


@router.get("/histogram", response_model=TimelineHistogramResponse)
async def timeline_histogram(
    since: datetime = Query(..., description="Inclusive timezone-aware UTC minute"),
    until: datetime = Query(..., description="Exclusive timezone-aware UTC minute"),
    butler: list[str] | None = Query(None, description="Filter by butler name(s)"),
    event_type: list[str] | None = Query(None, description="Filter by event type(s)"),
    trace: str | None = Query(None, description="Filter by OpenTelemetry trace ID"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TimelineHistogramResponse:
    """Return content-blind, server-counted minute density for a bounded interval."""
    validated_since, validated_until = _validate_interval(since, until, required=True)
    assert validated_since is not None and validated_until is not None
    trace_id = trace.strip() if trace is not None and trace.strip() else None
    want_sessions, want_notifications, only_errors, only_failed_notifications = _source_selection(
        event_type
    )
    target_butlers = list(dict.fromkeys(butler)) if butler else list(db.butler_names)

    expected_sources = (len(target_butlers) if want_sessions else 0) + (
        1 if want_notifications else 0
    )
    healthy_sources = 0
    degraded_sources: list[str] = []
    degraded_butlers: list[str] = []
    counts: dict[datetime, int] = {}

    if want_sessions:
        session_counts, degraded_butlers = await query_timeline_session_histogram_fan_out(
            db,
            since=validated_since,
            until=validated_until,
            butler_names=target_butlers,
            only_errors=only_errors,
            trace_id=trace_id,
        )
        healthy_sources += len(target_butlers) - len(degraded_butlers)
        if degraded_butlers:
            degraded_sources.append("sessions")
        for item in session_counts:
            counts[item.start.astimezone(UTC)] = (
                counts.get(item.start.astimezone(UTC), 0) + item.count
            )

    if want_notifications:
        try:
            notification_counts = await query_timeline_notification_histogram_single(
                db.pool("switchboard"),
                since=validated_since,
                until=validated_until,
                source_butlers=list(dict.fromkeys(butler)) if butler else None,
                only_failed=only_failed_notifications,
                trace_id=trace_id,
            )
            healthy_sources += 1
            for item in notification_counts:
                counts[item.start.astimezone(UTC)] = (
                    counts.get(item.start.astimezone(UTC), 0) + item.count
                )
        except Exception:
            logger.warning("Timeline histogram notification source unavailable", exc_info=True)
            degraded_sources.append("notifications")

    buckets: list[TimelineHistogramBucket] = []
    bucket_start = validated_since
    while bucket_start < validated_until:
        bucket_end = bucket_start + timedelta(minutes=1)
        buckets.append(
            TimelineHistogramBucket(
                start=bucket_start, end=bucket_end, count=counts.get(bucket_start, 0)
            )
        )
        bucket_start = bucket_end

    return TimelineHistogramResponse(
        data=buckets,
        meta=TimelineHistogramMeta(
            since=validated_since,
            until=validated_until,
            availability=_availability(expected_sources, healthy_sources),
            expected_sources=expected_sources,
            healthy_sources=healthy_sources,
            degraded_sources=list(dict.fromkeys(degraded_sources)),
            degraded_butlers=degraded_butlers,
        ),
    )


@router.get("/attention", response_model=TimelineAttentionResponse)
async def timeline_attention(
    butler: list[str] | None = Query(None, description="Filter by butler name(s)"),
    trace: str | None = Query(None, description="Filter by OpenTelemetry trace ID"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TimelineAttentionResponse:
    """Return recent records that are currently marked failed.

    The server chooses one 24-hour creation window for the complete response.
    Counts are source-local and independent of the five identifier rows kept
    for inspection, so truncation never changes the aggregate signal.
    """
    until = datetime.now(tz=UTC)
    since = until - timedelta(hours=24)
    trace_id = trace.strip() if trace is not None and trace.strip() else None
    target_butlers = list(dict.fromkeys(butler)) if butler is not None else list(db.butler_names)
    notification_butlers = target_butlers if butler is not None else None

    (
        session_rows,
        session_counts,
        degraded_butlers,
    ) = await query_timeline_attention_sessions_fan_out(
        db,
        since=since,
        until=until,
        butler_names=target_butlers,
        trace_id=trace_id,
    )
    failed_sessions = sum(session_counts.get(name, 0) for name in target_butlers)
    healthy_sources = len(target_butlers) - len(degraded_butlers)
    degraded_sources: list[str] = ["sessions"] if degraded_butlers else []

    attention_rows = session_rows
    failed_notifications = 0
    try:
        (
            notification_rows,
            failed_notifications,
        ) = await query_timeline_attention_notifications_single(
            db.pool("switchboard"),
            since=since,
            until=until,
            source_butlers=notification_butlers,
            trace_id=trace_id,
        )
        attention_rows = [*attention_rows, *notification_rows]
        healthy_sources += 1
    except Exception:
        degraded_sources.append("notifications")
        logger.warning("Timeline attention notification source unavailable", exc_info=True)

    total = failed_sessions + failed_notifications
    attention_rows.sort(key=lambda item: (item.timestamp, item.kind, str(item.id)), reverse=True)
    limited_rows = attention_rows[:TIMELINE_ATTENTION_LIMIT]
    expected_sources = len(target_butlers) + 1

    return TimelineAttentionResponse(
        data=[
            TimelineAttentionItem(
                id=item.id,
                kind=item.kind,
                butler=item.butler,
                timestamp=item.timestamp,
            )
            for item in limited_rows
        ],
        meta=TimelineAttentionMeta(
            since=since,
            until=until,
            failed_sessions=failed_sessions,
            failed_notifications=failed_notifications,
            total=total,
            has_more=total > len(limited_rows),
            availability=_availability(expected_sources, healthy_sources),
            expected_sources=expected_sources,
            healthy_sources=healthy_sources,
            degraded_sources=list(dict.fromkeys(degraded_sources)),
            degraded_butlers=degraded_butlers,
        ),
    )
