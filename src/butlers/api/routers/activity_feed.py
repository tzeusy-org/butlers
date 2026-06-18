"""Activity feed endpoint — butler-scoped cross-source event stream.

Provides:

- ``router`` — butler-scoped activity feed at
  ``GET /api/butlers/{name}/activity-feed``

Merges three event sources from the butler's database into a single
time-ordered list:

- ``sessions`` (completed_at DESC) → ``event_type = "session_completed"``
- ``pending_actions`` (requested_at DESC) → ``event_type = "approval_raised"``
- ``episodes`` (created_at DESC) → ``event_type = "memory_write"``

Each source is queried independently; missing tables are silently skipped
so the endpoint degrades gracefully when a butler does not have the
approvals or memory modules enabled. Results are merged and sorted in
application code, then capped at ``limit``.

SQL column projections and query functions are versioned in
:mod:`butlers.api.read_models.activity_v1`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.activity_feed import ActivityEvent, ActivityFeed
from butlers.api.read_models.activity_v1 import (
    ActivityActionRow,
    ActivityEpisodeRow,
    ActivitySessionRow,
    query_activity_actions,
    query_activity_episodes,
    query_activity_sessions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "activity-feed"])

_LIMIT_DEFAULT = 10
_LIMIT_MAX = 50


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_tz(dt: datetime | None) -> datetime | None:
    """Return *dt* with UTC tzinfo attached if it is naive, or unchanged if already aware.

    Asyncpg returns tz-aware datetimes for TIMESTAMPTZ columns, but plain
    TIMESTAMP columns (or test mocks that forget tzinfo) produce naive datetimes.
    Normalising here prevents ``TypeError: can't compare offset-naive and
    offset-aware datetimes`` when the merged event list is sorted.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _session_to_event(row: ActivitySessionRow) -> ActivityEvent:
    """Convert an :class:`ActivitySessionRow` (activity_v1) to an :class:`ActivityEvent`."""
    prompt = row.prompt or ""
    summary = (prompt[:120] + "...") if len(prompt) > 120 else prompt
    return ActivityEvent(
        event_type="session_completed",
        ts=_normalize_tz(row.completed_at),
        summary=summary or "Session completed",
        entity_id=str(row.id),
        metadata={
            "trigger_source": row.trigger_source,
            "success": row.success,
            "duration_ms": row.duration_ms,
        },
    )


def _action_to_event(row: ActivityActionRow) -> ActivityEvent:
    """Convert an :class:`ActivityActionRow` (activity_v1) to an :class:`ActivityEvent`."""
    agent_summary = row.agent_summary or ""
    tool_name = row.tool_name or ""
    summary = agent_summary or f"Approval requested: {tool_name}"
    if len(summary) > 120:
        summary = summary[:120] + "..."
    return ActivityEvent(
        event_type="approval_raised",
        ts=_normalize_tz(row.requested_at),
        summary=summary,
        entity_id=str(row.id),
        metadata={
            "tool_name": tool_name,
            "status": row.status,
            "session_id": str(row.session_id) if row.session_id else None,
        },
    )


def _episode_to_event(row: ActivityEpisodeRow) -> ActivityEvent:
    """Convert an :class:`ActivityEpisodeRow` (activity_v1) to an :class:`ActivityEvent`."""
    content = row.content or ""
    summary = (content[:120] + "...") if len(content) > 120 else content
    return ActivityEvent(
        event_type="memory_write",
        ts=_normalize_tz(row.created_at),
        summary=summary or "Memory episode written",
        entity_id=str(row.id),
        metadata={
            "importance": row.importance,
            "consolidation_status": row.consolidation_status,
            "session_id": str(row.session_id) if row.session_id else None,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/activity-feed
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/activity-feed",
    response_model=ActivityFeed,
)
async def get_activity_feed(
    name: str,
    limit: int = Query(_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX, description="Max events to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ActivityFeed:
    """Return a merged, time-ordered activity feed for a single butler.

    Queries three event sources from the butler's database:

    - Completed sessions (``session_completed``)
    - Pending actions / approval requests (``approval_raised``)
    - Memory episodes (``memory_write``)

    Each source is queried independently and missing tables are silently
    skipped.  Results are merged in application code, sorted by ``ts``
    descending, and capped at ``limit`` (default 10, max 50).

    SQL projections are governed by the v1 read-model contract in
    :mod:`butlers.api.read_models.activity_v1`.

    Returns 503 when the butler's database pool is not registered.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    events: list[ActivityEvent] = []

    # --- Sessions ---
    session_rows = await query_activity_sessions(pool, limit)
    for row in session_rows:
        events.append(_session_to_event(row))

    # --- Pending actions ---
    action_rows = await query_activity_actions(pool, limit)
    for row in action_rows:
        events.append(_action_to_event(row))

    # --- Memory episodes ---
    episode_rows = await query_activity_episodes(pool, limit)
    for row in episode_rows:
        events.append(_episode_to_event(row))

    # Merge and sort by ts descending, cap at limit.
    # Use a timezone-aware sentinel so naive/aware comparisons never raise.
    events.sort(key=lambda e: e.ts or datetime.min.replace(tzinfo=UTC), reverse=True)
    return ActivityFeed(events=events[:limit])
