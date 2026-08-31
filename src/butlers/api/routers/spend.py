"""Spend and usage tracking endpoints.

Provides aggregate spend summaries, daily time series, top-spending sessions,
spend breakdown, forecast, routing rules, and monthly ceiling.
Cost estimation uses the pricing.toml configuration loaded at startup.

Routes (§5.0):
  GET  /api/spend?period=          — aggregate summary (was /api/costs/summary)
  GET  /api/spend/daily            — daily time series (was /api/costs/daily)
  GET  /api/spend/top-sessions     — costliest sessions (was /api/costs/top-sessions)
  GET  /api/spend/by-schedule      — per-schedule cost analysis (was /api/costs/by-schedule)
  GET  /api/spend/breakdown?by=    — §5.2 butler|model|feature|purpose breakdown
  GET  /api/spend/forecast         — §5.2 naive linear extrapolation MTD → EOM
  GET  /api/spend/rules            — §5.2 list routing rules
  POST /api/spend/rules            — §5.2 create routing rule
  PUT  /api/spend/rules/{id}       — §5.2 update routing rule
  DELETE /api/spend/rules/{id}     — §5.2 delete routing rule
  PUT  /api/spend/ceiling          — §5.2 set monthly ceiling

Per-call spend events are delivered live over the unified fleet event bus
(``WS /api/events/stream``, see ``butlers.api.routers.events``) rather than a
dedicated stream — the earlier dedicated ``WS /api/spend/stream`` route was
retired in bu-01r64.2 once the bus fully covered this traffic.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import anyio
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_db_manager,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import (
    ApiMeta,
    ApiResponse,
    DailySpend,
    ScheduleCost,
    SpendDivergence,
    SpendSummary,
    TopSession,
    UnpricedModelUsage,
)
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers.audit import append as audit_append
from butlers.core.model_routing import (
    LedgerSpend,
    price_ledger_usage_rows,
    price_mtd_from_ledger,
)
from butlers.core.pricing import PricingConfig, estimate_session_cost
from butlers.core.sessions import (
    CADENCE_BASIS_DESCRIPTION,
    schedule_costs,
    sessions_daily,
    sessions_summary,
    top_sessions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spend", tags=["spend"])

_STATUS_TIMEOUT_S = 5.0
_SESSIONS_SUMMARY_TOOL = "sessions_summary"


def _is_tool_absent_error(exc: Exception, info: ButlerConnectionInfo) -> bool:
    """Return whether *exc* is a known structural absence for *info*.

    ``fastmcp.Client.call_tool`` raises ``ToolError`` (message ``"Unknown tool:
    '<name>'"``) when the server-side ``FastMCP`` instance has no tool
    matching the requested name. FastMCP intentionally uses the same error for
    a per-tool authorization denial, so that error text alone cannot establish
    absence. For this router, the known structural case is a **staffer**
    butler: ``sessions_summary`` /
    ``sessions_daily`` / ``top_sessions`` / ``schedule_costs`` are all
    registered only for non-STAFFER butlers (``core_tools/_sessions.py``,
    ``core_tools/_scheduling.py``), so every staffer (switchboard, messenger,
    qa) structurally lacks them regardless of its ``core_groups`` config. That
    is NOT a degraded source -- it must not land in ``unavailable_butlers``.
    A normal butler returning this error may instead have a denied registered
    tool, so it remains a genuine failure and must be tracked.
    """
    return (
        info.type == "staffer"
        and isinstance(exc, ToolError)
        and str(exc).startswith("Unknown tool:")
    )


def _get_db_manager() -> DatabaseManager | None:
    """Return dashboard DB manager when initialized, otherwise None.

    Ledger-authoritative aggregate routes surface a degraded response when this
    is unavailable; they never substitute session-derived prices.
    """
    try:
        return get_db_manager()
    except RuntimeError:
        return None


# The Spend dashboard's dollar-bearing aggregate surfaces share this single
# ledger spine. `sessions.model` describes the model requested for a session,
# not necessarily the catalog entry that actually executed it, so it is only
# suitable for diagnostics below -- never for pricing.
_LEDGER_USAGE_BY_DAY_SQL = """
SELECT
    (tul.recorded_at AT TIME ZONE 'UTC')::date AS day,
    tul.butler_name AS butler_name,
    COALESCE(tul.purpose, 'unknown') AS purpose,
    mc.model_id AS model_id,
    COUNT(*)::bigint AS calls,
    COALESCE(SUM(tul.input_tokens), 0)::bigint AS input_tokens,
    COALESCE(SUM(tul.output_tokens), 0)::bigint AS output_tokens,
    COALESCE(SUM(tul.cached_input_tokens), 0)::bigint AS cached_input_tokens,
    COALESCE(SUM(tul.cache_creation_tokens), 0)::bigint AS cache_creation_tokens
FROM public.token_usage_ledger tul
JOIN public.model_catalog mc ON mc.id = tul.catalog_entry_id
WHERE tul.recorded_at >= $1
  AND tul.recorded_at < $2
  AND ($3::text IS NULL OR tul.butler_name = $3)
GROUP BY day, tul.butler_name, COALESCE(tul.purpose, 'unknown'), mc.model_id
ORDER BY day, tul.butler_name, purpose, mc.model_id
"""

_HISTORICAL_MODEL_ATTRIBUTION_CUTOFF = date(2026, 7, 10)
_SESSION_LEDGER_DIVERGENCE_THRESHOLD = 0.05


def _utc_day_bounds(from_date: date, to_date: date) -> tuple[datetime, datetime]:
    """Return inclusive UTC calendar-day bounds for a date-only API range."""
    return (
        datetime.combine(from_date, datetime.min.time(), tzinfo=UTC),
        datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
    )


def _utc_today() -> date:
    """Return the UTC calendar day shared by spend API and ledger windows."""
    return datetime.now(UTC).date()


def _period_bounds(period: str) -> tuple[date, date]:
    """Resolve the established Spend presets to inclusive calendar dates."""
    today = _utc_today()
    if period == "today":
        return today, today
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "30d":
        return today - timedelta(days=29), today
    raise ValueError(f"Unsupported spend period: {period!r}")


def _historical_attribution_note(from_date: date) -> str | None:
    """Name the requested-versus-executed model distinction for legacy windows."""
    if from_date < _HISTORICAL_MODEL_ATTRIBUTION_CUTOFF:
        return (
            "Before 2026-07-10, legacy session model labels can name the requested "
            "model; dollar values here use the executed ledger model."
        )
    return None


async def _fetch_ledger_usage(
    db: DatabaseManager | None,
    from_date: date,
    to_date: date,
    *,
    butler: str | None = None,
) -> list[Mapping[str, object]] | None:
    """Fetch all pricing inputs from the executed-model ledger, or fail visibly."""
    if db is None:
        return None
    try:
        start_at, end_at = _utc_day_bounds(from_date, to_date)
        rows = await db.pool("switchboard").fetch(
            _LEDGER_USAGE_BY_DAY_SQL,
            start_at,
            end_at,
            butler,
        )
        return list(rows)
    except Exception:
        logger.warning("Failed to fetch ledger-backed spend aggregate", exc_info=True)
        return None


def _as_api_unpriced(usage: Iterable[object]) -> list[UnpricedModelUsage]:
    """Translate core ledger omissions into the public response shape."""
    return [
        UnpricedModelUsage(
            model=item.model,
            calls=item.calls,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            cached_input_tokens=item.cached_input_tokens,
            cache_creation_tokens=item.cache_creation_tokens,
        )
        for item in usage
    ]


def _sum_tokens(rows: Iterable[Mapping[str, object]]) -> tuple[int, int, int, int]:
    """Sum the four ledger token buckets without reinterpreting their meaning."""
    input_tokens = output_tokens = cached_input_tokens = cache_creation_tokens = 0
    for row in rows:
        input_tokens += int(row.get("input_tokens") or 0)
        output_tokens += int(row.get("output_tokens") or 0)
        cached_input_tokens += int(row.get("cached_input_tokens") or 0)
        cache_creation_tokens += int(row.get("cache_creation_tokens") or 0)
    return input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens


def _group_ledger_rows(
    rows: Iterable[Mapping[str, object]],
    key: str,
) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    return grouped


def _known_group_costs(
    rows: Iterable[Mapping[str, object]],
    key: str,
    pricing: PricingConfig,
) -> dict[str, float]:
    """Return priced group subtotals, never turning an all-unpriced group into zero."""
    costs: dict[str, float] = {}
    for label, group_rows in _group_ledger_rows(rows, key).items():
        group_spend = price_ledger_usage_rows(group_rows, pricing)
        # A group containing only unknown prices has no dollar value. A known
        # subscription/local zero is retained, which lets the UI distinguish it
        # from an omitted unknown model.
        if group_spend.cost_usd != 0.0 or not group_spend.unpriced_models:
            costs[label] = round(group_spend.cost_usd, 6)
    return costs


def _daily_ledger_spend(
    rows: Iterable[Mapping[str, object]],
    pricing: PricingConfig,
) -> list[DailySpend]:
    """Build daily actuals from ledger rows while retaining day-level omissions."""
    by_day = _group_ledger_rows(rows, "day")
    daily: list[DailySpend] = []
    for day, day_rows in sorted(by_day.items()):
        spend = price_ledger_usage_rows(day_rows, pricing)
        input_tokens, output_tokens, _, _ = _sum_tokens(day_rows)
        by_butler = _known_group_costs(day_rows, "butler_name", pricing)
        daily.append(
            DailySpend(
                date=day,
                cost_usd=round(spend.cost_usd, 6),
                sessions=sum(int(row.get("calls") or 0) for row in day_rows),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                by_butler=by_butler,
                unpriced_models=_as_api_unpriced(spend.unpriced_models),
            )
        )
    return daily


async def _session_token_totals_by_day(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    from_date: date,
    to_date: date,
) -> dict[str, int] | None:
    """Read session token totals for the diagnostic divergence detector only."""
    try:
        data = await sessions_daily(db.pool(info.name), from_date, to_date)
    except Exception:
        logger.warning(
            "Failed to fetch session tokens for ledger divergence (%s)",
            info.name,
            exc_info=True,
        )
        return None

    return {
        str(day["date"]): (
            int(day.get("input_tokens") or 0)
            + int(day.get("output_tokens") or 0)
            + int(day.get("cached_input_tokens") or 0)
            + int(day.get("cache_creation_tokens") or 0)
        )
        for day in data.get("days", [])
    }


async def _ledger_session_divergences(
    db: DatabaseManager | None,
    configs: list[ButlerConnectionInfo],
    from_date: date,
    to_date: date,
    rows: Iterable[Mapping[str, object]],
) -> tuple[list[SpendDivergence], bool]:
    """Compare session and ledger tokens without ever using sessions for money."""
    if db is None:
        return [], True

    ledger_by_butler_day: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        key = (str(row.get("butler_name") or "unknown"), str(row.get("day") or ""))
        ledger_by_butler_day[key] += sum(
            int(row.get(field) or 0)
            for field in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_creation_tokens",
            )
        )

    ledger_butlers = {name for name, _ in ledger_by_butler_day}
    configured_butlers = {info.name for info in configs}
    relevant = [info for info in configs if info.name in ledger_butlers]
    # Ledger rows can outlive a butler's current connection configuration. A
    # missing session pool is unknown diagnostic coverage, not proof that the
    # two stores agree, so preserve that deadman's degraded state.
    source_error = bool(ledger_butlers - configured_butlers)
    if not relevant:
        return [], source_error

    results = await asyncio.gather(
        *[_session_token_totals_by_day(db, info, from_date, to_date) for info in relevant]
    )

    divergences: list[SpendDivergence] = []
    for info, session_days in zip(relevant, results, strict=True):
        if session_days is None:
            source_error = True
            continue
        days = {day for (name, day) in ledger_by_butler_day if name == info.name} | set(
            session_days
        )
        for day in sorted(days):
            ledger_tokens = ledger_by_butler_day.get((info.name, day), 0)
            session_tokens = session_days.get(day, 0)
            denominator = max(ledger_tokens, session_tokens, 1)
            difference_ratio = abs(ledger_tokens - session_tokens) / denominator
            if difference_ratio > _SESSION_LEDGER_DIVERGENCE_THRESHOLD:
                divergences.append(
                    SpendDivergence(
                        date=day,
                        butler=info.name,
                        ledger_tokens=ledger_tokens,
                        session_tokens=session_tokens,
                        difference_ratio=round(difference_ratio, 6),
                    )
                )
    return divergences, source_error


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
            cache_creation_tokens=stats.get("cache_creation_tokens", 0),
            context_tokens=stats.get("context_tokens"),
        )
        if cost is None:
            continue
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
                cache_creation_tokens=stats.get("cache_creation_tokens", 0),
                context_tokens=stats.get("context_tokens"),
            )
            if cost is None:
                continue
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
            cost = estimate_session_cost(
                pricing,
                model_id,
                stats.get("input_tokens", 0),
                stats.get("output_tokens", 0),
                cached_input_tokens=stats.get("cached_input_tokens", 0),
                cache_creation_tokens=stats.get("cache_creation_tokens", 0),
                context_tokens=stats.get("context_tokens"),
            )
            if cost is not None:
                day_cost += cost
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
    *,
    tracker: DegradedSources | None = None,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Query a butler for session cost stats via the ``sessions_summary`` MCP tool.

    Returns (name, cost, sessions, input_tokens, output_tokens, by_model).

    On any of the failure branches below the returned tuple is a fabricated
    all-zero placeholder -- when *tracker* is provided, the butler is marked
    on it so callers can surface ``unavailable_butlers`` and treat the totals
    as a partial sum, never a confident fleet-wide total.
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
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary unavailable")
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invalid JSON from butler %s via %s: %s",
            info.name,
            _SESSIONS_SUMMARY_TOOL,
            exc,
        )
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary returned invalid JSON")
    except Exception as exc:
        if _is_tool_absent_error(exc, info):
            logger.debug(
                "Staffer %s has no %s tool registered -- legitimately absent, "
                "not a degraded source",
                info.name,
                _SESSIONS_SUMMARY_TOOL,
            )
            return (info.name, 0.0, 0, 0, 0, {})
        logger.warning(
            "Cost summary tool call failed for butler %s via %s (%s: %s)",
            info.name,
            _SESSIONS_SUMMARY_TOOL,
            type(exc).__name__,
            exc,
        )
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary tool call failed")
    return (info.name, 0.0, 0, 0, 0, {})


async def _get_butler_session_stats_for_range(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date,
    to_date: date,
    *,
    tracker: DegradedSources | None = None,
) -> tuple[str, float, int, int, int, dict[str, float]]:
    """Query a butler for session cost stats over a custom date range.

    Uses ``sessions_daily`` and aggregates totals across [from_date, to_date].
    Returns (name, cost, sessions, input_tokens, output_tokens, by_model).

    See ``_get_butler_session_stats`` for the *tracker*/``unavailable_butlers``
    contract on failure.
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
                            cache_creation_tokens=stats.get("cache_creation_tokens", 0),
                            context_tokens=stats.get("context_tokens"),
                        )
                        if cost is None:
                            continue
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
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary (date range) unavailable")
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invalid JSON from butler %s via sessions_daily: %s",
            info.name,
            exc,
        )
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary (date range) returned invalid JSON")
    except Exception as exc:
        if _is_tool_absent_error(exc, info):
            logger.debug(
                "Staffer %s has no sessions_daily tool registered -- "
                "legitimately absent, not a degraded source",
                info.name,
            )
            return (info.name, 0.0, 0, 0, 0, {})
        logger.warning(
            "Cost summary (date range) tool call failed for butler %s via sessions_daily (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        if tracker is not None:
            tracker.mark(info.name, msg="Cost summary (date range) tool call failed")
    return (info.name, 0.0, 0, 0, 0, {})


class FleetHaltAttentionEpisode(BaseModel):
    """Content-blind durable delivery evidence for the current breach window."""

    episode_id: uuid.UUID
    lifecycle_state: str
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None = None
    safe_reason: str | None = None


class FleetHaltAttentionObservation(BaseModel):
    """Fleet-halt outbox availability, distinct from an empty observation."""

    available: bool
    episode: FleetHaltAttentionEpisode | None = None


_FLEET_ATTENTION_REASON_COPY = {
    ("pre_transport", "recipient_unavailable"): "Recipient unavailable before delivery",
    ("pre_transport", "policy_denied"): "Delivery policy denied the alert",
    ("transport_rejected", "provider_rejected"): "Transport rejected the alert",
    ("transport_uncertain", "transport_timeout"): "Delivery timed out; outcome is uncertain",
    (
        "transport_uncertain",
        "transport_connection_lost",
    ): "Delivery connection was lost; outcome is uncertain",
    ("transport_uncertain", "worker_recovery"): "A dead delivery claim was fenced as uncertain",
}


@router.get(
    "/runtime-attention",
    response_model=ApiResponse[FleetHaltAttentionObservation],
)
async def get_fleet_halt_runtime_attention(
    _owner: str = Depends(require_dashboard_owner_control),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[FleetHaltAttentionObservation]:
    """Return sanitized durable alert evidence without weakening Spend data."""
    if db is None:
        return ApiResponse[FleetHaltAttentionObservation](
            data=FleetHaltAttentionObservation(available=False)
        )
    try:
        pool = db.credential_shared_pool()
        row = await pool.fetchrow("SELECT * FROM public.observe_runtime_attention_fleet_halt()")
    except Exception:
        logger.warning("Spend fleet-halt runtime-attention observation unavailable")
        return ApiResponse[FleetHaltAttentionObservation](
            data=FleetHaltAttentionObservation(available=False)
        )
    if row is None:
        return ApiResponse[FleetHaltAttentionObservation](
            data=FleetHaltAttentionObservation(available=True)
        )
    safe_reason = _FLEET_ATTENTION_REASON_COPY.get(
        (row["delivery_error_class"], row["delivery_error_detail"])
    )
    episode = FleetHaltAttentionEpisode(
        episode_id=row["episode_id"],
        lifecycle_state=row["lifecycle_state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivered_at=row["delivered_at"],
        safe_reason=safe_reason,
    )
    return ApiResponse[FleetHaltAttentionObservation](
        data=FleetHaltAttentionObservation(available=True, episode=episode)
    )


@router.get("", response_model=ApiResponse[SpendSummary])
@router.get("/summary", response_model=ApiResponse[SpendSummary], include_in_schema=False)
async def get_cost_summary(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
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
    if from_date is not None and to_date is not None:
        range_from, range_to = from_date, to_date
        period_label = f"{from_date.isoformat()}/{to_date.isoformat()}"
    else:
        range_from, range_to = _period_bounds(period)
        period_label = period
    if butler is not None:
        configs = [info for info in configs if info.name == butler]

    rows = await _fetch_ledger_usage(db, range_from, range_to, butler=butler)
    attribution_note = _historical_attribution_note(range_from)
    if rows is None:
        return ApiResponse[SpendSummary](
            data=SpendSummary(
                period=period_label,
                total_cost_usd=0.0,
                total_sessions=0,
                total_input_tokens=0,
                total_output_tokens=0,
                historical_attribution_note=attribution_note,
                source_error=True,
            )
        )

    spend = price_ledger_usage_rows(rows, pricing)
    input_tokens, output_tokens, _, _ = _sum_tokens(rows)
    divergences, divergence_source_error = await _ledger_session_divergences(
        db, configs, range_from, range_to, rows
    )
    return ApiResponse[SpendSummary](
        data=SpendSummary(
            period=period_label,
            total_cost_usd=round(spend.cost_usd, 6),
            total_sessions=sum(int(row.get("calls") or 0) for row in rows),
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            by_butler=_known_group_costs(rows, "butler_name", pricing),
            by_model=_known_group_costs(rows, "model_id", pricing),
            unpriced_models=_as_api_unpriced(spend.unpriced_models),
            divergences=divergences,
            divergence_source_error=divergence_source_error,
            historical_attribution_note=attribution_note,
        )
    )


async def _get_butler_daily_stats(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: str,
    to_date: str,
    *,
    tracker: DegradedSources | None = None,
) -> list[dict]:
    """Query a butler for daily session stats via the ``sessions_daily`` MCP tool.

    Returns a list of dicts with keys: date, cost_usd, sessions, input_tokens,
    output_tokens.

    On failure this returns ``[]``, indistinguishable from "genuinely has no
    sessions in range" -- when *tracker* is provided the butler is marked on
    it so callers can surface ``unavailable_butlers``.
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
                        cost = estimate_session_cost(
                            pricing,
                            model_id,
                            stats.get("input_tokens", 0),
                            stats.get("output_tokens", 0),
                            cached_input_tokens=stats.get("cached_input_tokens", 0),
                            cache_creation_tokens=stats.get("cache_creation_tokens", 0),
                            context_tokens=stats.get("context_tokens"),
                        )
                        if cost is not None:
                            day_cost += cost
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
    except (ButlerUnreachableError, TimeoutError, Exception) as exc:
        if _is_tool_absent_error(exc, info):
            logger.debug(
                "Staffer %s has no sessions_daily tool registered -- "
                "legitimately absent, not a degraded source",
                info.name,
            )
            return []
        if tracker is not None:
            tracker.mark(info.name, msg="Daily cost query failed")
    return []


@router.get("/daily", response_model=ApiResponse[list[DailySpend]])
async def get_daily_costs(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
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

    Actual tokens and priced subtotals are grouped from the executing-model
    ledger. Session reads are diagnostic-only divergence evidence; this route
    does not fall back to ``sessions_daily`` for money.
    """
    if to_date is None:
        to_date = _utc_today()
    if from_date is None:
        from_date = to_date - timedelta(days=6)

    if butler is not None:
        configs = [info for info in configs if info.name == butler]

    rows = await _fetch_ledger_usage(db, from_date, to_date, butler=butler)
    attribution_note = _historical_attribution_note(from_date)
    if rows is None:
        return ApiResponse[list[DailySpend]](
            data=[],
            meta=ApiMeta(
                source_error=True,
                historical_attribution_note=attribution_note,
            ),
        )

    spend = price_ledger_usage_rows(rows, pricing)
    divergences, divergence_source_error = await _ledger_session_divergences(
        db, configs, from_date, to_date, rows
    )
    return ApiResponse[list[DailySpend]](
        data=_daily_ledger_spend(rows, pricing),
        meta=ApiMeta(
            unpriced_models=[item.model_dump() for item in _as_api_unpriced(spend.unpriced_models)],
            divergences=[item.model_dump() for item in divergences],
            divergence_source_error=divergence_source_error,
            historical_attribution_note=attribution_note,
        ),
    )


def _top_sessions_from_data(
    name: str,
    data: dict,
    pricing: PricingConfig,
) -> list[TopSession]:
    """Build priced ``TopSession`` records from a ``top_sessions`` payload.

    The payload shape is identical whether it came from the butler's
    ``top_sessions`` MCP tool or a direct ``core.sessions.top_sessions`` DB read
    (the MCP tool is a thin wrapper over that helper), so both paths share this
    builder — the DB-first path is byte-for-byte parity with the MCP path.
    """
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
            cache_creation_tokens=s.get("cache_creation_tokens", 0),
            context_tokens=s.get("context_tokens"),
        )
        if cost is None:
            continue
        sessions.append(
            TopSession(
                session_id=s.get("session_id", ""),
                butler=name,
                cost_usd=round(cost, 6),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model_id,
                started_at=s.get("started_at", ""),
            )
        )
    return sessions


async def _get_butler_top_sessions_from_db(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    limit: int,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[TopSession] | None:
    """Read a butler's costliest sessions directly from its DB pool.

    Returns the priced ``TopSession`` list on success (possibly empty when the
    butler genuinely has no completed sessions), or ``None`` when the pool is
    absent or the query fails so the caller falls back to the MCP tool. A butler
    whose sessions table does not exist yet (schema not provisioned) raises here
    and yields ``None`` -- legitimately absent, classified by the fallback, never
    marked degraded on the DB miss alone.
    """
    try:
        data = await top_sessions(db.pool(info.name), limit, from_date, to_date)
    except KeyError:
        logger.debug("Top sessions DB pool unavailable for butler %s", info.name)
        return None
    except Exception as exc:
        logger.warning(
            "Top sessions DB query failed for butler %s (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        return None
    return _top_sessions_from_data(info.name, data, pricing)


async def _butler_top_sessions_db_first(
    db: DatabaseManager | None,
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    limit: int,
    from_date: date | None = None,
    to_date: date | None = None,
    *,
    tracker: DegradedSources | None = None,
) -> list[TopSession]:
    """DB-first, MCP-fallback per butler (bu-h1i8k).

    Tries the direct DB read first (which works even for the staffer butlers
    switchboard/messenger/qa, whose ``top_sessions`` MCP tool is structurally
    absent -- filling a permanent evidence hole). On pool absence/query error
    the DB helper returns ``None`` and we fall back to the MCP tool; only if BOTH
    paths fail is the butler marked on *tracker* (via the MCP helper's own
    degraded classification), preserving the ``unavailable_butlers`` contract.
    """
    if db is not None:
        db_result = await _get_butler_top_sessions_from_db(
            db, info, pricing, limit, from_date, to_date
        )
        if db_result is not None:
            return db_result
    return await _get_butler_top_sessions(
        mgr, info, pricing, limit, from_date, to_date, tracker=tracker
    )


async def _get_butler_top_sessions(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    limit: int,
    from_date: date | None = None,
    to_date: date | None = None,
    *,
    tracker: DegradedSources | None = None,
) -> list[TopSession]:
    """Query a single butler for its most expensive sessions.

    When ``from_date``/``to_date`` are provided, results are scoped to sessions
    started within that inclusive date range; otherwise all-time results are
    returned (pre-existing behavior).

    Returns a list of TopSession records with costs calculated from pricing
    config.  On failure this returns ``[]``, indistinguishable from
    "genuinely has no sessions" -- when *tracker* is provided the butler is
    marked on it so callers can surface ``unavailable_butlers``.
    """
    args: dict[str, object] = {"limit": limit}
    if from_date is not None and to_date is not None:
        args["from_date"] = from_date.isoformat()
        args["to_date"] = to_date.isoformat()
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool("top_sessions", args),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                return _top_sessions_from_data(info.name, data, pricing)
    except (ButlerUnreachableError, TimeoutError, Exception) as exc:
        if _is_tool_absent_error(exc, info):
            logger.debug(
                "Staffer %s has no top_sessions tool registered -- "
                "legitimately absent, not a degraded source",
                info.name,
            )
            return []
        if tracker is not None:
            tracker.mark(info.name, msg="Top sessions query failed")
    return []


@router.get("/top-sessions", response_model=ApiResponse[list[TopSession]])
async def get_top_sessions(
    limit: int = Query(default=10, ge=1, le=50),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[list[TopSession]]:
    """Return most expensive sessions across all butlers.

    Reads each butler's costliest sessions DB-first (via
    ``core.sessions.top_sessions``) with the ``top_sessions`` MCP tool as a
    per-butler fallback, merges the results, calculates costs using the pricing
    config, and returns the top *limit* sessions sorted by cost descending. The
    DB-first path also surfaces the staffer butlers (switchboard/messenger/qa)
    whose MCP tool is structurally absent (bu-h1i8k).

    When ``from`` and ``to`` query params are provided (ISO 8601 date strings,
    matching ``/api/spend/daily``), results are scoped to sessions started
    within that inclusive date range. When omitted, all-time results are
    returned (pre-existing behavior).  Both must be provided together, and
    ``from`` must not be later than ``to``.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response.
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

    tracker = DegradedSources(logger)
    tasks = [
        _butler_top_sessions_db_first(
            db, mgr, info, pricing, limit, from_date, to_date, tracker=tracker
        )
        for info in configs
    ]
    results = await asyncio.gather(*tasks)

    all_sessions: list[TopSession] = []
    for sessions in results:
        all_sessions.extend(sessions)

    all_sessions.sort(key=lambda s: s.cost_usd, reverse=True)
    meta = ApiMeta(unavailable_butlers=sorted(tracker.names)) if tracker.failed else ApiMeta()
    return ApiResponse[list[TopSession]](data=all_sessions[:limit], meta=meta)


def _schedule_costs_from_data(
    name: str,
    data: dict,
    pricing: PricingConfig,
) -> list[ScheduleCost]:
    """Build priced, per-schedule-merged ``ScheduleCost`` records from a payload.

    Shared by the MCP and DB-first paths (identical payload shape — the MCP tool
    wraps ``core.sessions.schedule_costs``).

    ``core/sessions.py::schedule_costs`` groups its SQL by (name, cron, model),
    so a schedule that ran under 2+ models in the window emits one raw row PER
    MODEL. Price each model fragment individually (pricing is model-specific) but
    merge fragments sharing a schedule name into a single bucket BEFORE building
    ``ScheduleCost`` objects, so exactly one entry per (butler, schedule_name) is
    returned -- otherwise a multi-model schedule silently splits into
    under-ranked fragments and collides on the frontend's
    ``${butler}-${schedule_name}`` React key (bu-hmdqz.7).
    """
    merged: dict[str, dict] = {}
    for entry in data.get("schedules", []):
        schedule_name = entry.get("name", "")
        model_id = entry.get("model", "")
        input_tokens = entry.get("total_input_tokens", 0)
        output_tokens = entry.get("total_output_tokens", 0)
        fragment_cost = estimate_session_cost(
            pricing,
            model_id,
            input_tokens,
            output_tokens,
            cached_input_tokens=entry.get("total_cached_input_tokens", 0),
            cache_creation_tokens=entry.get("total_cache_creation_tokens", 0),
            context_tokens=entry.get("context_tokens"),
        )
        if fragment_cost is None:
            continue
        bucket = merged.setdefault(
            schedule_name,
            {
                "cron": entry.get("cron", ""),
                "total_runs": 0,
                "total_cost_usd": 0.0,
                # The cadence is derived from the cron alone (see
                # ``core.sessions._estimate_monthly_runs``), so it is identical
                # across every model fragment of the same schedule -- take it
                # once, don't sum.
                "projected_monthly_runs": entry.get("projected_monthly_runs", 0.0),
            },
        )
        bucket["total_runs"] = bucket["total_runs"] + entry.get("total_runs", 0)
        bucket["total_cost_usd"] = bucket["total_cost_usd"] + fragment_cost

    costs: list[ScheduleCost] = []
    for schedule_name, bucket in merged.items():
        total_runs = bucket["total_runs"]
        total_cost = bucket["total_cost_usd"]
        avg_cost = total_cost / total_runs if total_runs > 0 else 0.0
        # Forecast, kept strictly separate from the measured totals above: the
        # projected monthly cost is avg-cost-per-run x the cron's own monthly
        # cadence, on the basis named in the response envelope's
        # ``forecast_basis``. There is no bare multiplier here -- the ~30x that
        # used to sit at this line reported a weekly schedule as thirty monthly
        # runs (bu-6jv4m.2).
        monthly_runs = bucket["projected_monthly_runs"]
        costs.append(
            ScheduleCost(
                schedule_name=schedule_name,
                butler=name,
                cron=bucket["cron"],
                total_runs=total_runs,
                total_cost_usd=round(total_cost, 6),
                avg_cost_per_run=round(avg_cost, 6),
                projected_monthly_runs=round(monthly_runs, 4),
                projected_monthly_usd=round(avg_cost * monthly_runs, 6),
            )
        )
    return costs


async def _get_butler_schedule_costs_from_db(
    db: DatabaseManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[ScheduleCost] | None:
    """Read a butler's per-schedule costs directly from its DB pool.

    Returns the priced ``ScheduleCost`` list on success (possibly empty), or
    ``None`` when the pool is absent or the query fails so the caller falls back
    to the MCP tool. A butler whose sessions/scheduled_tasks tables do not exist
    yet raises here and yields ``None`` -- classified by the fallback, never
    marked degraded on the DB miss alone.
    """
    try:
        data = await schedule_costs(db.pool(info.name), from_date, to_date)
    except KeyError:
        logger.debug("Schedule cost DB pool unavailable for butler %s", info.name)
        return None
    except Exception as exc:
        logger.warning(
            "Schedule cost DB query failed for butler %s (%s: %s)",
            info.name,
            type(exc).__name__,
            exc,
        )
        return None
    return _schedule_costs_from_data(info.name, data, pricing)


async def _butler_schedule_costs_db_first(
    db: DatabaseManager | None,
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date | None = None,
    to_date: date | None = None,
    *,
    tracker: DegradedSources | None = None,
) -> list[ScheduleCost]:
    """DB-first, MCP-fallback per butler (bu-h1i8k).

    Tries the direct DB read first (which works for the staffer butlers
    switchboard/messenger/qa whose ``schedule_costs`` MCP tool is structurally
    absent). On pool absence/query error the DB helper returns ``None`` and we
    fall back to the MCP tool; only if BOTH fail is the butler marked on
    *tracker* (via the MCP helper's degraded classification).
    """
    if db is not None:
        db_result = await _get_butler_schedule_costs_from_db(db, info, pricing, from_date, to_date)
        if db_result is not None:
            return db_result
    return await _get_butler_schedule_costs(mgr, info, pricing, from_date, to_date, tracker=tracker)


async def _get_butler_schedule_costs(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    pricing: PricingConfig,
    from_date: date | None = None,
    to_date: date | None = None,
    *,
    tracker: DegradedSources | None = None,
) -> list[ScheduleCost]:
    """Query a butler for per-schedule cost data.

    When ``from_date``/``to_date`` are provided, runs are scoped to that
    inclusive date range; otherwise all-time totals are returned (pre-existing
    behavior).

    On failure this returns ``[]``, indistinguishable from "genuinely has no
    schedules" -- when *tracker* is provided the butler is marked on it so
    callers can surface ``unavailable_butlers``.
    """
    args: dict[str, object] = {}
    if from_date is not None and to_date is not None:
        args["from_date"] = from_date.isoformat()
        args["to_date"] = to_date.isoformat()
    try:
        client = await asyncio.wait_for(mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S)
        result = await asyncio.wait_for(
            client.call_tool("schedule_costs", args),
            timeout=_STATUS_TIMEOUT_S,
        )
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                data = json.loads(text)
                return _schedule_costs_from_data(info.name, data, pricing)
    except (ButlerUnreachableError, TimeoutError, Exception) as exc:
        if _is_tool_absent_error(exc, info):
            logger.debug(
                "Staffer %s has no schedule_costs tool registered -- "
                "legitimately absent, not a degraded source",
                info.name,
            )
            return []
        if tracker is not None:
            tracker.mark(info.name, msg="Schedule cost query failed")
    return []


@router.get("/by-schedule", response_model=ApiResponse[list[ScheduleCost]])
async def get_costs_by_schedule(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    butler: str | None = Query(None, description="Filter to a single butler by name"),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[list[ScheduleCost]]:
    """Return per-schedule cost analysis across all butlers.

    When ``from`` and ``to`` query params are provided (ISO 8601 date strings,
    matching ``/api/spend/daily``), run totals are scoped to that inclusive
    date range (schedules with no runs in the window still appear, zeroed).
    When omitted, all-time totals are returned (pre-existing behavior).  Both
    must be provided together, and ``from`` must not be later than ``to``.

    When ``butler`` is provided, only that butler's data is included.  An
    unknown butler name returns an empty 200 response.
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

    # A butler whose schedule_costs call fails returns [] -- indistinguishable
    # from "genuinely has no schedules" -- so it silently vanishes from the
    # merged ranking. Track genuine failures and surface them in
    # meta.unavailable_butlers so the frontend can footnote the (undercounting)
    # table rather than read it as complete (mirrors /daily and /top-sessions;
    # bu-h3ej9).
    tracker = DegradedSources(logger)
    tasks = [
        _butler_schedule_costs_db_first(db, mgr, info, pricing, from_date, to_date, tracker=tracker)
        for info in configs
    ]
    results = await asyncio.gather(*tasks)
    all_costs = [c for butler_costs in results for c in butler_costs]
    all_costs.sort(key=lambda c: c.projected_monthly_usd, reverse=True)
    # The forecast basis is a constant of the estimator, not a property of any
    # one schedule, so it is stated once on the envelope (bu-6jv4m.2).
    meta = ApiMeta(
        forecast_basis=CADENCE_BASIS_DESCRIPTION,
        **({"unavailable_butlers": sorted(tracker.names)} if tracker.failed else {}),
    )
    return ApiResponse[list[ScheduleCost]](data=all_costs, meta=meta)


# Aggregate current-month token usage per (purpose, model_id) directly from the
# shared cross-butler ledger (bu-qvnce.12/core_156).  Unlike butler/model/feature,
# this dimension is not a per-butler fan-out -- ``purpose`` lives on
# ``public.token_usage_ledger`` itself, so a single query against any pool that can
# see ``public`` (the "switchboard" pool, matching ``spend_rules``/``spend_ceiling``
# elsewhere in this router) answers it for the whole fleet.  Mirrors
# ``model_routing._MTD_USAGE_BY_MODEL_SQL`` with an added ``purpose`` grouping key.
_MTD_USAGE_BY_PURPOSE_SQL = """
SELECT
    COALESCE(tul.purpose, 'unknown') AS purpose,
    mc.model_id AS model_id,
    COALESCE(SUM(tul.input_tokens), 0)  AS input_tokens,
    COALESCE(SUM(tul.output_tokens), 0) AS output_tokens,
    COALESCE(SUM(tul.cached_input_tokens), 0)   AS cached_input_tokens,
    COALESCE(SUM(tul.cache_creation_tokens), 0) AS cache_creation_tokens
FROM public.token_usage_ledger tul
JOIN public.model_catalog mc ON mc.id = tul.catalog_entry_id
WHERE tul.recorded_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
GROUP BY COALESCE(tul.purpose, 'unknown'), mc.model_id
"""


async def _get_spend_breakdown_by_purpose(
    db: DatabaseManager | None,
    pricing: PricingConfig,
) -> dict:
    """Return the ``by=purpose`` breakdown payload, priced from the shared ledger.

    Returns ``breakdown={}`` with ``source_error=True`` when the DB-backed path is
    unavailable (no ``DatabaseManager``) or the ledger query fails -- there is no MCP
    fallback for this dimension (no per-butler tool exposes ``token_usage_ledger``
    rows), so a failure here must never be mistaken for "genuinely no purpose-tagged
    spend this month" (see butlers/CLAUDE.md degraded-mode envelope convention).
    """
    if db is None:
        return {"by": "purpose", "breakdown": {}, "source_error": True}
    try:
        pool = db.pool("switchboard")
        rows = await pool.fetch(_MTD_USAGE_BY_PURPOSE_SQL)
    except Exception:
        logger.warning("Failed to fetch purpose spend breakdown", exc_info=True)
        return {"by": "purpose", "breakdown": {}, "source_error": True}

    breakdown: dict[str, float] = {}
    for row in rows:
        cost = estimate_session_cost(
            pricing,
            row["model_id"] or "unknown",
            int(row["input_tokens"]),
            int(row["output_tokens"]),
            cached_input_tokens=int(row["cached_input_tokens"]),
            cache_creation_tokens=int(row["cache_creation_tokens"]),
        )
        if cost is None:
            continue
        label = row["purpose"]
        breakdown[label] = round(breakdown.get(label, 0.0) + cost, 6)
    return {"by": "purpose", "breakdown": breakdown, "source_error": False}


# ---------------------------------------------------------------------------
# §5.2 — Breakdown endpoint
# ---------------------------------------------------------------------------


@router.get("/breakdown", response_model=ApiResponse[dict])
async def get_spend_breakdown(
    by: Literal["butler", "model", "feature", "purpose"] = Query(
        "butler", description="Dimension to break spend down by"
    ),
    db: DatabaseManager | None = Depends(_get_db_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
    mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[dict]:
    """Return spend broken down by butler, model, feature, or purpose for the current month.

    ``butler``, ``model``, and ``purpose`` are all grouped directly from
    ``public.token_usage_ledger`` using the catalog entry that executed each
    call. The ``feature`` dimension retains its by-schedule metadata evidence
    path; a richer feature taxonomy is deferred to a future revision.
    """
    if by in {"butler", "model", "purpose"}:
        today = _utc_today()
        month_start = today.replace(day=1)
        rows = await _fetch_ledger_usage(db, month_start, today)
        attribution_note = _historical_attribution_note(month_start)
        if rows is None:
            return ApiResponse[dict](
                data={
                    "by": by,
                    "breakdown": {},
                    "unpriced_models": [],
                    "billing_classes": {},
                    "source_error": True,
                    "divergences": [],
                    "divergence_source_error": True,
                    "historical_attribution_note": attribution_note,
                }
            )

        group_key = {
            "butler": "butler_name",
            "model": "model_id",
            "purpose": "purpose",
        }[by]
        spend = price_ledger_usage_rows(rows, pricing)
        divergences, divergence_source_error = await _ledger_session_divergences(
            db, configs, month_start, today, rows
        )
        model_classes = (
            {
                model: billing_class
                for model in _group_ledger_rows(rows, "model_id")
                if (billing_class := pricing.billing_class_for(model)) is not None
            }
            if by == "model"
            else {}
        )
        return ApiResponse[dict](
            data={
                "by": by,
                "breakdown": _known_group_costs(rows, group_key, pricing),
                "unpriced_models": [
                    item.model_dump() for item in _as_api_unpriced(spend.unpriced_models)
                ],
                "billing_classes": model_classes,
                "source_error": False,
                "divergences": [item.model_dump() for item in divergences],
                "divergence_source_error": divergence_source_error,
                "historical_attribution_note": attribution_note,
            }
        )

    # by == "feature": proxy to schedule-level spend (DB-first, MCP fallback).
    # This metadata-oriented table is intentionally left on its existing
    # schedule evidence path; it is not used by the aggregate ledger spine.
    feature_tracker = DegradedSources(logger)
    schedule_tasks = [
        _butler_schedule_costs_db_first(db, mgr, info, pricing, tracker=feature_tracker)
        for info in configs
    ]
    schedule_results = await asyncio.gather(*schedule_tasks)
    all_costs = [c for butler_costs in schedule_results for c in butler_costs]
    breakdown = {c.schedule_name: round(c.total_cost_usd, 6) for c in all_costs}
    return ApiResponse[dict](
        data={
            "by": "feature",
            "breakdown": breakdown,
            "unavailable_butlers": sorted(feature_tracker.names),
        }
    )


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
    # True when pricing MTD from public.token_usage_ledger (the same source
    # check_monthly_ceiling gates spawns on) failed or no DatabaseManager is
    # wired -- mtd_usd/projected_eom_usd/ceiling_usd are then fabricated
    # zeros/None, not a genuine "$0 month" (bu-7o89u.1 degraded envelope).
    ceiling_source_error: bool = False
    unpriced_models: list[UnpricedModelUsage] = Field(default_factory=list)
    # The ceiling still evaluates the known-priced subtotal for availability,
    # but this count makes its unpriced coverage explicit to the operator.
    ceiling_blind_to_unpriced_models: int = 0
    divergences: list[SpendDivergence] = Field(default_factory=list)
    divergence_source_error: bool = False
    historical_attribution_note: str | None = None
    # Retained as an additive compatibility field for older clients. Ledger
    # daily actuals have no per-butler fan-out exclusion path.
    unavailable_butlers: list[str] = Field(default_factory=list)


@router.get("/forecast", response_model=ApiResponse[ForecastResponse])
async def get_spend_forecast(
    db: DatabaseManager | None = Depends(_get_db_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[ForecastResponse]:
    """Return a naive linear spend forecast for the current month.

    Algorithm (§D5): ``mtd_total ÷ max(days_elapsed, 1) × days_in_month``.
    Returns a daily series (solid = actual, dashed = projected from today) plus
    a projected end-of-month total.

    MTD (``mtd_usd``) and the derived ``projected_eom_usd``/``ceiling_usd`` are
    priced from ``public.token_usage_ledger`` via
    ``butlers.core.model_routing.price_mtd_from_ledger`` — the exact helper
    ``check_monthly_ceiling`` uses to gate spawns (bu-7o89u.1) — so this
    dashboard figure can never diverge from the number that halts the fleet.
    A ledger failure (or no ``DatabaseManager`` wired) sets
    ``ceiling_source_error=True`` and reports ``mtd_usd=0``/``ceiling_usd=None``
    rather than falling back to a different, potentially-divergent source.

    The per-day ``days`` series is grouped from the same ledger input as MTD,
    so solid actuals and the projected rate retain one executed-model spine.
    Session reads are diagnostic-only divergence evidence and never supply a
    fallback dollar figure.

    TODO: replace the naive daily-rate extrapolation with a smarter estimator
    (per-butler decay weighting, weekend vs weekday adjustment, etc.)
    """
    today = _utc_today()
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = (today - month_start).days + 1  # inclusive of today

    # Fetch both the daily actuals and the gate total from the ledger. A
    # session fan-out is deliberately not a fallback here: it would revive the
    # requested-model attribution bug this endpoint exists to prevent.
    ceiling_source_error = False
    ledger_rows: list[Mapping[str, object]] = []
    mtd_spend = LedgerSpend(cost_usd=0.0)
    ceiling_usd: float | None = None
    if db is None:
        ceiling_source_error = True
    else:
        try:
            pool = db.pool("switchboard")
            start_at, end_at = _utc_day_bounds(month_start, today)
            ledger_rows = list(await pool.fetch(_LEDGER_USAGE_BY_DAY_SQL, start_at, end_at, None))
            mtd_spend = await price_mtd_from_ledger(pool, pricing)
            ceiling_row = await pool.fetchrow(
                "SELECT monthly_usd FROM public.spend_ceiling WHERE id = 1"
            )
            if ceiling_row:
                ceiling_usd = float(ceiling_row["monthly_usd"])
        except Exception:
            logger.warning(
                "Failed to price MTD from ledger for /spend/forecast; "
                "reporting ceiling_source_error instead of a fabricated total",
                exc_info=True,
            )
            ceiling_source_error = True
            ledger_rows = []
            mtd_spend = LedgerSpend(cost_usd=0.0)
            ceiling_usd = None

    daily_actuals = {day.date: day.cost_usd for day in _daily_ledger_spend(ledger_rows, pricing)}
    divergences, divergence_source_error = await _ledger_session_divergences(
        db, configs, month_start, today, ledger_rows
    )
    daily_rate = mtd_spend.cost_usd / max(days_elapsed, 1)
    projected_eom_usd = daily_rate * days_in_month

    # Build solid actuals + dashed projection series from the same ledger cost
    # calculation. When the source is degraded, the frontend gates the
    # fabricated zero projection rather than reading it as a calm all-clear.
    forecast_days: list[ForecastDay] = []
    current = month_start
    month_end = month_start.replace(day=days_in_month)
    while current <= month_end:
        iso = current.isoformat()
        if current <= today:
            forecast_days.append(
                ForecastDay(
                    date=iso,
                    cost_usd=round(daily_actuals.get(iso, 0.0), 6),
                    projected=False,
                )
            )
        else:
            forecast_days.append(
                ForecastDay(date=iso, cost_usd=round(daily_rate, 6), projected=True)
            )
        current += timedelta(days=1)

    return ApiResponse[ForecastResponse](
        data=ForecastResponse(
            days=forecast_days,
            projected_eom_usd=round(projected_eom_usd, 6),
            days_in_month=days_in_month,
            days_elapsed=days_elapsed,
            mtd_usd=round(mtd_spend.cost_usd, 6),
            ceiling_usd=ceiling_usd,
            projection_confidence=projection_confidence_for(days_elapsed),
            ceiling_source_error=ceiling_source_error,
            unpriced_models=_as_api_unpriced(mtd_spend.unpriced_models),
            ceiling_blind_to_unpriced_models=len(mtd_spend.unpriced_models),
            divergences=divergences,
            divergence_source_error=divergence_source_error,
            historical_attribution_note=_historical_attribution_note(month_start),
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
    site: ``butler``, ``complexity`` (alias ``tier``), and ``trigger`` (alias
    ``purpose`` — both evaluate against the dispatch ``trigger_source``; ``purpose``
    matches the vocabulary ``/spend/breakdown?by=purpose`` and
    ``public.token_usage_ledger.purpose`` use for the same dimension, see bu-qvnce.12).
    Each may be a scalar or a list (membership match).  ``trigger`` and ``purpose`` are
    mutually exclusive within one condition (422 if both are set) since they alias the
    same underlying value and could never both hold at once.
    """

    model_config = ConfigDict(extra="forbid")

    butler: str | list[str] | None = None
    complexity: str | list[str] | None = None
    tier: str | list[str] | None = None
    trigger: str | list[str] | None = None
    purpose: str | list[str] | None = None

    @model_validator(mode="after")
    def _validate_trigger_purpose_alias(self) -> SpendRuleCondition:
        # trigger and purpose both evaluate against the same dispatch trigger_source
        # (see _rule_condition_matches) -- a condition specifying both can never match
        # unless their values happen to agree, which is never a legitimate rule intent.
        # Reject at create/update time (422) rather than persist a rule that silently
        # fails closed forever at dispatch.
        if self.trigger is not None and self.purpose is not None:
            raise ValueError(
                "condition cannot set both 'trigger' and 'purpose' -- they are aliases "
                "for the same dispatch trigger_source dimension; a rule combining both "
                "could never match"
            )
        return self

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
