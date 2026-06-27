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
  WS   /api/spend/stream           — §5.3 per-call cost event stream
"""

from __future__ import annotations

import asyncio
import calendar
import hmac
import json
import logging
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import anyio
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from butlers.api.models import ApiResponse, DailySpend, ScheduleCost, SpendSummary, TopSession
from butlers.api.pricing import PricingConfig, estimate_session_cost
from butlers.api.routers.audit import append as audit_append
from butlers.core.sessions import sessions_daily, sessions_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spend", tags=["spend"])

_STATUS_TIMEOUT_S = 5.0
_SESSIONS_SUMMARY_TOOL = "sessions_summary"

# ---------------------------------------------------------------------------
# §5.3 — Spend event pub/sub broker
# ---------------------------------------------------------------------------

# Maximum events retained for reconnect snapshot
_STREAM_RECENT_MAX = 50

# Maximum WS subscriber queue depth before oldest events are dropped
_STREAM_QUEUE_MAXSIZE = 256


class SpendEvent(BaseModel):
    """A single per-call cost event emitted on the spend stream."""

    kind: str = "call"
    ts: float
    butler: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    session_id: str = ""
    extra: dict = {}


# Global pub/sub state — module-level singleton so tests can reset between runs
_spend_subscribers: list[asyncio.Queue] = []
_spend_recent: list[dict] = []  # ring buffer of recent raw events


def emit_spend_event(event: dict) -> None:
    """Publish a per-call cost event to all connected WS subscribers.

    Also appends to the ring buffer so new subscribers receive a snapshot
    of the most-recent events on connect.

    Call this from any place that records per-call costs (e.g. the runtime
    cost reporter or spawner session-close path).

    ``event`` must be a dict matching the ``SpendEvent`` shape.
    """
    if "ts" not in event:
        event = {**event, "ts": time.time()}

    # Maintain ring buffer (discard oldest if full)
    _spend_recent.append(event)
    if len(_spend_recent) > _STREAM_RECENT_MAX:
        _spend_recent.pop(0)

    dead: list[asyncio.Queue] = []
    for q in _spend_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _spend_subscribers.remove(q)
        except ValueError:
            pass


def _auth_ws_api_key(token: str | None) -> bool:
    """Return True if the provided token matches the configured DASHBOARD_API_KEY.

    When DASHBOARD_API_KEY is not set in the environment, all tokens are
    accepted (auth disabled — consistent with ``ApiKeyMiddleware``).
    """
    expected = os.environ.get("DASHBOARD_API_KEY") or None
    if expected is None:
        return True
    if not token:
        return False
    return hmac.compare_digest(token, expected)


@router.websocket("/stream")
async def spend_stream(
    websocket: WebSocket,
    api_key: str | None = Query(None),
) -> None:
    """WebSocket stream of per-call cost events (§5.3).

    Authentication: pass the dashboard API key via ``?api_key=<key>`` at
    upgrade time (browsers cannot set ``X-API-Key`` headers on WS upgrades).
    When ``DASHBOARD_API_KEY`` is not configured, all connections are allowed.

    On connect the server sends a ``snapshot`` message containing recent
    cost events (up to the last 50) so that client KPIs are not blank on
    reconnect.  Subsequent messages are individual ``call`` events as they
    arrive in real time.

    Event payload shape::

        {"kind": "call", "ts": <unix float>, "butler": "...",
         "model": "...", "tokens_in": N, "tokens_out": N,
         "cost_usd": N, "session_id": "...", "extra": {}}

    Snapshot payload shape::

        {"kind": "snapshot", "events": [...recent call events...]}

    The connection is kept open until the client disconnects.
    """
    if not _auth_ws_api_key(api_key):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()

    # Send snapshot of recent events so the client KPIs are immediately populated
    snapshot = {"kind": "snapshot", "events": list(_spend_recent)}
    try:
        await websocket.send_text(json.dumps(snapshot))
    except WebSocketDisconnect:
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=_STREAM_QUEUE_MAXSIZE)
    _spend_subscribers.append(queue)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(json.dumps(event))
            except TimeoutError:
                # Send keepalive ping to detect stale connections
                try:
                    await websocket.send_text(json.dumps({"kind": "ping"}))
                except WebSocketDisconnect:
                    break
            except WebSocketDisconnect:
                break
    finally:
        try:
            _spend_subscribers.remove(queue)
        except ValueError:
            pass


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


@router.get("", response_model=ApiResponse[SpendSummary])
@router.get("/summary", response_model=ApiResponse[SpendSummary], include_in_schema=False)
async def get_cost_summary(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[SpendSummary]:
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

    summary = SpendSummary(
        period=period_label,
        total_cost_usd=round(total_cost, 6),
        total_sessions=total_sessions,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        by_butler=by_butler,
        by_model=by_model,
    )
    return ApiResponse[SpendSummary](data=summary)


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


@router.get("/daily", response_model=ApiResponse[list[DailySpend]])
async def get_daily_costs(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[list[DailySpend]]:
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
        DailySpend(
            date=v["date"],
            cost_usd=round(v["cost_usd"], 6),
            sessions=v["sessions"],
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
        )
        for v in sorted(merged.values(), key=lambda x: x["date"])
    ]

    return ApiResponse[list[DailySpend]](data=daily)


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


def projection_confidence_for(days_elapsed: int) -> Literal["low", "normal"]:
    """Confidence of the naive month-end projection (§5.2).

    The linear estimator divides MTD spend by very few elapsed days early in the
    month, so the projection swings wildly until a few days of actuals exist.
    ``"low"`` (``days_elapsed < 3``) signals the Console aggregator NOT to raise a
    "spend near ceiling" attention item from a low-confidence projection.
    """
    return "low" if days_elapsed < 3 else "normal"


class ForecastResponse(BaseModel):
    days: list[ForecastDay]
    projected_eom_usd: float
    days_in_month: int
    days_elapsed: int
    mtd_usd: float
    ceiling_usd: float | None
    projection_confidence: Literal["low", "normal"]


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
            projection_confidence=projection_confidence_for(days_elapsed),
        )
    )


# ---------------------------------------------------------------------------
# §5.2 — Spend rules
# ---------------------------------------------------------------------------


# Canonical complexity tiers a rule condition may match (mirrors
# butlers.core.model_routing.TIER_FALLTHROUGH_ORDER). Kept inline to avoid an
# api → core import for a small literal set.
_VALID_COMPLEXITY_TIERS = frozenset(
    {"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"}
)


class SpendRuleCondition(BaseModel):
    """Enforced schema for a spend-rule ``condition``.

    Unknown keys are rejected (``extra="forbid"`` → 422), so a malformed rule can never
    be persisted and then silently fail-closed at dispatch.  All keys are optional; an
    empty condition ``{}`` is a valid catch-all.  Supported dimensions mirror exactly
    what ``model_routing.apply_spend_routing_rules`` can evaluate at the dispatch call
    site: ``butler``, ``complexity`` (alias ``tier``), and ``trigger`` (the dispatch
    ``trigger_source``).  Each may be a scalar or a list (membership match).
    """

    model_config = ConfigDict(extra="forbid")

    butler: str | list[str] | None = None
    complexity: str | list[str] | None = None
    tier: str | list[str] | None = None
    trigger: str | list[str] | None = None

    @model_validator(mode="after")
    def _validate_tiers(self) -> SpendRuleCondition:
        for field in ("complexity", "tier"):
            value = getattr(self, field)
            if value is None:
                continue
            candidates = value if isinstance(value, list) else [value]
            for c in candidates:
                if str(c).lower() not in _VALID_COMPLEXITY_TIERS:
                    raise ValueError(
                        f"condition.{field} '{c}' is not a valid complexity tier; "
                        f"must be one of {sorted(_VALID_COMPLEXITY_TIERS)}"
                    )
        return self


class SpendRuleAction(BaseModel):
    """Enforced schema for a spend-rule ``action`` (its effects).

    Unknown keys are rejected (``extra="forbid"`` → 422).  Supported effects:

    - ``model`` — re-route the dispatch to this priced ``model_id``.
    - ``max_cost_per_call`` — a hard per-dispatch USD cap (must be > 0) the spawner
      enforces as a DENY gate.

    At least one effect must be present — an empty action does nothing and is rejected.
    A rule may set the model effect, the cap effect, or both.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    max_cost_per_call: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _require_effect(self) -> SpendRuleAction:
        if self.model is None and self.max_cost_per_call is None:
            raise ValueError(
                "action must set at least one effect: 'model' and/or 'max_cost_per_call'"
            )
        return self


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
    condition: SpendRuleCondition
    action: SpendRuleAction


