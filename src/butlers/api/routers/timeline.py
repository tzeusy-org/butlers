"""Timeline endpoint — cross-butler unified event stream.

Provides:

- ``router`` — timeline endpoint at ``GET /api/timeline``

Merges sessions and notifications from all butler databases into a single
time-ordered event stream using ``DatabaseManager.fan_out()``. Supports
cursor-based pagination (``before`` timestamp + ``limit``) and filtering
by butler and event type.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.timeline import TimelineEvent, TimelineMeta, TimelineResponse

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
# Event builders
# ---------------------------------------------------------------------------


def _session_to_event(row, *, butler: str) -> TimelineEvent:
    """Convert a session row to a TimelineEvent."""
    success = row["success"]
    event_type = "error" if success is False else "session"

    summary = _derive_session_summary(row["prompt"] or "", trigger_source=row["trigger_source"])

    return TimelineEvent(
        id=row["id"],
        type=event_type,
        butler=butler,
        timestamp=row["started_at"],
        summary=summary,
        data={
            "trigger_source": row["trigger_source"],
            "success": success,
            "duration_ms": row["duration_ms"],
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        },
    )


def _notification_to_event(row, *, butler: str) -> TimelineEvent:
    """Convert a notification row to a TimelineEvent."""
    message = row["message"] or ""
    summary = message[:120] + ("..." if len(message) > 120 else "")

    return TimelineEvent(
        id=row["id"],
        type="notification",
        butler=butler,
        timestamp=row["created_at"],
        summary=summary,
        data={
            "channel": row["channel"],
            "recipient": row["recipient"],
            "status": row["status"],
            "source_butler": row["source_butler"],
        },
    )


# ---------------------------------------------------------------------------
# GET /api/timeline — cross-butler event stream
# ---------------------------------------------------------------------------


@router.get("", response_model=TimelineResponse)
async def list_timeline(
    before: datetime | None = Query(
        None, description="Cursor: only return events before this timestamp"
    ),
    limit: int = Query(50, ge=1, le=200, description="Max events to return"),
    butler: list[str] | None = Query(None, description="Filter by butler name(s)"),
    event_type: list[str] | None = Query(None, description="Filter by event type(s)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TimelineResponse:
    """Return a cursor-paginated cross-butler event stream.

    Fans out to all butler databases to fetch sessions and to the
    Switchboard database for notifications, then merges and sorts them
    by timestamp descending.

    Cursor-based pagination: pass ``before`` (ISO timestamp) to fetch
    the next page. The response includes ``meta.cursor`` for the
    subsequent page and ``meta.has_more`` to indicate if more exist.
    """
    # Determine which event sources to query
    want_sessions = event_type is None or "session" in event_type or "error" in event_type
    want_notifications = event_type is None or "notification" in event_type

    target_butlers = butler if butler else None
    events: list[TimelineEvent] = []

    # --- Sessions ---
    if want_sessions:
        conditions = []
        args: list[object] = []
        idx = 1

        if before is not None:
            conditions.append(f"started_at < ${idx}")
            args.append(before)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        # Fetch more than limit to account for merging; we trim after merge
        session_sql = (
            f"SELECT id, prompt, trigger_source, success, started_at, "
            f"completed_at, duration_ms "
            f"FROM sessions{where} "
            f"ORDER BY started_at DESC "
            f"LIMIT {limit + 1}"
        )

        results = await db.fan_out(session_sql, tuple(args), butler_names=target_butlers)

        for butler_name, rows in results.items():
            for row in rows:
                ev = _session_to_event(row, butler=butler_name)
                # If filtering by event_type, check the derived type
                if event_type is not None and ev.type not in event_type:
                    continue
                events.append(ev)

    # --- Notifications ---
    if want_notifications:
        conditions_n = []
        args_n: list[object] = []
        idx_n = 1

        if before is not None:
            conditions_n.append(f"created_at < ${idx_n}")
            args_n.append(before)
            idx_n += 1

        if target_butlers is not None:
            # Filter by source_butler matching any of the requested butlers
            placeholders = ", ".join(f"${idx_n + i}" for i in range(len(target_butlers)))
            conditions_n.append(f"source_butler IN ({placeholders})")
            args_n.extend(target_butlers)
            idx_n += len(target_butlers)

        where_n = (" WHERE " + " AND ".join(conditions_n)) if conditions_n else ""

        notif_sql = (
            f"SELECT id, source_butler, channel, recipient, message, status, created_at "
            f"FROM notifications{where_n} "
            f"ORDER BY created_at DESC "
            f"LIMIT {limit + 1}"
        )

        # Notifications live in the switchboard DB
        try:
            pool = db.pool("switchboard")
            rows = await pool.fetch(notif_sql, *args_n)
            for row in rows:
                events.append(_notification_to_event(row, butler=row["source_butler"]))
        except (KeyError, Exception):
            # Switchboard DB may not be available; skip notifications gracefully
            logger.debug("Switchboard pool not available; skipping notifications")

    # --- Merge and sort ---
    events.sort(key=lambda e: e.timestamp, reverse=True)

    # Apply limit + compute pagination metadata
    has_more = len(events) > limit
    page = events[:limit]

    cursor: str | None = None
    if has_more and page:
        cursor = page[-1].timestamp.isoformat()

    return TimelineResponse(data=page, meta=TimelineMeta(cursor=cursor, has_more=has_more))
