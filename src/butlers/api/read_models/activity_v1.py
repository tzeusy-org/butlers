"""Activity-feed read-model v1 — versioned read boundary for the butler activity feed.

Centralises the SQL column projections and per-pool query functions for the
butler-scoped activity feed endpoint, which merges sessions, pending_actions,
and memory episodes from a single butler's database into a time-ordered list.

A breaking schema change (new required column, renamed column, type change)
should produce a new ``activity_v2`` module rather than silently altering
this one.

Public surface
--------------
Column constants:
    SESSION_COLUMNS
    ACTION_COLUMNS
    EPISODE_COLUMNS

Row DTOs:
    ActivitySessionRow
    ActivityActionRow
    ActivityEpisodeRow

Query functions (all async):
    query_activity_sessions(pool, limit) -> list[ActivitySessionRow]
    query_activity_actions(pool, limit) -> list[ActivityActionRow]
    query_activity_episodes(pool, limit) -> list[ActivityEpisodeRow]

Row-to-DTO converters:
    row_to_session(row) -> ActivitySessionRow
    row_to_action(row) -> ActivityActionRow
    row_to_episode(row) -> ActivityEpisodeRow
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg
from asyncpg.exceptions import UndefinedTableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``activity_v2`` for breaking changes.
READ_MODEL_VERSION = "activity_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Columns projected from ``sessions`` for the activity feed.
#: Changing this list is a breaking change — create ``activity_v2`` instead.
SESSION_COLUMNS: str = "id, prompt, trigger_source, success, started_at, completed_at, duration_ms"

#: Columns projected from ``pending_actions`` for the activity feed.
ACTION_COLUMNS: str = "id, tool_name, agent_summary, status, requested_at, session_id"

#: Columns projected from ``episodes`` for the activity feed.
EPISODE_COLUMNS: str = "id, content, importance, consolidation_status, created_at, session_id"

# ---------------------------------------------------------------------------
# Typed row DTOs
# ---------------------------------------------------------------------------


@dataclass
class ActivitySessionRow:
    """Typed DTO for a sessions row as used in the butler activity feed (v1)."""

    id: UUID
    prompt: str | None
    trigger_source: str | None
    success: bool | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


@dataclass
class ActivityActionRow:
    """Typed DTO for a pending_actions row as used in the butler activity feed (v1)."""

    id: UUID
    tool_name: str | None
    agent_summary: str | None
    status: str | None
    requested_at: datetime
    session_id: UUID | None


@dataclass
class ActivityEpisodeRow:
    """Typed DTO for an episodes row as used in the butler activity feed (v1)."""

    id: UUID
    content: str | None
    importance: float | None
    consolidation_status: str | None
    created_at: datetime
    session_id: UUID | None


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def row_to_session(row: asyncpg.Record) -> ActivitySessionRow:
    """Convert an asyncpg Record to an :class:`ActivitySessionRow`.

    This is the single place that knows the column names from :data:`SESSION_COLUMNS`.
    """
    return ActivitySessionRow(
        id=row["id"],
        prompt=row["prompt"],
        trigger_source=row["trigger_source"],
        success=row["success"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
    )


def row_to_action(row: asyncpg.Record) -> ActivityActionRow:
    """Convert an asyncpg Record to an :class:`ActivityActionRow`.

    This is the single place that knows the column names from :data:`ACTION_COLUMNS`.
    """
    return ActivityActionRow(
        id=row["id"],
        tool_name=row["tool_name"],
        agent_summary=row["agent_summary"],
        status=row["status"],
        requested_at=row["requested_at"],
        session_id=row["session_id"],
    )


def row_to_episode(row: asyncpg.Record) -> ActivityEpisodeRow:
    """Convert an asyncpg Record to an :class:`ActivityEpisodeRow`.

    This is the single place that knows the column names from :data:`EPISODE_COLUMNS`.
    """
    return ActivityEpisodeRow(
        id=row["id"],
        content=row["content"],
        importance=row["importance"],
        consolidation_status=row["consolidation_status"],
        created_at=row["created_at"],
        session_id=row["session_id"],
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_activity_sessions(
    pool: asyncpg.Pool,
    limit: int,
) -> list[ActivitySessionRow]:
    """Fetch completed session rows from a single butler's pool.

    Only sessions with a non-null ``completed_at`` are returned, ordered by
    ``completed_at DESC``.  Missing tables are silently skipped (returns empty
    list) so the endpoint degrades gracefully when the butler has no sessions
    table.

    Parameters
    ----------
    pool:
        The asyncpg pool for a specific butler.
    limit:
        Maximum rows to fetch.

    Returns
    -------
    list[ActivitySessionRow]
        Typed session DTOs, or an empty list if the table does not exist.
    """
    try:
        rows = await pool.fetch(
            f"SELECT {SESSION_COLUMNS} "
            "FROM sessions "
            "WHERE completed_at IS NOT NULL "
            "ORDER BY completed_at DESC "
            "LIMIT $1",
            limit,
        )
        return [row_to_session(r) for r in rows]
    except UndefinedTableError:
        logger.debug("sessions table not found; skipping")
        return []


async def query_activity_actions(
    pool: asyncpg.Pool,
    limit: int,
) -> list[ActivityActionRow]:
    """Fetch pending-action rows from a single butler's pool.

    Returns rows ordered by ``requested_at DESC``.  Missing tables are
    silently skipped so the endpoint degrades gracefully when the butler
    does not have the approvals module enabled.

    Parameters
    ----------
    pool:
        The asyncpg pool for a specific butler.
    limit:
        Maximum rows to fetch.

    Returns
    -------
    list[ActivityActionRow]
        Typed action DTOs, or an empty list if the table does not exist.
    """
    try:
        rows = await pool.fetch(
            f"SELECT {ACTION_COLUMNS} FROM pending_actions ORDER BY requested_at DESC LIMIT $1",
            limit,
        )
        return [row_to_action(r) for r in rows]
    except UndefinedTableError:
        logger.debug("pending_actions table not found; skipping")
        return []


async def query_activity_episodes(
    pool: asyncpg.Pool,
    limit: int,
) -> list[ActivityEpisodeRow]:
    """Fetch memory episode rows from a single butler's pool.

    Returns rows ordered by ``created_at DESC``.  Missing tables are silently
    skipped so the endpoint degrades gracefully when the butler does not have
    the memory module enabled.

    Parameters
    ----------
    pool:
        The asyncpg pool for a specific butler.
    limit:
        Maximum rows to fetch.

    Returns
    -------
    list[ActivityEpisodeRow]
        Typed episode DTOs, or an empty list if the table does not exist.
    """
    try:
        rows = await pool.fetch(
            f"SELECT {EPISODE_COLUMNS} FROM episodes ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [row_to_episode(r) for r in rows]
    except UndefinedTableError:
        logger.debug("episodes table not found; skipping")
        return []
