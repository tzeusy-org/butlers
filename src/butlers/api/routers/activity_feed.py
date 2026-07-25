"""Activity feed endpoint — butler-scoped cross-source event stream.

Provides:

- ``router`` — butler-scoped activity feed at
  ``GET /api/butlers/{name}/activity-feed``

Merges three event sources from the butler's database into a single
time-ordered list:

- ``sessions`` (completed_at DESC) → ``event_type = "session_completed"``
- ``pending_actions`` (requested_at DESC) → ``event_type = "approval_raised"``
- ``episodes`` (created_at DESC) → ``event_type = "memory_write"``

Each source is queried independently. Missing session or approval tables are
silently skipped. A missing memory episodes table is skipped only when the
entire memory schema was absent at API startup; post-start or unknown memory
source loss returns an unavailable response rather than reading a same-named
public shadow. Results are merged and sorted in application code, then capped
at ``limit``.

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
from butlers.api.routers.memory import (
    _is_missing_memory_schema_error,
    _memory_relation,
    _memory_schema_absent_at_start,
)
from butlers.api.session_presentation import derive_session_summary

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
    summary = derive_session_summary(row.prompt, trigger_source=row.trigger_source)
    return ActivityEvent(
        event_type="session_completed",
        ts=_normalize_tz(row.completed_at),
        summary=summary,
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

    Each source is queried independently. Missing sessions and pending-actions
    tables are skipped. A missing episodes table is skipped only when the
    memory schema was absent at startup; later or unknown source loss returns
    503 so the raw feed cannot look healthy while omitting memory writes.
    Results are merged in application code, sorted by ``ts`` descending, and
    capped at ``limit`` (default 10, max 50).

    SQL projections are governed by the v1 read-model contract in
    :mod:`butlers.api.read_models.activity_v1`.

    Returns 503 when the butler's database pool is not registered or its
    memory episode source is unavailable.
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
    try:
        episode_rows = await query_activity_episodes(
            pool,
            limit,
            episodes_relation=_memory_relation(db, name, "episodes"),
            suppress_undefined_table=False,
        )
    except Exception as exc:
        if _is_missing_memory_schema_error(
            exc,
            schema_absent_at_start=_memory_schema_absent_at_start(db, name),
        ):
            logger.debug(
                "Skipping activity memory source for %s; memory schema was absent at startup",
                name,
                exc_info=True,
            )
            episode_rows = []
        else:
            logger.warning(
                "Activity feed memory episode source for %s is unavailable",
                name,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Butler '{name}' activity feed is unavailable because its memory episode "
                    "source could not be queried"
                ),
            ) from exc
    for row in episode_rows:
        events.append(_episode_to_event(row))

    # Merge and sort by ts descending, cap at limit.
    # Use a timezone-aware sentinel so naive/aware comparisons never raise.
    events.sort(key=lambda e: e.ts or datetime.min.replace(tzinfo=UTC), reverse=True)
    return ActivityFeed(events=events[:limit])
