"""Query functions and row DTOs for the ``dashboard_read`` module.

All cross-butler reads go through the two sanctioned RFC 0030 views
(``concierge.v_fleet_sessions`` / ``concierge.v_fleet_spend``) or a handful of
``public`` tables every butler role can already read
(``public.insight_candidates``). No function in this module issues a query
against another butler's schema directly — that access path does not exist
for the ``butler_concierge_rw`` runtime role (see the migration
``roster/concierge/migrations/001_fleet_views.py``).

Every public function here returns data alongside a ``source`` envelope via
:func:`source_envelope`, matching the ``{kind, ref, as_of}`` contract that
every ``dashboard_read`` MCP tool result carries (see
``docs/api_and_protocols/response-conventions.md`` for the fleet's degraded-
envelope vocabulary this module reuses on a query failure).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.read_models.insights_v1 import (
    query_insight_delivery_state as _query_insight_delivery_state,
)
from butlers.api.read_models.sessions_v1 import decode_session_cursor, encode_session_cursor
from butlers.core.pricing import PricingConfig, estimate_session_cost

logger = logging.getLogger(__name__)

#: The two sanctioned RFC 0030 cross-schema views. See the migration in
#: ``roster/concierge/migrations/001_fleet_views.py`` for the exact column
#: allowlist and grant set.
FLEET_SESSIONS_VIEW = "concierge.v_fleet_sessions"
FLEET_SPEND_VIEW = "concierge.v_fleet_spend"

_PERIOD_WINDOWS: dict[str, timedelta] = {
    "today": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


class RequiredViewMissingError(RuntimeError):
    """Raised when a sanctioned RFC 0030 view is missing or ungranted."""


# ---------------------------------------------------------------------------
# Source envelope
# ---------------------------------------------------------------------------


def source_envelope(kind: str, ref: str) -> dict[str, Any]:
    """Build the ``{kind, ref, as_of}`` envelope every tool result carries.

    Parameters
    ----------
    kind:
        What was read, e.g. ``"view"`` or ``"table"``.
    ref:
        The fully-qualified object name read (e.g. ``"concierge.v_fleet_sessions"``).
    """
    return {"kind": kind, "ref": ref, "as_of": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# Startup health check (RFC 0010 guardrail 4)
# ---------------------------------------------------------------------------


async def ensure_views_available(pool: asyncpg.Pool) -> None:
    """Verify both RFC 0030 views are queryable, raising loudly if not.

    Called once from :meth:`DashboardReadModule.on_startup`. A missing view
    or a revoked grant must fail the module's startup, not degrade silently
    to empty results at first tool call.
    """
    for view in (FLEET_SESSIONS_VIEW, FLEET_SPEND_VIEW):
        exists = await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", view)
        if not exists:
            raise RequiredViewMissingError(
                f"dashboard_read module: required view {view!r} is missing or not "
                "granted to this role. Run `alembic upgrade concierge@head`."
            )


# ---------------------------------------------------------------------------
# Fleet status
# ---------------------------------------------------------------------------


@dataclass
class ButlerStatusRow:
    """One butler's rollup from ``v_fleet_sessions`` (v1 contract)."""

    butler: str
    sessions_24h: int
    running_count: int
    failed_24h: int
    last_session_at: datetime | None


_FLEET_STATUS_SQL = f"""
    SELECT
        source_butler AS butler,
        count(*) FILTER (WHERE started_at >= now() - interval '24 hours') AS sessions_24h,
        count(*) FILTER (WHERE status = 'running') AS running_count,
        count(*) FILTER (
            WHERE status = 'failed' AND started_at >= now() - interval '24 hours'
        ) AS failed_24h,
        max(ended_at) AS last_session_at
    FROM {FLEET_SESSIONS_VIEW}
    {{where}}
    GROUP BY source_butler
    ORDER BY source_butler
"""


