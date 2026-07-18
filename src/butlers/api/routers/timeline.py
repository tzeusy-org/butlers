"""Timeline endpoint — cross-butler unified event stream.

Provides:

- ``router`` — timeline endpoint at ``GET /api/timeline``

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

# Inner payload of a message fence; this carries the genuine user/trigger
# intent. Switchboard fences the real message as
# ``<routed_message>\n...\n</routed_message>`` and prepends a large
# REQUEST CONTEXT / guidance envelope; other producers wrap presence/status
# updates as ``<user_message>...</user_message>`` (sometimes inline, e.g.
# "The user reports: <user_message>Status changed to away</user_message>"). The
# fenced text is the human-readable content in all of these, so we unwrap a
# fixed *family* of known fence tags rather than dumping the machine wrapper.
#
# The family is an explicit allowlist (not "any XML tag") so a stray guidance
# mention of some other tag can never be mistaken for real content. Each body
# must not contain a nested open of *its own* tag, otherwise a guidance mention
# (e.g. "instructions within <routed_message> tags") could capture across it.
_FENCE_TAGS = ("routed_message", "user_message")
_FENCE_RE = re.compile(
    r"<(?P<tag>" + "|".join(_FENCE_TAGS) + r")>"
    r"(?P<body>(?:(?!<(?P=tag)>).)*?)"
    r"</(?P=tag)>",
    re.DOTALL,
)

# Skill-dispatch preamble: ingestion/classification sessions open with
# "Please use the /<skill> skill to ..." followed by pages of tool-calling
# instructions. That machine preamble is not human intent — collapse it to a
# readable label derived from the skill slug (e.g. "/message-triage" ->
# "Message triage"). Anchored at the start so a fenced body that merely mentions
# a skill is never mistaken for a dispatch preamble.
_SKILL_PREAMBLE_RE = re.compile(
    r"^\s*Please use the /(?P<skill>[a-z0-9][a-z0-9-]*) skill\b",
    re.IGNORECASE,
)

# QA-canary sessions are spawned with a system prompt that opens with a fixed
# sentinel. Dumping that prompt renders the canary as an opaque (and, when the
# session fails, error-badged) row describing the QA agent's own instructions
# instead of the household event it stands in for. Map the sentinel to a short
# operational label keyed by prompt prefix.
_QA_PROMPT_LABELS = (
    ("You are a QA investigation agent for the butler system.", "QA patrol investigation"),
    ("You are a QA review follow-up agent.", "QA review follow-up"),
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
    "classification": "Switchboard classification",
    "manual": "Manual trigger",
}

# trigger_source values that identify a heartbeat/tick event. Classified here
# server-side (structured data) instead of the old client-side substring sniff
# on the summary text (``summary.includes('tick')``), which folded real owner
# events like "Buy concert tickets" into the collapsed heartbeat group.
#
# "classification" is switchboard's routing-decision trigger_source
# (bu-qvnce.12 renamed it from the historical "tick" — both values identify
# the same operational-telemetry call site, so both collapse into the same
# heartbeat group; "tick" is kept for rows recorded before the rename).
_HEARTBEAT_TRIGGER_SOURCES = frozenset({"tick", "classification", "heartbeat"})


def _truncate(text: str) -> str:
    """Collapse whitespace and cap the summary to a glanceable length."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _SUMMARY_MAX_LEN:
        return collapsed[:_SUMMARY_MAX_LEN] + "..."
    return collapsed


def _skill_label(skill_slug: str) -> str:
    """Humanize a skill slug into a trigger label ("message-triage" -> "Message triage")."""
    return skill_slug.replace("-", " ").strip().capitalize()


def _derive_session_summary(prompt: str, *, trigger_source: str | None) -> str:
    """Derive a human-readable summary from a (possibly enveloped) session prompt.

    Session prompts are machine text as often as not: routed messages carry a
    REQUEST CONTEXT / guidance envelope with the real message fenced in
    ``<routed_message>`` (or ``<user_message>``) tags; ingestion/classification
    sessions open with a "Please use the /<skill> skill ..." dispatch preamble;
    QA-canary sessions carry a fixed system prompt. Dumping the raw prompt
    surfaces that plumbing in the chronicle's primary column, which promises
    every household event in human terms. We instead:

    1. Label QA-canary sessions by their system-prompt sentinel.
    2. Collapse a "Please use the /<skill> skill" dispatch preamble to a
       readable trigger label derived from the skill slug.
    3. Prefer a fenced message body (``<routed_message>`` / ``<user_message>``)
       — the real user/trigger text — when present.
    4. Otherwise strip any structured-context preamble (REQUEST CONTEXT, INPUT
       CONTEXT, guidance sections) and use whatever readable text remains.
    5. Fall back to a trigger-based label when nothing readable survives.
    """
    text = prompt or ""
    lead = text.lstrip()

    # 1. QA-canary system prompt — label it, never dump its instructions.
    for sentinel, label in _QA_PROMPT_LABELS:
        if lead.startswith(sentinel):
            return label

    # 2. Skill-dispatch preamble — collapse to a trigger label.
    skill_match = _SKILL_PREAMBLE_RE.match(lead)
    if skill_match:
        return _skill_label(skill_match.group("skill"))

    # 3. Prefer the genuine fenced message payload when present.
    match = _FENCE_RE.search(text)
    if match:
        body = match.group("body").strip()
        if body:
            return _truncate(body)

    # 4. Strip the structured-context preamble. Keep only the text that precedes
    #    the first machine-context marker.
    cut = len(text)
    for marker in _CONTEXT_PREAMBLE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    stripped = text[:cut].strip()
    if stripped:
        return _truncate(stripped)

    # 5. Nothing readable survived — fall back to a trigger-based label.
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
    trace: str | None = Query(
        None,
        description=(
            "Filter to events carrying this OpenTelemetry trace ID. "
            "Trace-scoped results include matching sessions and notifications "
            "that carry the trace."
        ),
    ),
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

    trace_id = trace.strip() if trace is not None and trace.strip() else None

    # Determine which event sources/types to query.
    want_session_type = event_type is None or "session" in event_type
    want_error_type = event_type is None or "error" in event_type
    want_sessions = want_session_type or want_error_type
    want_notification_type = event_type is None or "notification" in event_type

    # Errors lens widening: a failed delivery is an error the owner must see, so
    # ``event_type=error`` now selects failed notifications alongside failed
    # sessions (previously "error" mapped solely to sessions with success=False,
    # leaving a multi-hour bounced-alert outage invisible to the Errors view).
    want_notifications = want_notification_type or want_error_type
    # When notifications are pulled ONLY because of the error lens (not an
    # explicit notification request), restrict them to failed deliveries so the
    # Errors view stays errors-only.
    only_failed_notifications = want_error_type and not want_notification_type

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
            trace_id=trace_id,
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
                only_failed=only_failed_notifications,
                trace_id=trace_id,
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
