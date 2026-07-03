"""Timeline endpoint — cross-butler unified event stream.

Provides:

- ``router`` — timeline endpoint at ``GET /api/timeline``

Merges sessions and notifications from all butler databases into a single
time-ordered event stream using ``DatabaseManager.fan_out()``. Supports
composite ``(timestamp, id)`` keyset pagination (``before`` cursor + ``limit``)
and SQL-level filtering by butler and event type.

Cross-butler fan-out reads go through the versioned read-model boundary in
``butlers.api.read_models.timeline_v1`` rather than constructing ad-hoc SQL
inline in this router.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.timeline import (
    TimelineEvent,
    TimelineHeartbeatRollup,
    TimelineMeta,
    TimelineResponse,
)
from butlers.api.read_models.timeline_v1 import (
    TimelineNotificationRow,
    TimelineSessionRow,
    decode_cursor,
    encode_cursor,
    query_timeline_notifications_single,
    query_timeline_sessions_fan_out,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Summary derivation
# ---------------------------------------------------------------------------

_SUMMARY_MAX_LEN = 120

# Inner payload of a routed message; this carries the genuine user/trigger
# intent. Switchboard fences the real message as
# ``<routed_message>\n...\n</routed_message>`` and prepends a large
# REQUEST CONTEXT / guidance envelope, so the fenced text is what we want.
# The body must not contain a nested ``<routed_message>`` open tag, otherwise a
# guidance mention of the tag (e.g. "instructions within <routed_message>
# tags") would be captured instead of the real fenced payload.
_ROUTED_MESSAGE_RE = re.compile(
    r"<routed_message>(?P<body>(?:(?!<routed_message>).)*?)</routed_message>",
    re.DOTALL,
)

# Structured-context preamble markers prepended to a session prompt. Anything
# from the first of these onward is machine context, not human-readable intent.
_CONTEXT_PREAMBLE_MARKERS = (
    "REQUEST CONTEXT",
    "INPUT CONTEXT",
    "CONVERSATION HISTORY",
    "CONTENT SAFETY:",
    "ATTACHMENTS (",
)

# Friendly fallback labels keyed by trigger_source when no readable text
# survives stripping the structured envelope.
_TRIGGER_LABELS = {
    "route": "Routed message",
    "schedule": "Scheduled task",
    "tick": "Scheduled tick",
    "manual": "Manual trigger",
}

# trigger_source values that identify a heartbeat/tick event. Classified here
# server-side (structured data) instead of the old client-side substring sniff
# on the summary text (``summary.includes('tick')``), which folded real owner
# events like "Buy concert tickets" into the collapsed heartbeat group.
_HEARTBEAT_TRIGGER_SOURCES = frozenset({"tick", "heartbeat"})


def _truncate(text: str) -> str:
    """Collapse whitespace and cap the summary to a glanceable length."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _SUMMARY_MAX_LEN:
        return collapsed[:_SUMMARY_MAX_LEN] + "..."
    return collapsed


def _derive_session_summary(prompt: str, *, trigger_source: str | None) -> str:
    """Derive a human-readable summary from a (possibly enveloped) session prompt.

    Session prompts are composed as ``f"{context}\\n\\n{prompt}"`` where
    ``context`` is the REQUEST CONTEXT / guidance envelope and ``prompt`` is the
    genuine message fenced in ``<routed_message>`` tags. Dumping the raw prompt
    surfaces the JSON envelope ("REQUEST CONTEXT (for reply targeting ...){...")
    in the activity feed, which is unreadable. We instead:

    1. Prefer the fenced ``<routed_message>`` body (the real user/trigger text).
    2. Otherwise strip any structured-context preamble (REQUEST CONTEXT, INPUT
       CONTEXT, guidance sections) and use whatever readable text remains.
    3. Fall back to a trigger-based label when nothing readable survives.
    """
    text = prompt or ""

    # 1. Prefer the genuine routed-message payload when present.
    match = _ROUTED_MESSAGE_RE.search(text)
    if match:
        body = match.group("body").strip()
        if body:
            return _truncate(body)

    # 2. Strip the structured-context preamble. Keep only the text that precedes
    #    the first machine-context marker.
    cut = len(text)
    for marker in _CONTEXT_PREAMBLE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    stripped = text[:cut].strip()
    if stripped:
        return _truncate(stripped)

    # 3. Nothing readable survived — fall back to a trigger-based label.
    return _TRIGGER_LABELS.get(trigger_source or "", "Activity")


# ---------------------------------------------------------------------------
# Event builders — convert read-model DTOs to TimelineEvent response models
# ---------------------------------------------------------------------------


def _session_dto_to_event(dto: TimelineSessionRow) -> TimelineEvent:
    """Convert a TimelineSessionRow DTO (timeline_v1) to a TimelineEvent."""
    event_type = "error" if dto.success is False else "session"
    summary = _derive_session_summary(dto.prompt or "", trigger_source=dto.trigger_source)

    return TimelineEvent(
        id=dto.id,
        type=event_type,
        butler=dto.butler or "",
        timestamp=dto.started_at,
        summary=summary,
        is_heartbeat=dto.trigger_source in _HEARTBEAT_TRIGGER_SOURCES,
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

    ``meta.degraded_sources`` lists any of ``sessions``/``notifications``
    whose query failed this request — the returned page for that source is
    then a partial, not a truthful empty, result (mirrors the
    ``aggregates_available`` degraded-mode convention, applied per source).
    """
    before_ts: datetime | None = None
    before_id: UUID | None = None
    if before is not None:
        try:
            before_ts, before_id = decode_cursor(before)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid 'before' cursor: {exc}") from exc

    # Determine which event sources/types to query.
    want_session_type = event_type is None or "session" in event_type
    want_error_type = event_type is None or "error" in event_type
    want_sessions = want_session_type or want_error_type
    want_notifications = event_type is None or "notification" in event_type

    # Push the session/error split into SQL (only_errors) instead of fetching
    # the newest sessions and filtering the derived type afterward — the old
    # post-query filter under-reported ("error" only ever saw failures among
    # the newest unfiltered page) and computed has_more over the wrong set.
    only_errors: bool | None = None
    if want_session_type and not want_error_type:
        only_errors = False
    elif want_error_type and not want_session_type:
        only_errors = True

    target_butlers = butler if butler else None
    events: list[TimelineEvent] = []
    degraded_sources: list[str] = []

    # --- Sessions — via versioned timeline read-model boundary (timeline_v1) ---
    if want_sessions:
        # Fetch more than limit per butler to account for merging; trim after merge
        session_dtos, degraded_butlers = await query_timeline_sessions_fan_out(
            db,
            before=before_ts,
            before_id=before_id,
            limit=limit + 1,
            butler_names=target_butlers,
            only_errors=only_errors,
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
                before=before_ts,
                before_id=before_id,
                limit=limit + 1,
                source_butlers=target_butlers,
            )
            for dto in notif_dtos:
                events.append(_notification_dto_to_event(dto))
        except KeyError:
            # Switchboard DB is not configured in this deployment; benign — skip
            # notifications and return the rest of the timeline.
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
        ),
    )
