"""Cost and usage tracking endpoints.

Provides aggregate cost summaries, daily time series, and
top-spending sessions. Cost estimation uses the pricing.toml
configuration loaded at startup.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import ApiResponse, CostSummary, DailyCost, ScheduleCost, TopSession
from butlers.api.pricing import PricingConfig, estimate_session_cost

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/costs", tags=["costs"])

_STATUS_TIMEOUT_S = 5.0
_SESSIONS_SUMMARY_TOOL = "sessions_summary"


async def _get_butler_session_stats(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    period: str,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Query a butler for session cost stats.

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
                total_cost = 0.0
                by_model: dict[str, float] = {}
                for model_id, stats in data.get("by_model", {}).items():
                    cost = estimate_session_cost(
                        pricing,
                        model_id,
                        stats.get("input_tokens", 0),
                        stats.get("output_tokens", 0),
                    )
                    total_cost += cost
                    by_model[model_id] = by_model.get(model_id, 0.0) + cost
                return (
                    info.name,
                    total_cost,
                    data.get("total_sessions", 0),
                    data.get("total_input_tokens", 0),
                    data.get("total_output_tokens", 0),
                    by_model,
                )
    except (ButlerUnreachableError, TimeoutError):
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


@router.get("/summary", response_model=ApiResponse[CostSummary])
async def get_cost_summary(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[CostSummary]:
    """Return aggregate cost summary across all butlers."""
    tasks = [_get_butler_session_stats(mgr, info, pricing, period) for info in configs]
    results = await asyncio.gather(*tasks)

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
        period=period,
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
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[list[DailyCost]]:
    """Return daily cost time series aggregated across all butlers.

    Query parameters ``from`` and ``to`` control the date range (ISO 8601
    date strings, e.g. ``2026-02-03``).  Both default to the last 7 days
    when omitted.

    The endpoint fans out ``sessions_daily`` MCP calls to every butler,
    then merges per-day results into a single sorted time series.
    """
    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=6)

    tasks = [
        _get_butler_daily_stats(mgr, info, pricing, from_date.isoformat(), to_date.isoformat())
        for info in configs
    ]
    all_results = await asyncio.gather(*tasks)

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
                    cost = estimate_session_cost(pricing, model_id, input_tokens, output_tokens)
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
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[list[TopSession]]:
    """Return most expensive sessions across all butlers.

    Fans out to each butler's ``top_sessions`` MCP tool, merges the results,
    calculates costs using the pricing config, and returns the top *limit*
    sessions sorted by cost descending.
    """
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
                        pricing, model_id, input_tokens, output_tokens
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
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[list[ScheduleCost]]:
    """Return per-schedule cost analysis across all butlers."""
    tasks = [_get_butler_schedule_costs(mgr, info, pricing) for info in configs]
    results = await asyncio.gather(*tasks)
    all_costs = [c for butler_costs in results for c in butler_costs]
    all_costs.sort(key=lambda c: c.projected_monthly_usd, reverse=True)
    return ApiResponse[list[ScheduleCost]](data=all_costs)