class SpendRuleUpdate(BaseModel):
    position: int | None = None
    condition: SpendRuleCondition | None = None
    action: SpendRuleAction | None = None


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

        def _dec(v: object) -> dict:
            return v if isinstance(v, dict) else json.loads(v)  # type: ignore[arg-type]

        async with pool.acquire() as conn:
            async with conn.transaction():
                if body.position is None:
                    max_pos = await conn.fetchval(
                        "SELECT COALESCE(MAX(position), -1) FROM public.spend_rules"
                    )
                    position = int(max_pos) + 1
                else:
                    position = body.position
                    # Shift existing rules down atomically inside this transaction
                    await conn.execute(
                        "UPDATE public.spend_rules SET position = position + 1, updated_at = $1 "
                        "WHERE position >= $2",
                        now,
                        position,
                    )

                condition_payload = body.condition.model_dump(exclude_none=True)
                action_payload = body.action.model_dump(exclude_none=True)
                row = await conn.fetchrow(
                    "INSERT INTO public.spend_rules "
                    "(position, condition, action, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, $5) "
                    "RETURNING id, position, condition, action, saved_7d, created_at, updated_at",
                    position,
                    json.dumps(condition_payload),
                    json.dumps(action_payload),
                    now,
                    now,
                )

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
    try:
        await audit_append(
            db.pool("switchboard"),
            actor="owner",
            action="spend.rule.create",
            target=f"rule:{rule.id}",
            note=f"position={position} condition={condition_payload} action={action_payload}",
        )
    except Exception:
        logger.warning("Audit append failed for spend.rule.create", exc_info=True)
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

        def _dec2(v: object) -> dict:
            return v if isinstance(v, dict) else json.loads(v)  # type: ignore[arg-type]

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Validate rule exists inside the transaction to prevent TOCTOU races
                existing = await conn.fetchrow(
                    "SELECT id, position, condition, action, saved_7d, created_at, updated_at "
                    "FROM public.spend_rules WHERE id = $1",
                    uuid.UUID(rule_id),
                )
                if existing is None:
                    raise HTTPException(status_code=404, detail="Spend rule not found")

                new_condition = (
                    body.condition.model_dump(exclude_none=True)
                    if body.condition is not None
                    else _dec2(existing["condition"])
                )
                new_action = (
                    body.action.model_dump(exclude_none=True)
                    if body.action is not None
                    else _dec2(existing["action"])
                )
                new_position = body.position if body.position is not None else existing["position"]

                if body.position is not None and body.position != existing["position"]:
                    old_position = existing["position"]
                    # Shift intermediate rules in the right direction to maintain dense ordering
                    if new_position < old_position:
                        # Moving up: shift rules between [new_position, old_position) down by 1
                        await conn.execute(
                            "UPDATE public.spend_rules "
                            "SET position = position + 1, updated_at = $1 "
                            "WHERE position >= $2 AND position < $3 AND id != $4",
                            now,
                            new_position,
                            old_position,
                            uuid.UUID(rule_id),
                        )
                    else:
                        # Moving down: shift rules between (old_position, new_position] up by 1
                        await conn.execute(
                            "UPDATE public.spend_rules "
                            "SET position = position - 1, updated_at = $1 "
                            "WHERE position > $2 AND position <= $3 AND id != $4",
                            now,
                            old_position,
                            new_position,
                            uuid.UUID(rule_id),
                        )

                row = await conn.fetchrow(
                    "UPDATE public.spend_rules "
                    "SET position=$1, condition=$2, action=$3, updated_at=$4 "
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

    try:
        await audit_append(
            db.pool("switchboard"),
            actor="owner",
            action="spend.rule.update",
            target=f"rule:{rule_id}",
            note=f"position={new_position} condition={new_condition} action={new_action}",
        )
    except Exception:
        logger.warning("Audit append failed for spend.rule.update", exc_info=True)
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

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "DELETE FROM public.spend_rules WHERE id = $1 RETURNING position",
                    uuid.UUID(rule_id),
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="Spend rule not found")

                deleted_position = row["position"]
                await conn.execute(
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

    try:
        await audit_append(
            db.pool("switchboard"),
            actor="owner",
            action="spend.rule.delete",
            target=f"rule:{rule_id}",
        )
    except Exception:
        logger.warning("Audit append failed for spend.rule.delete", exc_info=True)


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

    try:
        await audit_append(
            db.pool("switchboard"),
            actor="owner",
            action="spend.ceiling.update",
            target="ceiling:1",
            note=f"monthly_usd={body.monthly_usd}",
        )
    except Exception:
        logger.warning("Audit append failed for spend.ceiling.update", exc_info=True)
    return ApiResponse[SpendCeiling](data=ceiling)
