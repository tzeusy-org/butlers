"""Session log for butler daemon — append-only record of CC spawner invocations.

Each session represents one ephemeral Claude Code invocation. Sessions are
created when a trigger fires and completed when the CC instance returns.
The session log is append-only: after creation the only mutation is
``session_complete``, which fills in the result fields and sets completed_at.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
from croniter import croniter

logger = logging.getLogger(__name__)

# Valid trigger_source base values (schedule uses pattern "schedule:<task-name>")
TRIGGER_SOURCES = frozenset({"tick", "external", "trigger"})

# JSONB columns that need deserialization from string → Python object
_JSONB_FIELDS = ("tool_calls", "cost")
_SUMMARY_PERIODS = frozenset({"today", "7d", "30d"})


def _is_valid_trigger_source(trigger_source: str) -> bool:
    """Check if trigger_source is valid.

    Valid values:
    - "tick"
    - "external"
    - "trigger"
    - "schedule:<task-name>" where task-name is any non-empty string
    """
    if trigger_source in TRIGGER_SOURCES:
        return True
    if trigger_source.startswith("schedule:") and len(trigger_source) > 9:
        return True
    return False


def _decode_row(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict, deserializing JSONB string fields."""
    d = dict(row)
    for field in _JSONB_FIELDS:
        if field in d and isinstance(d[field], str):
            d[field] = json.loads(d[field])
    return d


