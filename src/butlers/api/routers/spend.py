"""Spend and usage tracking endpoints.

Provides aggregate spend summaries, daily time series, top-spending sessions,
spend breakdown, forecast, routing rules, and monthly ceiling.
Cost estimation uses the pricing.toml configuration loaded at startup.

Routes (§5.0):
  GET  /api/spend?period=          — aggregate summary (was /api/costs/summary)
  GET  /api/spend/daily            — daily time series (was /api/costs/daily)
  GET  /api/spend/top-sessions     — costliest sessions (was /api/costs/top-sessions)
  GET  /api/spend/by-schedule      — per-schedule cost analysis (was /api/costs/by-schedule)
  GET  /api/spend/breakdown?by=    — §5.2 butler|model|feature breakdown
  GET  /api/spend/forecast         — §5.2 naive linear extrapolation MTD → EOM
  GET  /api/spend/rules            — §5.2 list routing rules
  POST /api/spend/rules            — §5.2 create routing rule
  PUT  /api/spend/rules/{id}       — §5.2 update routing rule
  DELETE /api/spend/rules/{id}     — §5.2 delete routing rule
  PUT  /api/spend/ceiling          — §5.2 set monthly ceiling
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_db_manager,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import ApiResponse, CostSummary, DailyCost, ScheduleCost, TopSession
from butlers.api.pricing import PricingConfig, estimate_session_cost
from butlers.api.routers.audit import log_audit_entry
from butlers.core.sessions import sessions_daily, sessions_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spend", tags=["spend"])

_STATUS_TIMEOUT_S = 5.0
_SESSIONS_SUMMARY_TOOL = "sessions_summary"


def _get_db_manager() -> DatabaseManager | None:
    """Return dashboard DB manager when initialized, otherwise None.

    Unit tests for this router often exercise only the legacy MCP fan-out path
    without initializing API DB pools. Returning None preserves that path while
    production requests can use DB-backed session aggregates.
    """
    try:
        return get_db_manager()
    except RuntimeError:
        return None


def _cost_stats_from_session_summary(
    name: str,
    data: dict,
    pricing: PricingConfig,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Convert raw session aggregate data into the cost router tuple shape."""
    total_cost = 0.0
    by_model: dict[str, float] = {}
    for model_id, stats in data.get("by_model", {}).items():
        cost = estimate_session_cost(
            pricing,
            model_id,
            stats.get("input_tokens", 0),
            stats.get("output_tokens", 0),
            cached_input_tokens=stats.get("cached_input_tokens", 0),
            context_tokens=stats.get("context_tokens"),
        )
        total_cost += cost
        by_model[model_id] = by_model.get(model_id, 0.0) + cost
    return (
        name,
        total_cost,
        data.get("total_sessions", 0),
        data.get("total_input_tokens", 0),
        data.get("total_output_tokens", 0),
        by_model,
    )


