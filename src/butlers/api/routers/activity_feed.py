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
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.activity_feed import ActivityEvent, ActivityFeed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "activity-feed"])

_LIMIT_DEFAULT = 10
_LIMIT_MAX = 50


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _session_to_event(row) -> ActivityEvent:
    """Convert a sessions row to an ActivityEvent."""
    prompt = row["prompt"] or ""
    summary = (prompt[:120] + "...") if len(prompt) > 120 else prompt
    return ActivityEvent(
        event_type="session_completed",
        ts=row["completed_at"],
        summary=summary or "Session completed",
        entity_id=str(row["id"]),
        metadata={
            "trigger_source": row["trigger_source"],
            "success": row["success"],
            "duration_ms": row["duration_ms"],
        },
    )


def _action_to_event(row) -> ActivityEvent:
    """Convert a pending_actions row to an ActivityEvent."""
    agent_summary = row["agent_summary"] or ""
    tool_name = row["tool_name"] or ""
    summary = agent_summary or f"Approval requested: {tool_name}"
    if len(summary) > 120:
        summary = summary[:120] + "..."
    return ActivityEvent(
        event_type="approval_raised",
        ts=row["requested_at"],
        summary=summary,
        entity_id=str(row["id"]),
        metadata={
            "tool_name": tool_name,
            "status": row["status"],
            "session_id": str(row["session_id"]) if row["session_id"] else None,
        },
    )


def _episode_to_event(row) -> ActivityEvent:
    """Convert an episodes row to an ActivityEvent."""
    content = row["content"] or ""
    summary = (content[:120] + "...") if len(content) > 120 else content
    return ActivityEvent(
        event_type="memory_write",
        ts=row["created_at"],
        summary=summary or "Memory episode written",
        entity_id=str(row["id"]),
        metadata={
            "importance": row["importance"],
            "consolidation_status": row["consolidation_status"],
            "session_id": str(row["session_id"]) if row["session_id"] else None,
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
    try:
        rows = await pool.fetch(
            "SELECT id, prompt, trigger_source, success, started_at, completed_at, duration_ms "
            "FROM sessions "
            "WHERE completed_at IS NOT NULL "
            "ORDER BY completed_at DESC "
            "LIMIT $1",
            limit,
        )
        for row in rows:
            events.append(_session_to_event(row))
    except UndefinedTableError:
        logger.debug("sessions table not found for butler '%s'; skipping", name)

    # --- Pending actions ---
    try:
        rows = await pool.fetch(
            "SELECT id, tool_name, agent_summary, status, requested_at, session_id "
            "FROM pending_actions "
            "ORDER BY requested_at DESC "
            "LIMIT $1",
            limit,
        )
        for row in rows:
            events.append(_action_to_event(row))
    except UndefinedTableError:
        logger.debug("pending_actions table not found for butler '%s'; skipping", name)

    # --- Memory episodes ---
    try:
        rows = await pool.fetch(
            "SELECT id, content, importance, consolidation_status, created_at, session_id "
            "FROM episodes "
            "ORDER BY created_at DESC "
            "LIMIT $1",
            limit,
        )
        for row in rows:
            events.append(_episode_to_event(row))
    except UndefinedTableError:
        logger.debug("episodes table not found for butler '%s'; skipping", name)

    # Merge and sort by ts descending, cap at limit.
    # Use a timezone-aware sentinel so naive/aware comparisons never raise.
    events.sort(key=lambda e: e.ts or datetime.min.replace(tzinfo=UTC), reverse=True)
    return ActivityFeed(events=events[:limit])