async def query_fleet_status(
    pool: asyncpg.Pool, *, butler: str | None = None
) -> list[ButlerStatusRow]:
    """Per-butler rollup: 24h session count, running count, 24h failures, last activity."""
    where = "WHERE source_butler = $1" if butler is not None else ""
    args = (butler,) if butler is not None else ()
    rows = await pool.fetch(_FLEET_STATUS_SQL.format(where=where), *args)
    return [
        ButlerStatusRow(
            butler=r["butler"],
            sessions_24h=int(r["sessions_24h"] or 0),
            running_count=int(r["running_count"] or 0),
            failed_24h=int(r["failed_24h"] or 0),
            last_session_at=r["last_session_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Sessions: recent (keyset), detail, aggregate, trigger breakdown
# ---------------------------------------------------------------------------


@dataclass
class FleetSessionRow:
    """One session row from ``v_fleet_sessions`` (v1 contract).

    Deliberately excludes ``prompt``/``result``/``tool_calls``/``cost`` —
    those columns never enter the view (RFC 0030 guardrail 6).
    """

    id: UUID
    butler: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    trigger_source: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    error_class: str | None


def _row_to_fleet_session(row: asyncpg.Record) -> FleetSessionRow:
    return FleetSessionRow(
        id=row["id"],
        butler=row["source_butler"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        status=row["status"],
        trigger_source=row["trigger_source"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        error_class=row["error_class"],
    )


@dataclass
class FleetSessionsPage:
    rows: list[FleetSessionRow] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


async def query_sessions_recent(
    pool: asyncpg.Pool,
    *,
    limit: int,
    cursor: str | None = None,
    butler: str | None = None,
    only_errors: bool | None = None,
) -> FleetSessionsPage:
    """Keyset-paged recent sessions across the fleet, newest first."""
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if cursor is not None:
        started_at, row_id = decode_session_cursor(cursor)
        conditions.append(f"(started_at, id) < (${idx}, ${idx + 1})")
        args.extend([started_at, row_id])
        idx += 2

    if butler is not None:
        conditions.append(f"source_butler = ${idx}")
        args.append(butler)
        idx += 1

    if only_errors is True:
        conditions.append("status = 'failed'")
    elif only_errors is False:
        conditions.append("status != 'failed'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT * FROM {FLEET_SESSIONS_VIEW} {where} "
        f"ORDER BY started_at DESC, id DESC LIMIT {limit + 1}"
    )
    db_rows = await pool.fetch(sql, *args)

    rows = [_row_to_fleet_session(r) for r in db_rows[:limit]]
    has_more = len(db_rows) > limit
    next_cursor = encode_session_cursor(rows[-1].started_at, rows[-1].id) if has_more else None
    return FleetSessionsPage(rows=rows, has_more=has_more, next_cursor=next_cursor)


async def query_session_detail(pool: asyncpg.Pool, session_id: UUID) -> FleetSessionRow | None:
    """A single session's allowlisted fields, or None if not found in any schema."""
    row = await pool.fetchrow(f"SELECT * FROM {FLEET_SESSIONS_VIEW} WHERE id = $1", session_id)
    return _row_to_fleet_session(row) if row is not None else None


@dataclass
class ButlerCount:
    butler: str
    count: int


@dataclass
class FleetSessionsAggregate:
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    running_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_butler: list[ButlerCount] = field(default_factory=list)


async def query_sessions_aggregate(
    pool: asyncpg.Pool, *, since: datetime | None = None
) -> FleetSessionsAggregate:
    """Fleet-wide session totals for an optional lookback window."""
    where = "WHERE started_at >= $1" if since is not None else ""
    args = (since,) if since is not None else ()
    row = await pool.fetchrow(
        f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE status = 'success') AS success_count,
            count(*) FILTER (WHERE status = 'failed') AS failed_count,
            count(*) FILTER (WHERE status = 'running') AS running_count,
            coalesce(sum(input_tokens), 0) AS input_tokens,
            coalesce(sum(output_tokens), 0) AS output_tokens
        FROM {FLEET_SESSIONS_VIEW} {where}
        """,
        *args,
    )
    by_butler_rows = await pool.fetch(
        f"""
        SELECT source_butler AS butler, count(*) AS count
        FROM {FLEET_SESSIONS_VIEW} {where}
        GROUP BY source_butler
        HAVING count(*) > 0
        ORDER BY count(*) DESC
        """,
        *args,
    )
    return FleetSessionsAggregate(
        total=int(row["total"] or 0),
        success_count=int(row["success_count"] or 0),
        failed_count=int(row["failed_count"] or 0),
        running_count=int(row["running_count"] or 0),
        input_tokens=int(row["input_tokens"] or 0),
        output_tokens=int(row["output_tokens"] or 0),
        by_butler=[ButlerCount(butler=r["butler"], count=int(r["count"])) for r in by_butler_rows],
    )


@dataclass
class TriggerSourceCount:
    trigger_source: str | None
    count: int


async def query_sessions_trigger_breakdown(
    pool: asyncpg.Pool, *, since: datetime | None = None
) -> list[TriggerSourceCount]:
    """Fleet-wide session counts grouped by ``trigger_source``."""
    where = "WHERE started_at >= $1" if since is not None else ""
    args = (since,) if since is not None else ()
    rows = await pool.fetch(
        f"""
        SELECT trigger_source, count(*) AS count
        FROM {FLEET_SESSIONS_VIEW} {where}
        GROUP BY trigger_source
        ORDER BY count(*) DESC
        """,
        *args,
    )
    return [
        TriggerSourceCount(trigger_source=r["trigger_source"], count=int(r["count"])) for r in rows
    ]


async def query_fleet_errors_recent(
    pool: asyncpg.Pool, *, limit: int, since: datetime | None = None
) -> list[FleetSessionRow]:
    """Recently failed sessions across the fleet, newest first."""
    conditions = ["status = 'failed'"]
    args: list[Any] = []
    if since is not None:
        conditions.append("started_at >= $1")
        args.append(since)
    where = f"WHERE {' AND '.join(conditions)}"
    rows = await pool.fetch(
        f"SELECT * FROM {FLEET_SESSIONS_VIEW} {where} ORDER BY started_at DESC LIMIT {limit}",
        *args,
    )
    return [_row_to_fleet_session(r) for r in rows]


async def query_fleet_search(
    pool: asyncpg.Pool,
    *,
    limit: int,
    butler: str | None = None,
    trigger_source: str | None = None,
    model: str | None = None,
    error_class: str | None = None,
    status: str | None = None,
) -> list[FleetSessionRow]:
    """Structured search over allowlisted session metadata only.

    Never searches ``prompt``/``result`` text — those columns are not present
    in ``v_fleet_sessions`` and never cross a schema boundary (RFC 0030
    guardrail 6).
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1
    for column, value in (
        ("source_butler", butler),
        ("trigger_source", trigger_source),
        ("model", model),
        ("error_class", error_class),
        ("status", status),
    ):
        if value is not None:
            conditions.append(f"{column} = ${idx}")
            args.append(value)
            idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM {FLEET_SESSIONS_VIEW} {where} ORDER BY started_at DESC LIMIT {limit}",
        *args,
    )
    return [_row_to_fleet_session(r) for r in rows]


# ---------------------------------------------------------------------------
# Spend — token counts come from v_fleet_spend; dollar cost is computed here
# in Python via pricing.toml (estimate_session_cost), exactly as the
# dashboard's own /api/spend/* routes do. No dollar figure is ever stored or
# computed in SQL — see src/butlers/core/pricing.py.
# ---------------------------------------------------------------------------


@dataclass
class SpendRow:
    """Raw token usage for one completed session (from ``v_fleet_spend``)."""

    id: UUID
    butler: str
    started_at: datetime
    ended_at: datetime | None
    model: str | None
    input_tokens: int
    output_tokens: int


def _row_to_spend(row: asyncpg.Record) -> SpendRow:
    return SpendRow(
        id=row["id"],
        butler=row["source_butler"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        model=row["model"],
        input_tokens=int(row["input_tokens"] or 0),
        output_tokens=int(row["output_tokens"] or 0),
    )


async def _fetch_spend_rows(
    pool: asyncpg.Pool, *, since: datetime | None, until: datetime | None = None
) -> list[SpendRow]:
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1
    if since is not None:
        conditions.append(f"started_at >= ${idx}")
        args.append(since)
        idx += 1
    if until is not None:
        conditions.append(f"started_at < ${idx}")
        args.append(until)
        idx += 1
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(f"SELECT * FROM {FLEET_SPEND_VIEW} {where}", *args)
    return [_row_to_spend(r) for r in rows]


def _cost_cents(
    pricing: PricingConfig, model: str | None, input_tokens: int, output_tokens: int
) -> int | None:
    """Convert an estimated dollar cost to integer cents, preserving unpriced as None."""
    if not model:
        return None
    dollars = estimate_session_cost(pricing, model, input_tokens, output_tokens)
    return round(dollars * 100) if dollars is not None else None


@dataclass
class SpendSummary:
    period: str
    total_cost_cents: int | None
    unpriced_input_tokens: int
    unpriced_output_tokens: int
    session_count: int


async def query_spend_summary(
    pool: asyncpg.Pool, pricing: PricingConfig, *, period: str
) -> SpendSummary:
    """Aggregate estimated spend for ``period`` ('today' | 'week' | 'month')."""
    window = _PERIOD_WINDOWS.get(period)
    if window is None:
        raise ValueError(f"Invalid period {period!r}; must be one of {sorted(_PERIOD_WINDOWS)}")
    since = datetime.now(UTC) - window
    rows = await _fetch_spend_rows(pool, since=since)

    total_cents = 0
    any_priced = False
    unpriced_in = 0
    unpriced_out = 0
    for r in rows:
        cents = _cost_cents(pricing, r.model, r.input_tokens, r.output_tokens)
        if cents is None:
            unpriced_in += r.input_tokens
            unpriced_out += r.output_tokens
            continue
        any_priced = True
        total_cents += cents

    return SpendSummary(
        period=period,
        total_cost_cents=total_cents if any_priced or not rows else None,
        unpriced_input_tokens=unpriced_in,
        unpriced_output_tokens=unpriced_out,
        session_count=len(rows),
    )


@dataclass
class DailySpend:
    date: str
    cost_cents: int | None
    session_count: int


async def query_spend_daily(
    pool: asyncpg.Pool, pricing: PricingConfig, *, days: int
) -> list[DailySpend]:
    """Daily estimated spend series for the last ``days`` days (oldest first)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await _fetch_spend_rows(pool, since=since)

    by_day: dict[str, list[SpendRow]] = {}
    for r in rows:
        day_key = r.started_at.date().isoformat()
        by_day.setdefault(day_key, []).append(r)

    result: list[DailySpend] = []
    for day_key in sorted(by_day):
        day_rows = by_day[day_key]
        cents = 0
        any_priced = False
        for r in day_rows:
            c = _cost_cents(pricing, r.model, r.input_tokens, r.output_tokens)
            if c is not None:
                any_priced = True
                cents += c
        result.append(
            DailySpend(
                date=day_key,
                cost_cents=cents if any_priced else None,
                session_count=len(day_rows),
            )
        )
    return result


@dataclass
class TopSpendSession:
    id: UUID
    butler: str
    started_at: datetime
    model: str | None
    cost_cents: int | None


async def query_spend_top_sessions(
    pool: asyncpg.Pool, pricing: PricingConfig, *, limit: int, since: datetime | None = None
) -> list[TopSpendSession]:
    """Costliest sessions in the window, most expensive first."""
    rows = await _fetch_spend_rows(pool, since=since)
    scored = [
        TopSpendSession(
            id=r.id,
            butler=r.butler,
            started_at=r.started_at,
            model=r.model,
            cost_cents=_cost_cents(pricing, r.model, r.input_tokens, r.output_tokens),
        )
        for r in rows
    ]
    scored.sort(key=lambda s: (s.cost_cents is None, -(s.cost_cents or 0)))
    return scored[:limit]


@dataclass
class SpendBreakdownRow:
    key: str
    cost_cents: int | None
    session_count: int


async def query_spend_breakdown_by_butler(
    pool: asyncpg.Pool, pricing: PricingConfig, *, since: datetime | None = None
) -> list[SpendBreakdownRow]:
    """Estimated spend grouped by owning butler, highest first."""
    rows = await _fetch_spend_rows(pool, since=since)
    by_butler: dict[str, list[SpendRow]] = {}
    for r in rows:
        by_butler.setdefault(r.butler, []).append(r)
    return _breakdown_rows(pricing, by_butler)


async def query_spend_breakdown_by_model(
    pool: asyncpg.Pool, pricing: PricingConfig, *, since: datetime | None = None
) -> list[SpendBreakdownRow]:
    """Estimated spend grouped by model, highest first."""
    rows = await _fetch_spend_rows(pool, since=since)
    by_model: dict[str, list[SpendRow]] = {}
    for r in rows:
        by_model.setdefault(r.model or "unknown", []).append(r)
    return _breakdown_rows(pricing, by_model)


def _breakdown_rows(
    pricing: PricingConfig, grouped: dict[str, list[SpendRow]]
) -> list[SpendBreakdownRow]:
    out: list[SpendBreakdownRow] = []
    for key, group_rows in grouped.items():
        cents = 0
        any_priced = False
        for r in group_rows:
            c = _cost_cents(pricing, r.model, r.input_tokens, r.output_tokens)
            if c is not None:
                any_priced = True
                cents += c
        out.append(
            SpendBreakdownRow(
                key=key,
                cost_cents=cents if any_priced else None,
                session_count=len(group_rows),
            )
        )
    out.sort(key=lambda b: (b.cost_cents is None, -(b.cost_cents or 0)))
    return out


# ---------------------------------------------------------------------------
# Insight delivery state — direct reuse of the versioned read-model function.
# ``public.insight_candidates`` is a plain public-schema table already
# readable by every butler role (standard per-role public grant), so no
# fleet view or fan-out is needed here.
# ---------------------------------------------------------------------------

query_insight_delivery_state = _query_insight_delivery_state