async def session_create(
    pool: asyncpg.Pool,
    prompt: str,
    trigger_source: str,
    trace_id: str | None = None,
    model: str | None = None,
) -> uuid.UUID:
    """Insert a new session row and return its UUID.

    Args:
        pool: asyncpg connection pool for the butler's database.
        prompt: The prompt text sent to the CC instance.
        trigger_source: What caused this session. Must be one of:
            ``"tick"``, ``"external"``, ``"trigger"``, or ``"schedule:<task-name>"``.
        trace_id: Optional OpenTelemetry trace ID for correlation.
        model: Optional model identifier used for this invocation.

    Returns:
        The UUID of the newly created session.

    Raises:
        ValueError: If ``trigger_source`` is not a recognised value.
    """
    if not _is_valid_trigger_source(trigger_source):
        raise ValueError(
            f"Invalid trigger_source {trigger_source!r}; must be 'tick', 'external', "
            f"'trigger', or 'schedule:<task-name>'"
        )

    session_id: uuid.UUID = await pool.fetchval(
        """
        INSERT INTO sessions (prompt, trigger_source, trace_id, model)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        prompt,
        trigger_source,
        trace_id,
        model,
    )
    logger.info("Session created: %s (trigger=%s, model=%s)", session_id, trigger_source, model)
    return session_id


async def session_complete(
    pool: asyncpg.Pool,
    session_id: uuid.UUID,
    output: str | None,
    tool_calls: list[dict[str, Any]],
    duration_ms: int,
    success: bool,
    error: str | None = None,
    cost: dict[str, Any] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Mark a session as completed with its outcome data.

    This is the **only** mutation allowed after creation (append-only contract).

    Args:
        pool: asyncpg connection pool for the butler's database.
        session_id: UUID of the session to complete.
        output: The textual output from the CC instance, or None on failure.
        tool_calls: List of tool call records (serialised as JSONB).
        duration_ms: Wall-clock duration of the CC invocation in milliseconds.
        success: Whether the session completed successfully.
        error: Error message if the session failed, None otherwise.
        cost: Optional cost/token usage dict (serialised as JSONB).
        input_tokens: Optional count of input tokens consumed by the session.
        output_tokens: Optional count of output tokens produced by the session.

    Raises:
        ValueError: If ``session_id`` does not match an existing session.
    """
    row = await pool.fetchval(
        """
        UPDATE sessions
        SET result        = $2,
            tool_calls    = $3::jsonb,
            duration_ms   = $4,
            cost          = $5::jsonb,
            success       = $6,
            error         = $7,
            input_tokens  = $8,
            output_tokens = $9,
            completed_at  = now()
        WHERE id = $1
        RETURNING id
        """,
        session_id,
        output,
        json.dumps(tool_calls),
        duration_ms,
        json.dumps(cost) if cost is not None else None,
        success,
        error,
        input_tokens,
        output_tokens,
    )
    if row is None:
        raise ValueError(f"Session {session_id} not found")
    logger.info(
        "Session completed: %s (%d ms, success=%s, in=%s, out=%s)",
        session_id,
        duration_ms,
        success,
        input_tokens,
        output_tokens,
    )


async def sessions_list(
    pool: asyncpg.Pool,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return a paginated list of sessions ordered by started_at DESC.

    Args:
        pool: asyncpg connection pool for the butler's database.
        limit: Maximum number of sessions to return.
        offset: Number of sessions to skip (for pagination).

    Returns:
        List of session records as dicts.
    """
    rows = await pool.fetch(
        """
        SELECT id, prompt, trigger_source, result, tool_calls,
               duration_ms, trace_id, model, cost, success, error,
               input_tokens, output_tokens,
               started_at, completed_at
        FROM sessions
        ORDER BY started_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    return [_decode_row(row) for row in rows]


async def sessions_active(
    pool: asyncpg.Pool,
) -> list[dict[str, Any]]:
    """Return all currently active (in-progress) sessions.

    A session is considered active when ``completed_at IS NULL`` — it has been
    created by the spawner but the CC instance has not yet returned.

    This is the primary mechanism for the dashboard to detect running sessions.

    Args:
        pool: asyncpg connection pool for the butler's database.

    Returns:
        List of active session records as dicts, ordered by started_at DESC.
    """
    rows = await pool.fetch(
        """
        SELECT id, prompt, trigger_source, result, tool_calls,
               duration_ms, trace_id, model, cost, success, error, started_at, completed_at
        FROM sessions
        WHERE completed_at IS NULL
        ORDER BY started_at DESC
        """,
    )
    return [_decode_row(row) for row in rows]


async def sessions_get(
    pool: asyncpg.Pool,
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return a full session record by UUID, or None if not found.

    Args:
        pool: asyncpg connection pool for the butler's database.
        session_id: UUID of the session to retrieve.

    Returns:
        Session record as a dict, or None if no session with that ID exists.
    """
    row = await pool.fetchrow(
        """
        SELECT id, prompt, trigger_source, result, tool_calls,
               duration_ms, trace_id, model, cost, success, error,
               input_tokens, output_tokens,
               started_at, completed_at
        FROM sessions
        WHERE id = $1
        """,
        session_id,
    )
    if row is None:
        return None
    return _decode_row(row)


def _period_start(period: str) -> datetime:
    """Return the UTC lower-bound datetime for a summary period."""
    now = datetime.now(UTC)
    if period == "today":
        return datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    raise ValueError(f"Unsupported period: {period!r}")


def _parse_iso_date(value: str | date) -> date:
    """Parse an ISO date string or pass through a date object."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _estimate_runs_per_day(cron: str) -> float:
    """Estimate average daily run frequency from a cron expression."""
    if not croniter.is_valid(cron):
        return 0.0

    start = datetime.now(UTC)
    end = start + timedelta(days=1)
    itr = croniter(cron, start)
    count = 0

    # Hard cap protects against pathological cron expressions.
    while count < 5000:
        nxt = itr.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        if nxt > end:
            break
        count += 1
    return float(count)


async def sessions_summary(pool: asyncpg.Pool, period: str = "today") -> dict[str, Any]:
    """Return aggregate session/token stats grouped by model for a period."""
    if period not in _SUMMARY_PERIODS:
        raise ValueError(f"Invalid period {period!r}; must be one of {sorted(_SUMMARY_PERIODS)}")

    since = _period_start(period)
    totals = await pool.fetchrow(
        """
        SELECT
            COUNT(*)::bigint AS total_sessions,
            COALESCE(SUM(input_tokens), 0)::bigint AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS total_output_tokens
        FROM sessions
        WHERE started_at >= $1
        """,
        since,
    )

    by_model_rows = await pool.fetch(
        """
        SELECT
            model,
            COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens
        FROM sessions
        WHERE started_at >= $1 AND model IS NOT NULL AND model <> ''
        GROUP BY model
        ORDER BY model
        """,
        since,
    )

    by_model: dict[str, dict[str, int]] = {}
    for row in by_model_rows:
        by_model[str(row["model"])] = {
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
        }

    if totals is None:
        return {
            "period": period,
            "total_sessions": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "by_model": by_model,
        }

    return {
        "period": period,
        "total_sessions": int(totals["total_sessions"]),
        "total_input_tokens": int(totals["total_input_tokens"]),
        "total_output_tokens": int(totals["total_output_tokens"]),
        "by_model": by_model,
    }


async def sessions_daily(
    pool: asyncpg.Pool,
    from_date: str | date,
    to_date: str | date,
) -> dict[str, list[dict[str, Any]]]:
    """Return daily session/token aggregates and per-model token breakdowns."""
    from_day = _parse_iso_date(from_date)
    to_day = _parse_iso_date(to_date)
    if from_day > to_day:
        raise ValueError("from_date must be <= to_date")

    start_at = datetime.combine(from_day, datetime.min.time(), tzinfo=UTC)
    end_exclusive = datetime.combine(to_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    daily_rows = await pool.fetch(
        """
        SELECT
            (started_at AT TIME ZONE 'UTC')::date AS day,
            COUNT(*)::bigint AS sessions,
            COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens
        FROM sessions
        WHERE started_at >= $1 AND started_at < $2
        GROUP BY day
        ORDER BY day
        """,
        start_at,
        end_exclusive,
    )

    by_model_rows = await pool.fetch(
        """
        SELECT
            (started_at AT TIME ZONE 'UTC')::date AS day,
            model,
            COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens
        FROM sessions
        WHERE started_at >= $1
          AND started_at < $2
          AND model IS NOT NULL
          AND model <> ''
        GROUP BY day, model
        ORDER BY day, model
        """,
        start_at,
        end_exclusive,
    )

    by_day_model: dict[str, dict[str, dict[str, int]]] = {}
    for row in by_model_rows:
        day_key = row["day"].isoformat()
        by_day_model.setdefault(day_key, {})[str(row["model"])] = {
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
        }

    days: list[dict[str, Any]] = []
    for row in daily_rows:
        day_key = row["day"].isoformat()
        days.append(
            {
                "date": day_key,
                "sessions": int(row["sessions"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "by_model": by_day_model.get(day_key, {}),
            }
        )

    return {"days": days}


async def top_sessions(pool: asyncpg.Pool, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Return the highest-token completed sessions."""
    safe_limit = max(1, int(limit))
    rows = await pool.fetch(
        """
        SELECT
            id,
            COALESCE(model, '') AS model,
            COALESCE(input_tokens, 0)::bigint AS input_tokens,
            COALESCE(output_tokens, 0)::bigint AS output_tokens,
            started_at
        FROM sessions
        WHERE completed_at IS NOT NULL
        ORDER BY (COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) DESC, started_at DESC
        LIMIT $1
        """,
        safe_limit,
    )

    sessions: list[dict[str, Any]] = []
    for row in rows:
        started_at = row["started_at"]
        sessions.append(
            {
                "session_id": str(row["id"]),
                "model": str(row["model"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "started_at": started_at.isoformat() if started_at else "",
            }
        )
    return {"sessions": sessions}


async def schedule_costs(pool: asyncpg.Pool) -> dict[str, list[dict[str, Any]]]:
    """Return per-schedule token usage aggregates for cost analysis."""
    rows = await pool.fetch(
        """
        SELECT
            st.name,
            st.cron,
            s.model,
            COUNT(s.id)::bigint AS total_runs,
            COALESCE(SUM(s.input_tokens), 0)::bigint AS total_input_tokens,
            COALESCE(SUM(s.output_tokens), 0)::bigint AS total_output_tokens
        FROM scheduled_tasks AS st
        LEFT JOIN sessions AS s
            ON s.trigger_source = ('schedule:' || st.name)
        GROUP BY st.name, st.cron, s.model
        ORDER BY st.name, s.model
        """
    )

    schedules: list[dict[str, Any]] = []
    for row in rows:
        cron = str(row["cron"])
        schedules.append(
            {
                "name": str(row["name"]),
                "cron": cron,
                "model": "" if row["model"] is None else str(row["model"]),
                "total_runs": int(row["total_runs"]),
                "total_input_tokens": int(row["total_input_tokens"]),
                "total_output_tokens": int(row["total_output_tokens"]),
                "runs_per_day": _estimate_runs_per_day(cron),
            }
        )

    return {"schedules": schedules}