async def _get_butler_session_stats_from_db(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    period: str,
) -> tuple[str, float, int, int, int, dict[str, float]] | None:
    """Read session cost stats directly from the butler DB pool if available."""
    try:
        data = await sessions_summary(db.pool(info.name), period)
    except KeyError:
        logger.debug("Cost summary DB pool unavailable for butler %s", info.name)
        return None
    except Exception as exc:
        logger.warning(
            "Cost summary DB query failed for butler %s (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        return None
    return _cost_stats_from_session_summary(info.name, data, pricing)


async def _get_butler_session_stats_for_range_from_db(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date,
    to_date: date,
) -> tuple[str, float, int, int, int, dict[str, float]] | None:
    """Read custom-range session cost stats directly from the butler DB pool."""
    try:
        data = await sessions_daily(db.pool(info.name), from_date, to_date)
    except KeyError:
        logger.debug("Cost range DB pool unavailable for butler %s", info.name)
        return None
    except Exception as exc:
        logger.warning(
            "Cost range DB query failed for butler %s (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        return None

    total_cost = 0.0
    total_sessions = 0
    total_input = 0
    total_output = 0
    by_model: dict[str, float] = {}
    for day_entry in data.get("days", []):
        total_sessions += day_entry.get("sessions", 0)
        total_input += day_entry.get("input_tokens", 0)
        total_output += day_entry.get("output_tokens", 0)
        for model_id, stats in day_entry.get("by_model", {}).items():
            cost = estimate_session_cost(
                pricing,
                model_id,
                stats.get("input_tokens", 0),
                stats.get("output_tokens", 0),
                cached_input_tokens=stats.get("cached_input_tokens", 0),
                context_tokens=stats.get("context_tokens"),
            )
            total_cost += cost
            by_model[model_id] = by_model.get(model_id, 0.0) + cost
    return (info.name, total_cost, total_sessions, total_input, total_output, by_model)


async def _get_butler_daily_stats_from_db(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: str,
    to_date: str,
) -> list[dict] | None:
    """Read daily session costs directly from the butler DB pool if available."""
    try:
        data = await sessions_daily(db.pool(info.name), from_date, to_date)
    except KeyError:
        logger.debug("Daily cost DB pool unavailable for butler %s", info.name)
        return None
    except Exception as exc:
        logger.warning(
            "Daily cost DB query failed for butler %s (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        return None

    days: list[dict] = []
    for day_entry in data.get("days", []):
        day_cost = 0.0
        for model_id, stats in day_entry.get("by_model", {}).items():
            day_cost += estimate_session_cost(
                pricing,
                model_id,
                stats.get("input_tokens", 0),
                stats.get("output_tokens", 0),
                cached_input_tokens=stats.get("cached_input_tokens", 0),
                context_tokens=stats.get("context_tokens"),
            )
        days.append(
            {
                "date": day_entry.get("date", ""),
                "cost_usd": round(day_cost, 6),
                "sessions": day_entry.get("sessions", 0),
                "input_tokens": day_entry.get("input_tokens", 0),
                "output_tokens": day_entry.get("output_tokens", 0),
            }
        )
    return days


async def _get_butler_session_stats(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    period: str,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Query a butler for session cost stats via the ``sessions_summary`` MCP tool.

    Returns (name, cost, sessions, input_tokens, output_tokens, by_model).
    """
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool(_SESSIONS_SUMMARY_TOOL, {"period": period}),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                return _cost_stats_from_session_summary(info.name, data, pricing)
    except (
        ButlerUnreachableError,
        TimeoutError,
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
    ):
        logger.debug(
            "Cost summary unavailable for butler %s via %s",
            info.name,
            _SESSIONS_SUMMARY_TOOL,
        )
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invalid JSON from butler %s via %s: %s",
            info.name,
            _SESSIONS_SUMMARY_TOOL,
            exc,
        )
    except Exception as exc:
        logger.warning(
            "Cost summary tool call failed for butler %s via %s (%s: %s)",
            info.name,
            _SESSIONS_SUMMARY_TOOL,
            type(exc).__name__,
            exc,
        )
    return (info.name, 0.0, 0, 0, 0, {})


async def _get_butler_session_stats_for_range(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date,
    to_date: date,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Query a butler for session cost stats over a custom date range.

    Uses ``sessions_daily`` and aggregates totals across [from_date, to_date].
    Returns (name, cost, sessions, input_tokens, output_tokens, by_model).
    """
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool(
                "sessions_daily",
                {"from_date": from_date.isoformat(), "to_date": to_date.isoformat()},
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                total_cost = 0.0
                total_sessions = 0
                total_input = 0
                total_output = 0
                by_model: dict[str, float] = {}
                for day_entry in data.get("days", []):
                    total_sessions += day_entry.get("sessions", 0)
                    total_input += day_entry.get("input_tokens", 0)
                    total_output += day_entry.get("output_tokens", 0)
                    for model_id, stats in day_entry.get("by_model", {}).items():
                        cost = estimate_session_cost(
                            pricing,
                            model_id,
                            stats.get("input_tokens", 0),
                            stats.get("output_tokens", 0),
                            cached_input_tokens=stats.get("cached_input_tokens", 0),
                            context_tokens=stats.get("context_tokens"),
                        )
                        total_cost += cost
                        by_model[model_id] = by_model.get(model_id, 0.0) + cost
                return (info.name, total_cost, total_sessions, total_input, total_output, by_model)
    except (
        ButlerUnreachableError,
        TimeoutError,
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
    ):
        logger.debug(
            "Cost summary for date range unavailable for butler %s via sessions_daily",
            info.name,
        )
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invalid JSON from butler %s via sessions_daily: %s",
            info.name,
            exc,
        )
    except Exception as exc:
        logger.warning(
            "Cost summary (date range) tool call failed for butler %s via sessions_daily (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
    return (info.name, 0.0, 0, 0, 0, {})


@router.get("", response_model=ApiResponse[CostSummary])
@router.get("/summary", response_model=ApiResponse[CostSummary], include_in_schema=False)
async def get_cost_summary(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[CostSummary]:
    """Return aggregate cost summary across all butlers.

    When ``from`` and ``to`` query params are provided (ISO 8601 date strings,
    e.g. ``2026-01-01``), the summary covers that custom date range and the
    ``period`` param is ignored.  When omitted, the ``period`` preset
    (``today`` / ``7d`` / ``30d``) is used.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response (all counts zero).

    Validation: both ``from`` and ``to`` must be provided together, and
    ``from`` must not be later than ``to``.
    """
    if (from_date is None) != (to_date is None):
        raise HTTPException(
            status_code=422,
            detail="Both 'from' and 'to' must be provided together, or both omitted.",
        )
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="'from' must not be later than 'to'.",
        )
    if butler is not None:
        configs = [c for c in configs if c.name == butler]
    if from_date is not None and to_date is not None:
        tasks = [
            _get_butler_session_stats_for_range_from_db(db, info, pricing, from_date, to_date)
            if db is not None
            else _get_butler_session_stats_for_range(mgr, info, pricing, from_date, to_date)
            for info in configs
        ]
        period_label = f"{from_date.isoformat()}/{to_date.isoformat()}"
    else:
        tasks = [
            _get_butler_session_stats_from_db(db, info, pricing, period)
            if db is not None
            else _get_butler_session_stats(mgr, info, pricing, period)
            for info in configs
        ]
        period_label = period
    raw_results = await asyncio.gather(*tasks)
    if db is not None:
        if from_date is not None and to_date is not None:
            fallback_tasks = [
                _get_butler_session_stats_for_range(mgr, info, pricing, from_date, to_date)
                for info, result in zip(configs, raw_results, strict=False)
                if result is None
            ]
        else:
            fallback_tasks = [
                _get_butler_session_stats(mgr, info, pricing, period)
                for info, result in zip(configs, raw_results, strict=False)
                if result is None
            ]
        fallback_results = await asyncio.gather(*fallback_tasks)
        fallback_iter = iter(fallback_results)
        results = [result if result is not None else next(fallback_iter) for result in raw_results]
    else:
        results = raw_results

    total_cost = 0.0
    total_sessions = 0
    total_input = 0
    total_output = 0
    by_butler: dict[str, float] = {}
    by_model: dict[str, float] = {}

    for name, cost, sessions, inp, out, models in results:
        total_cost += cost
        total_sessions += sessions
        total_input += inp
        total_output += out
        if cost > 0:
            by_butler[name] = cost
        for model_id, model_cost in models.items():
            by_model[model_id] = by_model.get(model_id, 0.0) + model_cost

    summary = CostSummary(
        period=period_label,
        total_cost_usd=round(total_cost, 6),
        total_sessions=total_sessions,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        by_butler=by_butler,
        by_model=by_model,
    )
    return ApiResponse[CostSummary](data=summary)


async def _get_butler_daily_stats(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Query a butler for daily session stats via the ``sessions_daily`` MCP tool.

    Returns a list of dicts with keys: date, cost_usd, sessions, input_tokens,
    output_tokens.  Falls back to an empty list when the butler is unreachable
    or the tool is not yet implemented.
    """
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool(
                "sessions_daily",
                {"from_date": from_date, "to_date": to_date},
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                days: list[dict] = []
                for day_entry in data.get("days", []):
                    day_cost = 0.0
                    for model_id, stats in day_entry.get("by_model", {}).items():
                        day_cost += estimate_session_cost(
                            pricing,
                            model_id,
                            stats.get("input_tokens", 0),
                            stats.get("output_tokens", 0),
                            cached_input_tokens=stats.get("cached_input_tokens", 0),
                            context_tokens=stats.get("context_tokens"),
                        )
                    days.append(
                        {
                            "date": day_entry.get("date", ""),
                            "cost_usd": round(day_cost, 6),
                            "sessions": day_entry.get("sessions", 0),
                            "input_tokens": day_entry.get("input_tokens", 0),
                            "output_tokens": day_entry.get("output_tokens", 0),
                        }
                    )
                return days
    except (ButlerUnreachableError, TimeoutError, Exception):
        pass
    return []


@router.get("/daily", response_model=ApiResponse[list[DailyCost]])
async def get_daily_costs(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[list[DailyCost]]:
    """Return daily cost time series aggregated across all butlers.

    Query parameters ``from`` and ``to`` control the date range (ISO 8601
    date strings, e.g. ``2026-02-03``).  Both default to the last 7 days
    when omitted.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response.

    The endpoint fans out ``sessions_daily`` MCP calls to every butler,
    then merges per-day results into a single sorted time series.
    """
    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=6)

    if butler is not None:
        configs = [c for c in configs if c.name == butler]

    tasks = [
        _get_butler_daily_stats_from_db(
            db,
            info,
            pricing,
            from_date.isoformat(),
            to_date.isoformat(),
        )
        if db is not None
        else _get_butler_daily_stats(mgr, info, pricing, from_date.isoformat(), to_date.isoformat())
        for info in configs
    ]
    raw_results = await asyncio.gather(*tasks)
    if db is not None:
        fallback_tasks = [
            _get_butler_daily_stats(mgr, info, pricing, from_date.isoformat(), to_date.isoformat())
            for info, result in zip(configs, raw_results, strict=False)
            if result is None
        ]
        fallback_results = await asyncio.gather(*fallback_tasks)
        fallback_iter = iter(fallback_results)
        all_results = [
            result if result is not None else next(fallback_iter) for result in raw_results
        ]
    else:
        all_results = raw_results

    # Merge daily stats from all butlers keyed by date string.
    merged: dict[str, dict] = {}
    for butler_days in all_results:
        for day in butler_days:
            d = day["date"]
            if d not in merged:
                merged[d] = {
                    "date": d,
                    "cost_usd": 0.0,
                    "sessions": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            merged[d]["cost_usd"] += day["cost_usd"]
            merged[d]["sessions"] += day["sessions"]
            merged[d]["input_tokens"] += day["input_tokens"]
            merged[d]["output_tokens"] += day["output_tokens"]

    # Sort by date ascending and round costs.
    daily = [
        DailyCost(
            date=v["date"],
            cost_usd=round(v["cost_usd"], 6),
            sessions=v["sessions"],
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
        )
        for v in sorted(merged.values(), key=lambda x: x["date"])
    ]

    return ApiResponse[list[DailyCost]](data=daily)


async def _get_butler_top_sessions(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    limit: int,
) -> list[TopSession]:
    """Query a single butler for its most expensive sessions.

    Returns a list of TopSession records with costs calculated from pricing config.
    Falls back to empty list when the butler is unreachable or returns bad data.
    """
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool("top_sessions", {"limit": limit}),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                sessions: list[TopSession] = []
                for s in data.get("sessions", []):
                    model_id = s.get("model", "")
                    input_tokens = s.get("input_tokens", 0)
                    output_tokens = s.get("output_tokens", 0)
                    cost = estimate_session_cost(
                        pricing,
                        model_id,
                        input_tokens,
                        output_tokens,
                        cached_input_tokens=s.get("cached_input_tokens", 0),
                        context_tokens=s.get("context_tokens"),
                    )
                    sessions.append(
                        TopSession(
                            session_id=s.get("session_id", ""),
                            butler=info.name,
                            cost_usd=round(cost, 6),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            model=model_id,
                            started_at=s.get("started_at", ""),
                        )
                    )
                return sessions
    except (ButlerUnreachableError, TimeoutError, Exception):
        pass
    return []


@router.get("/top-sessions", response_model=ApiResponse[list[TopSession]])
async def get_top_sessions(
    limit: int = Query(default=10, ge=1, le=50),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[list[TopSession]]:
    """Return most expensive sessions across all butlers.

    Fans out to each butler's ``top_sessions`` MCP tool, merges the results,
    calculates costs using the pricing config, and returns the top *limit*
    sessions sorted by cost descending.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response.
    """
    if butler is not None:
        configs = [c for c in configs if c.name == butler]

    tasks = [_get_butler_top_sessions(mgr, info, pricing, limit) for info in configs]
    results = await asyncio.gather(*tasks)

    all_sessions: list[TopSession] = []
    for sessions in results:
        all_sessions.extend(sessions)

    all_sessions.sort(key=lambda s: s.cost_usd, reverse=True)
    return ApiResponse[list[TopSession]](data=all_sessions[:limit])


async def _get_butler_schedule_costs(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
) -> list[ScheduleCost]:
    """Query a butler for per-schedule cost data."""
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool("schedule_costs", {}),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                costs = []
                for entry in data.get("schedules", []):
                    model_id = entry.get("model", "")
                    input_tokens = entry.get("total_input_tokens", 0)
                    output_tokens = entry.get("total_output_tokens", 0)
                    total_cost = estimate_session_cost(
                        pricing,
                        model_id,
                        input_tokens,
                        output_tokens,
                        cached_input_tokens=entry.get("total_cached_input_tokens", 0),
                        context_tokens=entry.get("context_tokens"),
                    )
                    total_runs = entry.get("total_runs", 0)
                    avg_cost = total_cost / total_runs if total_runs > 0 else 0.0
                    runs_per_day = entry.get("runs_per_day", 0.0)
                    costs.append(
                        ScheduleCost(
                            schedule_name=entry.get("name", ""),
                            butler=info.name,
                            cron=entry.get("cron", ""),
                            total_runs=total_runs,
                            total_cost_usd=round(total_cost, 6),
                            avg_cost_per_run=round(avg_cost, 6),
                            runs_per_day=runs_per_day,
                            projected_monthly_usd=round(avg_cost * runs_per_day * 30, 6),
                        )
                    )
                return costs
    except (ButlerUnreachableError, TimeoutError, Exception):
        pass
    return []


@router.get("/by-schedule", response_model=ApiResponse[list[ScheduleCost]])
async def get_costs_by_schedule(
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[list[ScheduleCost]]:
    """Return per-schedule cost analysis across all butlers.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response.
    """
    if butler is not None:
        configs = [c for c in configs if c.name == butler]

    tasks = [_get_butler_schedule_costs(mgr, info, pricing) for info in configs]
    results = await asyncio.gather(*tasks)
    all_costs = [c for butler_costs in results for c in butler_costs]
    all_costs.sort(key=lambda c: c.projected_monthly_usd, reverse=True)
    return ApiResponse[list[ScheduleCost]](data=all_costs)


# ---------------------------------------------------------------------------
# §5.2 — Breakdown endpoint
# ---------------------------------------------------------------------------


def _get_spend_db_manager() -> DatabaseManager:
    """Dependency: require DB manager for spend mutation endpoints."""
    try:
        return get_db_manager()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Database not available")


@router.get("/breakdown", response_model=ApiResponse[dict])
async def get_spend_breakdown(
    by: Literal["butler", "model", "feature"] = Query(
        "butler", description="Dimension to break spend down by"
    ),
    db: DatabaseManager | None = Depends(_get_db_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[dict]:
    """Return spend broken down by butler, model, or feature for the current month.

    Uses the MTD (month-to-date) summary from each butler.  The ``feature``
    dimension currently mirrors the ``by_schedule`` breakdown — a richer
    feature taxonomy is deferred to a future revision.
    """
    # Reuse the existing MTD summary across all butlers
    tasks = [
        _get_butler_session_stats_from_db(db, info, pricing, "30d")
        if db is not None
        else _get_butler_session_stats(mgr, info, pricing, "30d")
        for info in configs
    ]
    raw_results = await asyncio.gather(*tasks)
    if db is not None:
        fallback_tasks = [
            _get_butler_session_stats(mgr, info, pricing, "30d")
            for info, result in zip(configs, raw_results, strict=False)
            if result is None
        ]
        fallback_results = await asyncio.gather(*fallback_tasks)
        fallback_iter = iter(fallback_results)
        results = [r if r is not None else next(fallback_iter) for r in raw_results]
    else:
        results = raw_results

    if by == "butler":
        breakdown: dict[str, float] = {}
        for name, cost, _, _, _, _ in results:
            if cost > 0:
                breakdown[name] = round(cost, 6)
        return ApiResponse[dict](data={"by": "butler", "breakdown": breakdown})

    if by == "model":
        breakdown = {}
        for _, _, _, _, _, by_model in results:
            for model_id, model_cost in by_model.items():
                breakdown[model_id] = round(breakdown.get(model_id, 0.0) + model_cost, 6)
        return ApiResponse[dict](data={"by": "model", "breakdown": breakdown})

    # by == "feature": proxy to schedule-level spend
    schedule_tasks = [_get_butler_schedule_costs(mgr, info, pricing) for info in configs]
    schedule_results = await asyncio.gather(*schedule_tasks)
    all_costs = [c for butler_costs in schedule_results for c in butler_costs]
    breakdown = {c.schedule_name: round(c.total_cost_usd, 6) for c in all_costs}
    return ApiResponse[dict](data={"by": "feature", "breakdown": breakdown})


# ---------------------------------------------------------------------------
# §5.2 — Forecast endpoint
# ---------------------------------------------------------------------------


class ForecastDay(BaseModel):
    date: str
    cost_usd: float
    projected: bool  # True for extrapolated days after today


class ForecastResponse(BaseModel):
    days: list[ForecastDay]
    projected_eom_usd: float
    days_in_month: int
    days_elapsed: int
    mtd_usd: float
    ceiling_usd: float | None


@router.get("/forecast", response_model=ApiResponse[ForecastResponse])
async def get_spend_forecast(
    db: DatabaseManager | None = Depends(_get_db_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[ForecastResponse]:
    """Return a naive linear spend forecast for the current month.

    Algorithm (§D5): ``mtd_total ÷ max(days_elapsed, 1) × days_in_month``.
    Returns a daily series (solid = actual, dashed = projected from today) plus
    a projected end-of-month total.

    TODO: replace the naive daily-rate extrapolation with a smarter estimator
    (per-butler decay weighting, weekend vs weekday adjustment, etc.)
    """
    today = date.today()
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = (today - month_start).days + 1  # inclusive of today

    # Fetch daily actuals for the month so far
    tasks = [
        _get_butler_daily_stats_from_db(
            db,
            info,
            pricing,
            month_start.isoformat(),
            today.isoformat(),
        )
        if db is not None
        else _get_butler_daily_stats(mgr, info, pricing, month_start.isoformat(), today.isoformat())
        for info in configs
    ]
    raw_results = await asyncio.gather(*tasks)
    if db is not None:
        fallback_tasks = [
            _get_butler_daily_stats(mgr, info, pricing, month_start.isoformat(), today.isoformat())
            for info, result in zip(configs, raw_results, strict=False)
            if result is None
        ]
        fallback_results = await asyncio.gather(*fallback_tasks)
        fallback_iter = iter(fallback_results)
        all_results = [r if r is not None else next(fallback_iter) for r in raw_results]
    else:
        all_results = raw_results

    # Merge daily actuals across butlers
    merged: dict[str, float] = {}
    for butler_days in all_results:
        for day_entry in butler_days:
            d = day_entry["date"]
            merged[d] = merged.get(d, 0.0) + day_entry["cost_usd"]

    mtd_usd = sum(merged.values())
    daily_rate = mtd_usd / max(days_elapsed, 1)
    projected_eom_usd = daily_rate * days_in_month

    # Build solid actuals + dashed projection series
    forecast_days: list[ForecastDay] = []
    current = month_start
    month_end = month_start.replace(day=days_in_month)
    while current <= month_end:
        iso = current.isoformat()
        if current <= today:
            forecast_days.append(
                ForecastDay(date=iso, cost_usd=round(merged.get(iso, 0.0), 6), projected=False)
            )
        else:
            forecast_days.append(
                ForecastDay(date=iso, cost_usd=round(daily_rate, 6), projected=True)
            )
        current += timedelta(days=1)

    # Fetch monthly ceiling (silently ignore DB errors)
    ceiling_usd: float | None = None
    if db is not None:
        try:
            pool = db.pool("switchboard")
            row = await pool.fetchrow("SELECT monthly_usd FROM public.spend_ceiling WHERE id = 1")
            if row:
                ceiling_usd = float(row["monthly_usd"])
        except Exception:
            pass

    return ApiResponse[ForecastResponse](
        data=ForecastResponse(
            days=forecast_days,
            projected_eom_usd=round(projected_eom_usd, 6),
            days_in_month=days_in_month,
            days_elapsed=days_elapsed,
            mtd_usd=round(mtd_usd, 6),
            ceiling_usd=ceiling_usd,
        )
    )


# ---------------------------------------------------------------------------
# §5.2 — Spend rules
# ---------------------------------------------------------------------------


class SpendRuleCondition(BaseModel):
    butler: str | None = None
    complexity: str | None = None


class SpendRuleAction(BaseModel):
    model: str


class SpendRule(BaseModel):
    id: str
    position: int
    condition: dict
    action: dict
    saved_7d: float | None = None
    created_at: str
    updated_at: str


class SpendRuleCreate(BaseModel):
    position: int | None = None
    condition: dict
    action: dict


class SpendRuleUpdate(BaseModel):
    position: int | None = None
    condition: dict | None = None
    action: dict | None = None


def _require_spend_db(db: DatabaseManager | None = Depends(_get_db_manager)) -> DatabaseManager:
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return db


@router.get("/rules", response_model=ApiResponse[list[SpendRule]])
async def list_spend_rules(
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[list[SpendRule]]:
    """Return all spend routing rules ordered by position."""
    if db is None:
        return ApiResponse[list[SpendRule]](data=[])
    try:
        pool = db.pool("switchboard")
        rows = await pool.fetch(
            "SELECT id, position, condition, action, saved_7d, created_at, updated_at "
            "FROM public.spend_rules ORDER BY position ASC"
        )

        def _decode(v: object) -> dict:
            return v if isinstance(v, dict) else json.loads(v)  # type: ignore[arg-type]

        rules = [
            SpendRule(
                id=str(row["id"]),
                position=row["position"],
                condition=_decode(row["condition"]),
                action=_decode(row["action"]),
                saved_7d=float(row["saved_7d"]) if row["saved_7d"] is not None else None,
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
            for row in rows
        ]
    except Exception as exc:
        logger.warning("Failed to fetch spend rules: %s", exc)
        rules = []
    return ApiResponse[list[SpendRule]](data=rules)


@router.post("/rules", response_model=ApiResponse[SpendRule], status_code=201)
async def create_spend_rule(
    body: SpendRuleCreate,
    request: Request,
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[SpendRule]:
    """Create a new spend routing rule.

    The rule is inserted at ``position`` (or appended if omitted).  Rules with
    equal or higher positions are shifted down by one to maintain ordering
    integrity.  Calls ``audit.append('spend.rule')`` after successful insert.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        pool = db.pool("switchboard")
        now = datetime.now(tz=UTC)

        if body.position is None:
            max_pos = await pool.fetchval(
                "SELECT COALESCE(MAX(position), -1) FROM public.spend_rules"
            )
            position = int(max_pos) + 1
        else:
            position = body.position
            # Shift existing rules down
            await pool.execute(
                "UPDATE public.spend_rules SET position = position + 1, updated_at = $1 "
                "WHERE position >= $2",
                now,
                position,
            )

        row = await pool.fetchrow(
            "INSERT INTO public.spend_rules (position, condition, action, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id, position, condition, action, saved_7d, created_at, updated_at",
            position,
            json.dumps(body.condition),
            json.dumps(body.action),
            now,
            now,
        )

        def _dec(v: object) -> dict:
            return v if isinstance(v, dict) else json.loads(v)  # type: ignore[arg-type]

        rule = SpendRule(
            id=str(row["id"]),
            position=row["position"],
            condition=_dec(row["condition"]),
            action=_dec(row["action"]),
            saved_7d=None,
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to create spend rule: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create spend rule") from exc

    # Audit log — fire and forget; never breaks primary operation
    await log_audit_entry(
        db,
        butler="switchboard",
        operation="spend.rule.create",
        request_summary={"condition": body.condition, "action": body.action, "position": position},
    )
    return ApiResponse[SpendRule](data=rule)


@router.put("/rules/{rule_id}", response_model=ApiResponse[SpendRule])
async def update_spend_rule(
    rule_id: str,
    body: SpendRuleUpdate,
    request: Request,
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[SpendRule]:
    """Update an existing spend routing rule by ID.

    Updating ``position`` triggers the same shift-and-reorder logic as create.
    Calls ``audit.append('spend.rule')`` after successful update.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        pool = db.pool("switchboard")
        now = datetime.now(tz=UTC)

        # Validate rule exists
        existing = await pool.fetchrow(
            "SELECT id, position, condition, action, saved_7d, created_at, updated_at "
            "FROM public.spend_rules WHERE id = $1",
            uuid.UUID(rule_id),
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Spend rule not found")

        def _dec2(v: object) -> dict:
            return v if isinstance(v, dict) else json.loads(v)  # type: ignore[arg-type]

        new_condition = (
            body.condition if body.condition is not None else _dec2(existing["condition"])
        )
        new_action = body.action if body.action is not None else _dec2(existing["action"])
        new_position = body.position if body.position is not None else existing["position"]

        if body.position is not None and body.position != existing["position"]:
            # Shift other rules to make room at new position
            await pool.execute(
                "UPDATE public.spend_rules SET position = position + 1, updated_at = $1 "
                "WHERE position >= $2 AND id != $3",
                now,
                new_position,
                uuid.UUID(rule_id),
            )

        row = await pool.fetchrow(
            "UPDATE public.spend_rules SET position=$1, condition=$2, action=$3, updated_at=$4 "
            "WHERE id=$5 "
            "RETURNING id, position, condition, action, saved_7d, created_at, updated_at",
            new_position,
            json.dumps(new_condition),
            json.dumps(new_action),
            now,
            uuid.UUID(rule_id),
        )
        rule = SpendRule(
            id=str(row["id"]),
            position=row["position"],
            condition=_dec2(row["condition"]),
            action=_dec2(row["action"]),
            saved_7d=float(row["saved_7d"]) if row["saved_7d"] is not None else None,
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to update spend rule %s: %s", rule_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update spend rule") from exc

    await log_audit_entry(
        db,
        butler="switchboard",
        operation="spend.rule.update",
        request_summary={
            "rule_id": rule_id,
            "condition": new_condition,
            "action": new_action,
            "position": new_position,
        },
    )
    return ApiResponse[SpendRule](data=rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_spend_rule(
    rule_id: str,
    request: Request,
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> None:
    """Delete a spend routing rule by ID.

    Rules with higher positions are shifted up by one after deletion.
    Calls ``audit.append('spend.rule')`` after successful delete.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        pool = db.pool("switchboard")
        now = datetime.now(tz=UTC)

        row = await pool.fetchrow(
            "DELETE FROM public.spend_rules WHERE id = $1 RETURNING position",
            uuid.UUID(rule_id),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Spend rule not found")

        deleted_position = row["position"]
        await pool.execute(
            "UPDATE public.spend_rules SET position = position - 1, updated_at = $1 "
            "WHERE position > $2",
            now,
            deleted_position,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to delete spend rule %s: %s", rule_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete spend rule") from exc

    await log_audit_entry(
        db,
        butler="switchboard",
        operation="spend.rule.delete",
        request_summary={"rule_id": rule_id},
    )


# ---------------------------------------------------------------------------
# §5.2 — Monthly ceiling
# ---------------------------------------------------------------------------


class SpendCeiling(BaseModel):
    monthly_usd: float
    updated_at: str


class SpendCeilingUpdate(BaseModel):
    monthly_usd: float


@router.put("/ceiling", response_model=ApiResponse[SpendCeiling])
async def update_spend_ceiling(
    body: SpendCeilingUpdate,
    request: Request,
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[SpendCeiling]:
    """Set the monthly spend ceiling.

    Uses an upsert pattern (singleton row id=1).  Calls
    ``audit.append('spend.ceiling')`` after successful update.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if body.monthly_usd <= 0:
        raise HTTPException(status_code=422, detail="monthly_usd must be positive")
    try:
        pool = db.pool("switchboard")
        now = datetime.now(tz=UTC)
        row = await pool.fetchrow(
            "INSERT INTO public.spend_ceiling (id, monthly_usd, updated_at) "
            "VALUES (1, $1, $2) "
            "ON CONFLICT (id) DO UPDATE "
            "SET monthly_usd = EXCLUDED.monthly_usd, updated_at = EXCLUDED.updated_at "
            "RETURNING monthly_usd, updated_at",
            body.monthly_usd,
            now,
        )
        ceiling = SpendCeiling(
            monthly_usd=float(row["monthly_usd"]),
            updated_at=row["updated_at"].isoformat(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to update spend ceiling: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update spend ceiling") from exc

    await log_audit_entry(
        db,
        butler="switchboard",
        operation="spend.ceiling.update",
        request_summary={"monthly_usd": body.monthly_usd},
    )
    return ApiResponse[SpendCeiling](data=ceiling)
