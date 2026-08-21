"""Session log for butler daemon — append-only record of LLM CLI spawner invocations.

Each session represents one ephemeral LLM CLI invocation. Sessions are
created when a trigger fires and completed when the runtime instance returns.
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

# Valid trigger_source base values.
# Schedule-like sources also allow "schedule:<task-name>" and
# "deadline:<task-name>".
TRIGGER_SOURCES = frozenset(
    {"tick", "classification", "external", "trigger", "route", "healing", "dashboard", "qa"}
)


def _strip_null_bytes(value: str | None) -> str | None:
    """Strip NUL characters that PostgreSQL text columns cannot store."""
    if value is None:
        return None
    return value.replace("\x00", "")


def _strip_untranslatable_chars(value: str | None) -> str | None:
    """Remove characters PostgreSQL cannot round-trip through text/jsonb.

    PostgreSQL rejects NUL code points and lone surrogate code points when
    converting JSON escape sequences back to text. Runtime/tool payloads can
    still contain those values in Python strings, so normalize them away before
    inserting into TEXT/JSONB columns.

    Uses the C-implemented ``encode``/``decode`` fast path for surrogate
    stripping so large prompts and tool payloads do not pay a per-character
    Python loop.
    """
    if value is None:
        return None
    res = value.replace("\x00", "")
    try:
        res.encode("utf-8")
        return res
    except UnicodeEncodeError:
        return res.encode("utf-8", "ignore").decode("utf-8")


def _sanitize_json_value(value: Any) -> Any:
    """Recursively sanitize string leaves before JSON serialization."""
    if isinstance(value, str):
        return _strip_untranslatable_chars(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            (
                _strip_untranslatable_chars(key) if isinstance(key, str) else key
            ): _sanitize_json_value(val)
            for key, val in value.items()
        }
    return value


# JSONB columns that need deserialization from string → Python object
_JSONB_FIELDS = ("tool_calls", "cost")
_SUMMARY_PERIODS = frozenset({"today", "7d", "30d"})


def _is_valid_trigger_source(trigger_source: str) -> bool:
    """Check if trigger_source is valid.

    Valid values:
    - "tick"
    - "classification"
    - "external"
    - "trigger"
    - "route"
    - "healing"
    - "dashboard"
    - "qa"
    - "schedule:<task-name>" where task-name is any non-empty string
    - "deadline:<task-name>" where task-name is any non-empty string
    """
    if trigger_source in TRIGGER_SOURCES:
        return True
    if trigger_source.startswith("schedule:") and len(trigger_source) > 9:
        return True
    if trigger_source.startswith("deadline:") and len(trigger_source) > 9:
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
    *,
    request_id: str,
    ingestion_event_id: str | None = None,
    complexity: str | None = None,
    resolution_source: str | None = None,
    butler_name: str | None = None,
) -> uuid.UUID:
    """Insert a new session row and return its UUID.

    Args:
        pool: asyncpg connection pool for the butler's database.
        prompt: The prompt text sent to the runtime instance.
        trigger_source: What caused this session. Must be one of:
            ``"tick"``, ``"classification"``, ``"external"``, ``"trigger"``,
            ``"route"``, ``"healing"``, ``"dashboard"``,
            ``"schedule:<task-name>"``, or ``"deadline:<task-name>"``.
        trace_id: Optional OpenTelemetry trace ID for correlation.
        model: Optional model identifier used for this invocation.
        request_id: Required request ID for this session (UUIDv7 format).
            Connector-sourced sessions pass the UUID from the ingestion
            request_context; internal sessions (tick, schedule) generate a
            fresh UUID. Must not be None.
        ingestion_event_id: Optional UUID of the ingestion event that caused
            this session.  Connector-sourced callers pass the ingestion event
            UUID; internally-triggered sessions (tick, schedule, trigger) pass
            None.
        complexity: Optional complexity tier used for model selection (e.g.
            ``"cheap"``, ``"workhorse"``, ``"reasoning"``). Defaults to ``"workhorse"``
            at the database level when not provided.
        resolution_source: Optional source of model resolution (e.g.
            ``"catalog"``, ``"toml_fallback"``). Defaults to
            ``"toml_fallback"`` at the database level when not provided.
        butler_name: Optional owning butler name, used only to enrich the
            ``session`` event emitted onto the fleet event bus (bu-86c4c.8);
            not persisted.

    Returns:
        The UUID of the newly created session.

    Raises:
        ValueError: If ``trigger_source`` is not a recognised value.
        ValueError: If ``request_id`` is None.
    """
    if request_id is None:
        raise ValueError("request_id is required and must not be None")
    if not _is_valid_trigger_source(trigger_source):
        raise ValueError(
            f"Invalid trigger_source {trigger_source!r}; must be 'tick', "
            f"'classification', 'external', 'trigger', 'route', 'healing', "
            f"'dashboard', 'qa', 'schedule:<task-name>', or 'deadline:<task-name>'"
        )

    # Sanitize once up front so the retry path does not redo the work.
    sanitized_prompt = _strip_untranslatable_chars(prompt)

    async def _insert(resolved_ingestion_event_id: str | None) -> uuid.UUID:
        return await pool.fetchval(
            """
            INSERT INTO sessions
                (prompt, trigger_source, trace_id, model, request_id, ingestion_event_id,
                 complexity, resolution_source)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            sanitized_prompt,
            trigger_source,
            trace_id,
            model,
            request_id,
            resolved_ingestion_event_id,
            complexity,
            resolution_source,
        )

    try:
        session_id: uuid.UUID = await _insert(ingestion_event_id)
    except asyncpg.ForeignKeyViolationError as exc:
        constraint_name = getattr(exc, "constraint_name", None)
        is_ingestion_event_fk = (
            constraint_name == "sessions_ingestion_event_id_fkey"
            or "sessions_ingestion_event_id_fkey" in str(exc)
        )
        if ingestion_event_id is None or not is_ingestion_event_fk:
            raise
        logger.warning(
            "Session ingestion_event_id=%s no longer exists; creating session without "
            "ingestion-event linkage",
            ingestion_event_id,
        )
        session_id = await _insert(None)
    logger.info("Session created: %s (trigger=%s, model=%s)", session_id, trigger_source, model)

    # Fan a "session started" event onto the multiplexed fleet event bus
    # (bu-86c4c.8, move 5) via Postgres LISTEN/NOTIFY (RFC 0022, bu-01r64.1).
    # Best-effort: never let this block or fail session creation.
    session_event_data = {
        "phase": "started",
        "session_id": str(session_id),
        "butler": butler_name,
        "trigger_source": trigger_source,
        "model": model,
    }
    try:
        from butlers.fleet_events import publish_fleet_event

        await publish_fleet_event(pool, "session", session_event_data)
    except Exception:
        logger.debug("publish_fleet_event('session') failed (non-fatal)", exc_info=True)

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
    cached_input_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    butler_name: str | None = None,
) -> None:
    """Mark a session as completed with its outcome data.

    This is the **only** mutation allowed after creation (append-only contract).

    Args:
        pool: asyncpg connection pool for the butler's database.
        session_id: UUID of the session to complete.
        output: The textual output from the runtime instance, or None on failure.
        tool_calls: List of tool call records (serialised as JSONB).
        duration_ms: Wall-clock duration of the runtime invocation in milliseconds.
        success: Whether the session completed successfully.
        error: Error message if the session failed, None otherwise.
        cost: Optional cost/token usage dict (serialised as JSONB).
        input_tokens: Optional count of UNCACHED input tokens consumed by the
            session (cache reads/writes are tracked separately — see the
            runtime usage contract in ``butlers.core.runtimes.base``).
        output_tokens: Optional count of output tokens produced by the session.
        cached_input_tokens: Optional count of prompt-cache READ tokens.
        cache_creation_tokens: Optional count of prompt-cache WRITE tokens.
        butler_name: Optional owning butler name, used only to enrich the
            ``session`` event emitted onto the fleet event bus (bu-86c4c.8);
            not persisted.

    Raises:
        ValueError: If ``session_id`` does not match an existing session.
    """
    # PostgreSQL text columns reject NUL (\x00) characters.  LLM output
    # occasionally contains them (e.g. from binary-ish attachments or model
    # artefacts), so strip before writing.
    safe_output = _strip_untranslatable_chars(output)
    safe_error = _strip_untranslatable_chars(error)
    safe_tool_calls = _sanitize_json_value(tool_calls)
    safe_cost = _sanitize_json_value(cost) if cost is not None else None

    row = await pool.fetchval(
        """
        UPDATE sessions
        SET result        = $2,
            tool_calls    = $3,
            duration_ms   = $4,
            cost          = $5,
            success       = $6,
            error         = $7,
            input_tokens  = $8,
            output_tokens = $9,
            cached_input_tokens   = $10,
            cache_creation_tokens = $11,
            completed_at  = now()
        WHERE id = $1
        RETURNING id
        """,
        session_id,
        safe_output,
        safe_tool_calls,
        duration_ms,
        safe_cost,
        success,
        safe_error,
        input_tokens,
        output_tokens,
        cached_input_tokens,
        cache_creation_tokens,
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

    # Fan a "session ended" event onto the multiplexed fleet event bus
    # (bu-86c4c.8, move 5) via Postgres LISTEN/NOTIFY (RFC 0022, bu-01r64.1).
    # Best-effort: never let this block or fail session completion.
    session_event_data = {
        "phase": "ended",
        "session_id": str(session_id),
        "butler": butler_name,
        "duration_ms": duration_ms,
        "success": success,
    }
    try:
        from butlers.fleet_events import publish_fleet_event

        await publish_fleet_event(pool, "session", session_event_data)
    except Exception:
        logger.debug("publish_fleet_event('session') failed (non-fatal)", exc_info=True)


async def recover_orphaned_sessions(pool: asyncpg.Pool) -> int:
    """Close sessions left in-flight by a previous daemon process.

    Called once on daemon startup, before the spawner accepts new triggers.
    Each butler daemon is the sole writer to its own ``sessions`` table, so
    any row with ``completed_at IS NULL`` at startup is by definition an
    orphan: the previous daemon was killed (SIGKILL, OOM, container restart,
    host reboot) before reaching the spawner's normal completion path.

    Marks orphans as failed:
      - ``completed_at = now()``
      - ``success = false``
      - ``error = 'orphaned: daemon restart'`` (preserved if already set)
      - ``duration_ms`` filled from elapsed wall time if not already set

    Without this sweep, orphan rows accumulate forever and the chronicler
    sessions adapter projects each one as an open ``work`` episode that
    never closes — surfacing as multi-day-old "in-progress" sessions on
    the chronicles dashboard.
    """
    count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE sessions
            SET completed_at = now(),
                success = false,
                error = COALESCE(error, 'orphaned: daemon restart'),
                duration_ms = COALESCE(
                    duration_ms,
                    LEAST(
                        GREATEST(
                            (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::bigint,
                            0::bigint
                        ),
                        2147483647::bigint
                    )::integer
                )
            WHERE completed_at IS NULL
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """
    )
    n = int(count or 0)
    if n:
        logger.warning(
            "recover_orphaned_sessions: closed %d orphaned session(s) from prior daemon run",
            n,
        )
    return n


async def session_set_healing_fingerprint(
    pool: asyncpg.Pool,
    session_id: uuid.UUID,
    fingerprint: str,
) -> None:
    """Set the healing_fingerprint on an existing session row (best-effort).

    Called by the self-healing dispatcher after a session fails and a
    fingerprint has been computed.  If the session does not exist, the
    update silently affects 0 rows — no error is raised.

    Args:
        pool: asyncpg connection pool for the butler's database.
        session_id: UUID of the session to update.
        fingerprint: 64-character hex SHA-256 fingerprint string.
    """
    await pool.execute(
        """
        UPDATE sessions
        SET healing_fingerprint = $2
        WHERE id = $1
        """,
        session_id,
        fingerprint,
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
               input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens,
               request_id, ingestion_event_id,
               complexity, resolution_source, started_at, completed_at
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
    created by the spawner but the runtime instance has not yet returned.

    This is the primary mechanism for the dashboard to detect running sessions.

    Args:
        pool: asyncpg connection pool for the butler's database.

    Returns:
        List of active session records as dicts, ordered by started_at DESC.
    """
    rows = await pool.fetch(
        """
        SELECT id, prompt, trigger_source, result, tool_calls,
               duration_ms, trace_id, model, cost, success, error, request_id,
               ingestion_event_id, complexity, resolution_source, started_at, completed_at
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
               input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens,
               request_id, ingestion_event_id,
               complexity, resolution_source, started_at, completed_at
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


def _resolve_optional_range(
    from_date: str | date | None,
    to_date: str | date | None,
) -> tuple[datetime | None, datetime | None]:
    """Resolve an optional ``[from_date, to_date]`` pair into UTC timestamp bounds.

    Returns ``(None, None)`` when both are omitted (callers should treat this as
    "no range" / all-time, preserving pre-existing behavior). Raises ``ValueError``
    when only one side is given, or ``from_date`` is later than ``to_date``.
    """
    if from_date is None and to_date is None:
        return None, None
    if from_date is None or to_date is None:
        raise ValueError("from_date and to_date must be provided together")

    from_day = _parse_iso_date(from_date)
    to_day = _parse_iso_date(to_date)
    if from_day > to_day:
        raise ValueError("from_date must be <= to_date")

    start_at = datetime.combine(from_day, datetime.min.time(), tzinfo=UTC)
    end_exclusive = datetime.combine(to_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return start_at, end_exclusive


# ---------------------------------------------------------------------------
# Schedule cadence basis (bu-6jv4m.2)
#
# Everything downstream that forecasts a monthly cost from a cron expression
# multiplies by the cadence produced here, so the basis is stated once, in
# public constants, and carried into the API response rather than left as an
# unstated assumption. The bug this replaced was exactly that: the estimator
# counted occurrences in the NEXT 24 HOURS (so a weekly cron read as 1 on
# Mondays and 0 otherwise) and the router multiplied by a hardcoded 30.
# ---------------------------------------------------------------------------

#: Mean length of a Gregorian calendar month, in days (365.2425 / 12).
AVERAGE_MONTH_DAYS = 365.2425 / 12

#: Human-readable statement of the cadence basis, surfaced in the API response
#: so a projection is never mistaken for measured history.
CADENCE_BASIS_DESCRIPTION = (
    "Projected runs are the cron expression's own cadence over an average "
    "Gregorian calendar month (30.436875 days), sampled from a fixed anchor so "
    "the forecast does not change with the time of the request."
)

#: Fixed sampling anchor: the start of a Gregorian 400-year leap cycle. Anchoring
#: to a constant instant (rather than "now") makes the cadence a pure function of
#: the cron string -- the same schedule projects the same monthly runs whenever
#: the owner looks.
_CADENCE_ANCHOR = datetime(2001, 1, 1, tzinfo=UTC)

#: Sampling horizon: four years, long enough for weekly, monthly and yearly
#: expressions to average out their calendar irregularities.
_CADENCE_HORIZON_DAYS = 1461

#: Occurrence cap. A high-frequency expression (hourly, per-minute) reaches this
#: long before the horizon; sampling then stops early and the window is snapped
#: back to a whole cycle by ``_truncate_cadence_window`` before the rate is read.
_CADENCE_MAX_OCCURRENCES = 2000


def _shift_years(moment: datetime, years: int) -> datetime:
    """``moment`` advanced by whole calendar years, clamping 29 Feb to 28 Feb."""
    try:
        return moment.replace(year=moment.year + years)
    except ValueError:
        return moment.replace(year=moment.year + years, day=28)


def _count_occurrences(
    cron: str, start: datetime, end: datetime, limit: int
) -> tuple[int, datetime | None]:
    """Count firings of ``cron`` in ``(start, end]``, stopping at ``limit``.

    Returns the count, plus the instant of the limiting occurrence when the
    limit was reached before ``end`` (i.e. the window actually sampled), and
    ``None`` when the whole of ``(start, end]`` was covered.
    """
    itr = croniter(cron, start)
    count = 0
    latest = start
    while count < limit:
        nxt = itr.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        if nxt > end:
            return count, None
        count += 1
        latest = nxt

    # The cap stopped the loop. It only shortened the window if firings actually
    # remain inside ``end``; a cron that happens to fire exactly ``limit`` times
    # across the whole window was fully sampled and must not be truncated.
    peek = itr.get_next(datetime)
    if peek.tzinfo is None:
        peek = peek.replace(tzinfo=UTC)
    return (count, latest) if peek <= end else (count, None)


def _truncate_cadence_window(reference: datetime, sampled_until: datetime) -> datetime:
    """Snap a cap-shortened sampling window back to a whole calendar cycle.

    Dividing a count by the raw span it was collected over assumes the firings
    are spread evenly across that span. A seasonal expression breaks the
    assumption: ``0 * * 1 *`` (hourly, but only in January) exhausts the
    occurrence cap partway through a January, so the raw span is bounded by a
    dense stretch and the resulting rate reads about 31% high -- a plausible
    number, on a page whose entire purpose is forecast honesty.

    Snapping the window back to the longest whole cycle that fits inside it --
    years, else weeks, else days -- guarantees every season the window contains
    is represented in full, so the recount inside it averages honestly. Returns
    ``reference`` unchanged when the sample is shorter than a single day, which
    a five-field cron (at most 1440 firings a day, well under the cap) cannot
    produce; the caller then keeps the raw span.
    """
    if _shift_years(reference, 1) <= sampled_until:
        years = 1
        while _shift_years(reference, years + 1) <= sampled_until:
            years += 1
        return _shift_years(reference, years)

    days = int((sampled_until - reference).total_seconds() // 86400)
    if days >= 7:
        return reference + timedelta(days=days - days % 7)
    return reference + timedelta(days=days)


def _estimate_runs_per_month(cron: str, *, reference: datetime = _CADENCE_ANCHOR) -> float:
    """Estimate how many times a cron expression fires in an average month.

    The basis is ``AVERAGE_MONTH_DAYS`` (30.436875 days, the mean Gregorian
    calendar month), described for callers by ``CADENCE_BASIS_DESCRIPTION``.
    Occurrences are enumerated forward from ``reference`` over a four-year
    horizon and the observed rate is scaled to one average month. A
    high-frequency expression exhausts ``_CADENCE_MAX_OCCURRENCES`` first; the
    window is then truncated to a whole cycle (see ``_truncate_cadence_window``)
    and recounted, so a seasonal expression is not measured over a dense stretch
    alone.

    ``reference`` defaults to a fixed anchor so the result is a pure function of
    ``cron``; it is injectable only so tests can demonstrate that invariance
    across several fixed clocks. The four-year horizon is not a whole number of
    weeks (1461 days = 208.71 weeks), so a weekly expression samples 208 or 209
    firings against an exact 208.71 -- an error of about 0.14%, immaterial next
    to the per-run cost it multiplies.

    Returns ``0.0`` for an expression croniter cannot parse -- a schedule whose
    cadence is unknown projects nothing rather than a fabricated number. It must
    not raise: ``_schedule_costs_from_data`` serves every row of
    ``/api/spend/by-schedule``, so one bad cron string would take out the whole
    response.
    """
    if not croniter.is_valid(cron):
        return 0.0

    window_end = reference + timedelta(days=_CADENCE_HORIZON_DAYS)
    count, capped_at = _count_occurrences(cron, reference, window_end, _CADENCE_MAX_OCCURRENCES)
    if capped_at is not None:
        truncated_end = _truncate_cadence_window(reference, capped_at)
        if truncated_end > reference:
            window_end = truncated_end
            count, _ = _count_occurrences(cron, reference, window_end, _CADENCE_MAX_OCCURRENCES)
        else:
            window_end = capped_at

    span_days = (window_end - reference).total_seconds() / 86400
    if span_days <= 0 or count == 0:
        return 0.0
    return count / span_days * AVERAGE_MONTH_DAYS


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
            COALESCE(SUM(output_tokens), 0)::bigint AS total_output_tokens,
            COALESCE(SUM(cached_input_tokens), 0)::bigint AS total_cached_input_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)::bigint AS total_cache_creation_tokens
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
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COALESCE(SUM(cached_input_tokens), 0)::bigint AS cached_input_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)::bigint AS cache_creation_tokens
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
            "cached_input_tokens": int(row["cached_input_tokens"]),
            "cache_creation_tokens": int(row["cache_creation_tokens"]),
        }

    if totals is None:
        return {
            "period": period,
            "total_sessions": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_cache_creation_tokens": 0,
            "by_model": by_model,
        }

    return {
        "period": period,
        "total_sessions": int(totals["total_sessions"]),
        "total_input_tokens": int(totals["total_input_tokens"]),
        "total_output_tokens": int(totals["total_output_tokens"]),
        "total_cached_input_tokens": int(totals["total_cached_input_tokens"]),
        "total_cache_creation_tokens": int(totals["total_cache_creation_tokens"]),
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
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COALESCE(SUM(cached_input_tokens), 0)::bigint AS cached_input_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)::bigint AS cache_creation_tokens
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
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COALESCE(SUM(cached_input_tokens), 0)::bigint AS cached_input_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)::bigint AS cache_creation_tokens
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
            "cached_input_tokens": int(row["cached_input_tokens"]),
            "cache_creation_tokens": int(row["cache_creation_tokens"]),
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
                "cached_input_tokens": int(row["cached_input_tokens"]),
                "cache_creation_tokens": int(row["cache_creation_tokens"]),
                "by_model": by_day_model.get(day_key, {}),
            }
        )

    return {"days": days}


async def top_sessions(
    pool: asyncpg.Pool,
    limit: int = 10,
    from_date: str | date | None = None,
    to_date: str | date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the highest-token completed sessions.

    When ``from_date``/``to_date`` are both provided (ISO date strings or
    ``date`` objects), results are scoped to sessions started within that
    inclusive range. When both are omitted, all-time results are returned
    (pre-existing behavior, preserved for back-compat). Providing only one
    of the two raises ``ValueError``.
    """
    safe_limit = max(1, int(limit))
    start_at, end_exclusive = _resolve_optional_range(from_date, to_date)
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
          AND ($2::timestamptz IS NULL OR started_at >= $2)
          AND ($3::timestamptz IS NULL OR started_at < $3)
        ORDER BY (COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) DESC, started_at DESC
        LIMIT $1
        """,
        safe_limit,
        start_at,
        end_exclusive,
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


async def schedule_costs(
    pool: asyncpg.Pool,
    from_date: str | date | None = None,
    to_date: str | date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return per-schedule token usage aggregates for cost analysis.

    When ``from_date``/``to_date`` are both provided (ISO date strings or
    ``date`` objects), only runs started within that inclusive range are
    aggregated — schedules with no runs in the window still appear (with
    zeroed totals) thanks to the LEFT JOIN. When both are omitted, all-time
    totals are returned (pre-existing behavior, preserved for back-compat).
    Providing only one of the two raises ``ValueError``.

    Each row carries measured totals for the window plus two forecast fields
    that must not be confused with them: ``runs_per_month`` (the cron's own
    cadence over an average calendar month, from ``_estimate_runs_per_month``)
    and ``forecast_basis`` (the prose statement of that basis).
    """
    start_at, end_exclusive = _resolve_optional_range(from_date, to_date)
    rows = await pool.fetch(
        """
        SELECT
            st.name,
            st.cron,
            s.model,
            COUNT(s.id)::bigint AS total_runs,
            COALESCE(SUM(s.input_tokens), 0)::bigint AS total_input_tokens,
            COALESCE(SUM(s.output_tokens), 0)::bigint AS total_output_tokens,
            COALESCE(SUM(s.cached_input_tokens), 0)::bigint AS total_cached_input_tokens,
            COALESCE(SUM(s.cache_creation_tokens), 0)::bigint AS total_cache_creation_tokens
        FROM scheduled_tasks AS st
        LEFT JOIN sessions AS s
            ON s.trigger_source = ('schedule:' || st.name)
            AND ($1::timestamptz IS NULL OR s.started_at >= $1)
            AND ($2::timestamptz IS NULL OR s.started_at < $2)
        GROUP BY st.name, st.cron, s.model
        ORDER BY st.name, s.model
        """,
        start_at,
        end_exclusive,
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
                "total_cached_input_tokens": int(row["total_cached_input_tokens"]),
                "total_cache_creation_tokens": int(row["total_cache_creation_tokens"]),
                # Forecast input, not measured history: the cadence the cron
                # expression itself implies over an average calendar month
                # (bu-6jv4m.2). Consumers must keep it separate from the
                # measured totals above.
                "runs_per_month": _estimate_runs_per_month(cron),
                "forecast_basis": CADENCE_BASIS_DESCRIPTION,
            }
        )

    return {"schedules": schedules}
