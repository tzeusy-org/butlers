"""MCP tool wiring for the dashboard_read module.

Every tool is strictly read-only and every result dict carries a
``source: {kind, ref, as_of}`` envelope (see :func:`queries.source_envelope`)
naming exactly what was read and when — never a bare payload with implicit
provenance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from . import queries as q


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _uid(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _session_dict(row: q.FleetSessionRow) -> dict[str, Any]:
    return {
        "id": _uid(row.id),
        "butler": row.butler,
        "started_at": _dt(row.started_at),
        "ended_at": _dt(row.ended_at),
        "status": row.status,
        "trigger_source": row.trigger_source,
        "model": row.model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "error_class": row.error_class,
    }


def _since_for(days: int | None) -> datetime | None:
    if days is None:
        return None
    from datetime import timedelta

    return datetime.now(UTC) - timedelta(days=days)


def register_tools(mcp: Any, module: Any) -> None:
    """Register all dashboard_read MCP tools."""

    # -- fleet status --------------------------------------------------

    @mcp.tool()
    async def dashboard_read_fleet_status() -> dict[str, Any]:
        """Per-butler fleet rollup: 24h session count, running count, 24h failures, last activity.

        Read-only. Sourced from ``concierge.v_fleet_sessions`` (RFC 0030).
        """
        rows = await q.query_fleet_status(module._get_pool())
        return {
            "butlers": [
                {
                    "butler": r.butler,
                    "sessions_24h": r.sessions_24h,
                    "running_count": r.running_count,
                    "failed_24h": r.failed_24h,
                    "last_session_at": _dt(r.last_session_at),
                }
                for r in rows
            ],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_butler_detail(butler: str) -> dict[str, Any]:
        """Fleet rollup for one named butler: 24h sessions, running, 24h failures, last activity.

        Returns an empty rollup (all zero counts) if the butler has no
        session rows in the lookback window — never omits the butler key.
        """
        rows = await q.query_fleet_status(module._get_pool(), butler=butler)
        if rows:
            r = rows[0]
            detail = {
                "butler": r.butler,
                "sessions_24h": r.sessions_24h,
                "running_count": r.running_count,
                "failed_24h": r.failed_24h,
                "last_session_at": _dt(r.last_session_at),
            }
        else:
            detail = {
                "butler": butler,
                "sessions_24h": 0,
                "running_count": 0,
                "failed_24h": 0,
                "last_session_at": None,
            }
        return {**detail, "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW)}

    # -- sessions --------------------------------------------------------

    @mcp.tool()
    async def dashboard_read_sessions_recent(
        limit: int = 20,
        cursor: str | None = None,
        butler: str | None = None,
        only_errors: bool | None = None,
    ) -> dict[str, Any]:
        """Keyset-paged recent sessions across the fleet, newest first.

        ``limit`` is capped at 200. Pass the previous result's ``next_cursor``
        to fetch the following page. ``only_errors=True`` restricts to failed
        sessions; ``False`` restricts to non-failed sessions.
        """
        page = await q.query_sessions_recent(
            module._get_pool(),
            limit=min(max(limit, 1), 200),
            cursor=cursor,
            butler=butler,
            only_errors=only_errors,
        )
        return {
            "sessions": [_session_dict(r) for r in page.rows],
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_session_detail(session_id: str) -> dict[str, Any]:
        """A single session's allowlisted fields, fanned out across the whole fleet by id.

        Never returns ``prompt``/``result``/``tool_calls`` — those never
        cross the schema boundary (RFC 0030 guardrail 6).
        """
        row = await q.query_session_detail(module._get_pool(), UUID(session_id))
        return {
            "session": _session_dict(row) if row is not None else None,
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_sessions_aggregate(since_days: int | None = None) -> dict[str, Any]:
        """Fleet-wide session totals (success/failed/running counts, token sums).

        ``since_days`` restricts to sessions started in the last N days;
        omit for an all-time aggregate.
        """
        agg = await q.query_sessions_aggregate(module._get_pool(), since=_since_for(since_days))
        return {
            "total": agg.total,
            "success_count": agg.success_count,
            "failed_count": agg.failed_count,
            "running_count": agg.running_count,
            "input_tokens": agg.input_tokens,
            "output_tokens": agg.output_tokens,
            "by_butler": [{"butler": b.butler, "count": b.count} for b in agg.by_butler],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_sessions_trigger_breakdown(
        since_days: int | None = None,
    ) -> dict[str, Any]:
        """Fleet-wide session counts grouped by ``trigger_source`` (e.g. schedule, route, tick)."""
        breakdown = await q.query_sessions_trigger_breakdown(
            module._get_pool(), since=_since_for(since_days)
        )
        return {
            "breakdown": [
                {"trigger_source": b.trigger_source, "count": b.count} for b in breakdown
            ],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_fleet_errors_recent(
        limit: int = 20, since_days: int | None = None
    ) -> dict[str, Any]:
        """Recently failed sessions across the fleet, newest first.

        Each row carries ``error_class`` (a short classifier such as an
        exception class name) — never the raw error message.
        """
        rows = await q.query_fleet_errors_recent(
            module._get_pool(), limit=min(max(limit, 1), 200), since=_since_for(since_days)
        )
        return {
            "sessions": [_session_dict(r) for r in rows],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_fleet_search(
        limit: int = 20,
        butler: str | None = None,
        trigger_source: str | None = None,
        model: str | None = None,
        error_class: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Structured cross-butler session search by metadata only.

        Filters on ``butler`` / ``trigger_source`` / ``model`` / ``error_class``
        / ``status``. This is NOT a text search over prompts or results — that
        content never crosses a schema boundary (RFC 0030 guardrail 6), so it
        is simply not available to search.
        """
        rows = await q.query_fleet_search(
            module._get_pool(),
            limit=min(max(limit, 1), 200),
            butler=butler,
            trigger_source=trigger_source,
            model=model,
            error_class=error_class,
            status=status,
        )
        return {
            "sessions": [_session_dict(r) for r in rows],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    # -- timeline / activity ---------------------------------------------

    @mcp.tool()
    async def dashboard_read_timeline_recent(
        limit: int = 20, cursor: str | None = None, only_errors: bool | None = None
    ) -> dict[str, Any]:
        """Cross-butler session-event timeline, keyset-paged, newest first.

        This is the session half of the dashboard's unified timeline;
        notification deliveries live in the switchboard schema and are out
        of scope for this staffer's read surface.
        """
        page = await q.query_sessions_recent(
            module._get_pool(),
            limit=min(max(limit, 1), 200),
            cursor=cursor,
            only_errors=only_errors,
        )
        return {
            "events": [_session_dict(r) for r in page.rows],
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_butler_activity(butler: str, limit: int = 20) -> dict[str, Any]:
        """A single butler's recent completed sessions, newest first.

        The session half of that butler's activity feed; pending actions and
        memory episodes live in the butler's own schema and are out of scope
        for this staffer's read surface.
        """
        page = await q.query_sessions_recent(
            module._get_pool(), limit=min(max(limit, 1), 200), butler=butler
        )
        return {
            "butler": butler,
            "sessions": [_session_dict(r) for r in page.rows],
            "source": q.source_envelope("view", q.FLEET_SESSIONS_VIEW),
        }

    # -- spend -------------------------------------------------------------

    @mcp.tool()
    async def dashboard_read_spend_summary(period: str = "today") -> dict[str, Any]:
        """Aggregate estimated spend for a period ('today' | 'week' | 'month').

        ``total_cost_cents`` is estimated from token counts via
        ``pricing.toml`` (never a stored dollar figure). ``None`` when no
        session in the window has a priced model — see
        ``unpriced_input_tokens``/``unpriced_output_tokens`` for the
        unpriced usage in that case.
        """
        summary = await q.query_spend_summary(
            module._get_pool(), module._get_pricing(), period=period
        )
        return {
            "period": summary.period,
            "total_cost_cents": summary.total_cost_cents,
            "unpriced_input_tokens": summary.unpriced_input_tokens,
            "unpriced_output_tokens": summary.unpriced_output_tokens,
            "session_count": summary.session_count,
            "source": q.source_envelope("view", q.FLEET_SPEND_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_spend_daily(days: int = 14) -> dict[str, Any]:
        """Daily estimated spend series for the last N days (oldest first, capped at 90)."""
        series = await q.query_spend_daily(
            module._get_pool(), module._get_pricing(), days=min(max(days, 1), 90)
        )
        return {
            "days": [
                {"date": d.date, "cost_cents": d.cost_cents, "session_count": d.session_count}
                for d in series
            ],
            "source": q.source_envelope("view", q.FLEET_SPEND_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_spend_top_sessions(
        limit: int = 10, since_days: int | None = None
    ) -> dict[str, Any]:
        """Costliest sessions in the window, most expensive first (capped at 100)."""
        rows = await q.query_spend_top_sessions(
            module._get_pool(),
            module._get_pricing(),
            limit=min(max(limit, 1), 100),
            since=_since_for(since_days),
        )
        return {
            "sessions": [
                {
                    "id": _uid(r.id),
                    "butler": r.butler,
                    "started_at": _dt(r.started_at),
                    "model": r.model,
                    "cost_cents": r.cost_cents,
                }
                for r in rows
            ],
            "source": q.source_envelope("view", q.FLEET_SPEND_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_spend_breakdown_by_butler(
        since_days: int | None = None,
    ) -> dict[str, Any]:
        """Estimated spend grouped by owning butler, highest first."""
        rows = await q.query_spend_breakdown_by_butler(
            module._get_pool(), module._get_pricing(), since=_since_for(since_days)
        )
        return {
            "breakdown": [
                {"butler": r.key, "cost_cents": r.cost_cents, "session_count": r.session_count}
                for r in rows
            ],
            "source": q.source_envelope("view", q.FLEET_SPEND_VIEW),
        }

    @mcp.tool()
    async def dashboard_read_spend_breakdown_by_model(
        since_days: int | None = None,
    ) -> dict[str, Any]:
        """Estimated spend grouped by model, highest first."""
        rows = await q.query_spend_breakdown_by_model(
            module._get_pool(), module._get_pricing(), since=_since_for(since_days)
        )
        return {
            "breakdown": [
                {"model": r.key, "cost_cents": r.cost_cents, "session_count": r.session_count}
                for r in rows
            ],
            "source": q.source_envelope("view", q.FLEET_SPEND_VIEW),
        }

    # -- insights ------------------------------------------------------

    @mcp.tool()
    async def dashboard_read_insight_delivery_state() -> dict[str, Any]:
        """Insight-pipeline delivery counts (queued/delivered/failed) and last delivery time."""
        row = await q.query_insight_delivery_state(module._get_pool())
        payload = (
            {
                "queued": row.queued,
                "delivered": row.delivered,
                "failed": row.failed,
                "last_delivery_at": _dt(row.last_delivery_at),
            }
            if row is not None
            else {"queued": 0, "delivered": 0, "failed": 0, "last_delivery_at": None}
        )
        return {**payload, "source": q.source_envelope("table", "public.insight_candidates")}
