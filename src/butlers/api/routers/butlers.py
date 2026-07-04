"""Butler discovery and status endpoints.

Scans the roster directory for butler configs, then probes each butler's
MCP server in parallel to determine live status.  Unreachable butlers
(timeout, connection refused, etc.) are reported with ``status: "down"``
rather than causing the entire request to fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import anyio
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import (
    ApiResponse,
    ButlerConfigResponse,
    ButlerDetail,
    ButlerSummary,
    MCPToolCallRequest,
    MCPToolCallResponse,
    MCPToolInfo,
    ModuleInfo,
    ModuleStatus,
    ProcessFacts,
    ScheduleEntry,
    SkillInfo,
    TickResponse,
    TriggerRequest,
    TriggerResponse,
)
from butlers.api.pricing import PricingConfig, estimate_session_cost
from butlers.api.read_models.butlers_v1 import query_sessions_24h
from butlers.api.routers.audit import log_audit_entry
from butlers.config import ConfigError, load_config
from butlers.core.sessions import sessions_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers"])

# Timeout (in seconds) for each individual butler status probe.
_STATUS_TIMEOUT_S = 5.0
_MCP_LIST_TOOLS_TIMEOUT_S = 15.0
_MCP_CALL_TIMEOUT_S = 30.0

# Default roster location relative to the repository root.
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[4] / "roster"


def _get_roster_dir() -> Path:
    """Return the roster directory path. Override in tests."""
    return _DEFAULT_ROSTER_DIR


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


_STALE_CONNECTION_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError)


async def _probe_butler(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    sessions_24h: int = 0,
) -> ButlerSummary:
    """Probe a single butler's MCP server and return a summary.

    Attempts to connect via the MCP client and call ``ping()``.  If the
    butler responds, ``status`` is ``"ok"``.  Any failure (connection
    refused, timeout, unexpected error) results in ``status: "down"``.

    On stale-connection errors (``ClosedResourceError``, ``BrokenResourceError``)
    the cached client is evicted and a single retry is attempted with a fresh
    connection before reporting the butler as down.
    """
    for attempt in range(2):
        try:
            client = await asyncio.wait_for(
                mgr.get_client(info.name),
                timeout=_STATUS_TIMEOUT_S,
            )
            await asyncio.wait_for(client.ping(), timeout=_STATUS_TIMEOUT_S)
            status = "ok"
            break
        except ButlerUnreachableError:
            logger.debug("Butler %s is unreachable", info.name)
            status = "down"
            break
        except _STALE_CONNECTION_ERRORS:
            await mgr.invalidate_client(info.name)
            if attempt == 0:
                logger.debug("Butler %s: stale connection, retrying with fresh client", info.name)
                continue
            logger.debug("Butler %s is unreachable after reconnect attempt", info.name)
            status = "down"
        except TimeoutError:
            logger.debug("Butler %s timed out", info.name)
            status = "down"
            break
        except Exception:
            logger.warning("Unexpected error probing butler %s", info.name, exc_info=True)
            status = "down"
            break

    return ButlerSummary(
        name=info.name,
        status=status,
        port=info.port,
        type=info.type,
        description=info.description,
        sessions_24h=sessions_24h,
    )


async def _fetch_sessions_24h(
    db: DatabaseManager,
    butler_names: list[str] | None = None,
) -> dict[str, int]:
    """Return a mapping of butler_name -> session count for the last 24 hours.

    Delegates to :func:`~butlers.api.read_models.butlers_v1.query_sessions_24h`
    from the versioned read-model boundary.  This call is best-effort: any DB
    or query failure returns an empty mapping so the list endpoint stays
    available when the DB is unhealthy.

    Args:
        db: The database manager.
        butler_names: Subset of butler names to query.  Defaults to all
            registered butlers if omitted.
    """
    return await query_sessions_24h(db, butler_names, timeout_s=_STATUS_TIMEOUT_S)


@router.get("", response_model=ApiResponse[list[ButlerSummary]])
async def list_butlers(
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerSummary]]:
    """Return all discovered butlers with live status and 24h session counts."""
    config_names = [info.name for info in configs]
    sessions_by_butler = await _fetch_sessions_24h(db, butler_names=config_names)
    tasks = [
        _probe_butler(mgr, info, sessions_24h=sessions_by_butler.get(info.name, 0))
        for info in configs
    ]
    summaries = await asyncio.gather(*tasks)
    return ApiResponse[list[ButlerSummary]](data=list(summaries))


async def _fetch_last_session_started_at(
    db: DatabaseManager,
    butler_name: str,
) -> datetime | None:
    """Return the MAX ``started_at`` timestamp from the butler's ``sessions`` table.

    Uses the butler-scoped pool so no ``butler_name`` column filter is needed.
    Returns ``None`` when the sessions table does not exist, the butler has no
    sessions, or the DB is unavailable.
    """
    query = (
        "SELECT CASE WHEN to_regclass('sessions') IS NOT NULL"
        " THEN (SELECT MAX(started_at) FROM sessions)"
        " ELSE NULL END"
    )
    try:
        pool = db.pool(butler_name)
        value = await asyncio.wait_for(
            pool.fetchval(query),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        logger.debug(
            "Failed to fetch last_session_started_at for butler %s", butler_name, exc_info=True
        )
        return None
    return value


# ---------------------------------------------------------------------------
# Process facts helpers
# ---------------------------------------------------------------------------


async def _fetch_registered_duration(
    db: DatabaseManager,
    butler_name: str,
) -> float | None:
    """Return seconds elapsed since the butler first registered in the switchboard.

    Queries ``switchboard.butler_registry.registered_at`` and returns the age
    in seconds relative to now.  Returns ``None`` when the switchboard pool is
    unavailable or the butler has no registry row.
    """
    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        return None

    try:
        row = await asyncio.wait_for(
            sw_pool.fetchrow(
                "SELECT registered_at FROM butler_registry WHERE name = $1",
                butler_name,
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        logger.debug("Failed to fetch registered_at for butler %s", butler_name, exc_info=True)
        return None

    if row is None or row["registered_at"] is None:
        return None

    registered_at = row["registered_at"]
    # Normalize to UTC-aware datetime
    if hasattr(registered_at, "tzinfo") and registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=UTC)

    elapsed = (datetime.now(UTC) - registered_at).total_seconds()
    return max(elapsed, 0.0)


def _build_process_facts(
    connection_info: ButlerConnectionInfo,
    roster_dir: Path,
    registered_duration_seconds: float | None,
) -> ProcessFacts:
    """Assemble process facts from stable topology sources.

    - ``container_name``: derived from the ``BUTLERS_HOST`` env var (the MCP
      host the dashboard uses to reach butler daemons). Absent when unset or
      resolves to ``localhost``.
    - ``port``: from ``ButlerConnectionInfo.port``.
    - ``registered_duration_seconds``: seconds since switchboard registration.
    - ``config_path``: roster-relative path, e.g. ``roster/general/butler.toml``.
    """
    host = os.environ.get("BUTLERS_HOST", "localhost")
    container_name: str | None = host if host and host != "localhost" else None

    toml_path = roster_dir / connection_info.name / "butler.toml"
    config_path = str(toml_path.relative_to(roster_dir.parent))

    return ProcessFacts(
        container_name=container_name,
        port=connection_info.port,
        registered_duration_seconds=registered_duration_seconds,
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# Board endpoint: GET /api/butlers/board  (bu-86c4c.17)
# ---------------------------------------------------------------------------
#
# Canonical liveness model
# ------------------------
# This endpoint is the SINGLE source of the "activity" / "cell_tone" verdict
# for every butler-status surface (the roster board, the /system topology
# graph, and the /system heartbeat list). Rules applied in order (first
# match wins):
#
#   1. status == "down"                        -> "offline"     / red
#   2. eligibility == "quarantined"             -> "quarantined" / red
#   3. heartbeat data unavailable for this row  -> "unknown"     / neutral
#   4. active_session_count > 0                 -> "running"     / green
#   5. cadence_status == "overdue" (silent longer than the butler's own
#      cron expectation, or > 5 min when no cadence is known)
#                                                -> "overdue"     / amber
#   6. else                                     -> "idle"        / neutral
#
# Cadence expectations come from each butler's own enabled cron schedules
# (its ``scheduled_tasks`` table), so "IDLE" no longer means the same thing
# for an hourly butler and a weekly one: a butler silent 3x its own cadence
# is "overdue", a flat idle/running binary can't express that fact.
#
# Consolidation
# -------------
# Replaces the frontend's former ~2N-query fan-out (one runtime-config
# request and one hourly-activity request per butler, from useButlerStatusBoard)
# with a single request: this endpoint fans out to the same sources
# server-side and returns rows + aggregates in one round trip. Row order is
# the roster's stable discovery order (``get_butler_configs()``), never
# re-sorted by a live counter, so board rows never shuffle position between
# polls.

_BOARD_HOURLY_ACTIVITY_SQL = """
WITH hours AS (
  SELECT generate_series(
    DATE_TRUNC('hour', NOW()) - (($1 - 1) * INTERVAL '1 hour'),
    DATE_TRUNC('hour', NOW()),
    '1 hour'
  ) AS hour_start
)
SELECT
  h.hour_start,
  COUNT(s.id) AS sessions_count
FROM hours h
LEFT JOIN sessions s ON s.started_at >= h.hour_start
                    AND s.started_at < h.hour_start + INTERVAL '1 hour'
GROUP BY 1
ORDER BY 1 DESC
"""

# A butler is "overdue" only once its silence exceeds its own cadence by this
# factor -- avoids flagging a butler that simply hasn't hit its next
# scheduled run yet.
_CADENCE_OVERDUE_FACTOR = 2.0
# Fallback staleness threshold when a butler has no enabled cron schedule at
# all (matches the pre-existing ButlerHeartbeatTile threshold).
_DEFAULT_STALE_SECONDS = 5 * 60


class BoardRow(BaseModel):
    """One butler's row on the consolidated fleet status board."""

    name: str
    type: str  # "butler" | "staffer"
    description: str | None
    status: str  # "ok" | "down" (raw MCP probe result)

    # --- canonical liveness (see module docstring above) ---
    activity: str  # "running" | "idle" | "overdue" | "offline" | "quarantined" | "unknown"
    cell_tone: str  # "green" | "amber" | "red" | "neutral"

    eligibility: str  # "active" | "quarantined" | "stale" | "unavailable"
    quarantine_reason: str | None
    quarantined_at: str | None

    sessions_24h: int
    cost_today: float | None
    load_pct: int | None
    max_concurrent: int | None
    # 0 whenever heartbeat_unavailable is true (unreliable), matching load_pct's
    # own degradation rule -- never a confident "0 active" during an outage.
    active_session_count: int

    last_session_at: str | None
    last_heartbeat_at: str | None
    heartbeat_age_seconds: float | None
    heartbeat_unavailable: bool
    schema_unreachable: bool

    hourly_stripe: list[int]
    hourly_total: int
    # True when the hourly-activity query failed -- hourly_stripe/hourly_total
    # above are a fabricated [0]*24/0 in that case, never a truthful empty.
    stripe_source_error: bool

    # --- cron-expectation join ---
    cadence_seconds: float | None
    cadence_label: str | None  # "hourly" | "daily" | "weekly" | "custom" | None (no schedule)
    silence_seconds: float | None
    cadence_status: str  # "on_schedule" | "overdue" | "unknown"


class BoardAggregates(BaseModel):
    """Fleet-wide aggregates for the board header/footer."""

    total: int
    butler_count: int
    staffer_count: int
    active: int
    offline: int
    quarantined: int
    overdue: int
    total_sessions_24h: int
    # Partial sum over rows with known cost when cost_source_error is True --
    # NEVER render bare as a confident total in that case (repo degraded-mode
    # doctrine); show "unavailable"/"partial" per cost_source_error instead.
    total_spend_today: float
    avg_load_pct: int | None

    heartbeat_source_error: bool
    registry_source_error: bool
    cost_source_error: bool
    # True when any row's hourly-activity ("stripe") query failed, meaning
    # total_sessions_24h below is a partial sum, never a confident total.
    sessions_source_error: bool
    has_per_entry_errors: bool
    sources_partially_degraded: bool


class BoardResponse(BaseModel):
    """Response envelope for GET /api/butlers/board."""

    rows: list[BoardRow]
    aggregates: BoardAggregates
    generated_at: str


def _board_as_utc(value: datetime | None) -> datetime | None:
    """Normalize a possibly-naive asyncpg timestamp to a UTC-aware datetime."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _board_parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string back to a datetime, tolerating None/invalid input."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cron_cadence_seconds(crons: list[str], *, now: datetime) -> float | None:
    """Return the shortest interval (seconds) between occurrences across *crons*.

    Returns None when no cron expression is valid/parseable.
    """
    best: float | None = None
    for cron in crons:
        try:
            it = croniter(cron, now)
            first = it.get_next(datetime)
            second = it.get_next(datetime)
        except Exception:
            continue
        interval = (second - first).total_seconds()
        if interval > 0 and (best is None or interval < best):
            best = interval
    return best


def _cadence_label(cadence_seconds: float | None) -> str | None:
    """Bucket a cadence interval into a human-facing label."""
    if cadence_seconds is None:
        return None
    if cadence_seconds <= 3600 * 2:
        return "hourly"
    if cadence_seconds <= 3600 * 24 * 1.5:
        return "daily"
    if cadence_seconds <= 3600 * 24 * 9:
        return "weekly"
    return "custom"


async def _fetch_enabled_crons(pool: object) -> list[str]:
    """Return enabled cron expressions from the butler's own scheduled_tasks table.

    Tolerates schemas without the temporal-intelligence ``task_type`` column
    (pre-migration) and missing/unreachable ``scheduled_tasks`` tables --
    both degrade to an empty list (cadence_status becomes "unknown"), never
    an error.
    """
    try:
        rows = await asyncio.wait_for(
            pool.fetch(
                "SELECT cron FROM scheduled_tasks"
                " WHERE enabled = true AND (task_type IS NULL OR task_type = 'cron')"
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        try:
            rows = await asyncio.wait_for(
                pool.fetch("SELECT cron FROM scheduled_tasks WHERE enabled = true"),
                timeout=_STATUS_TIMEOUT_S,
            )
        except Exception:
            return []
    return [r["cron"] for r in rows if r["cron"]]


async def _fetch_board_hourly_stripe(pool: object) -> tuple[list[int], int, bool]:
    """Return a 24-slot oldest-first session-count stripe, its total, and an error flag.

    Mirrors GET /api/butlers/{name}/analytics/hourly-activity so the board's
    stripe and SESS·24H figure always match that endpoint's numbers.

    A query failure returns ``([0] * 24, 0, True)`` -- the third element must
    be surfaced as ``stripe_source_error`` so that all-zero stripe is never
    read as a truthful "no activity" (degraded-mode doctrine, CLAUDE.md).
    """
    try:
        rows = await asyncio.wait_for(
            pool.fetch(_BOARD_HOURLY_ACTIVITY_SQL, 24),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        return [0] * 24, 0, True

    # SQL orders newest-first (index 0 = current hour); convert to
    # oldest-first (slot 0 = oldest) for the stripe.
    stripe = [0] * 24
    for idx, row in enumerate(rows):
        slot = 23 - idx
        if 0 <= slot < 24:
            stripe[slot] = int(row["sessions_count"])
    return stripe, sum(stripe), False


async def _fetch_board_max_concurrent(pool: object) -> int | None:
    """Return max_concurrent from the butler's runtime_config row, if any."""
    try:
        row = await asyncio.wait_for(
            pool.fetchrow("SELECT max_concurrent FROM runtime_config LIMIT 1"),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        return None
    if row is None:
        return None
    value = row["max_concurrent"]
    return int(value) if value else None


async def _fetch_board_cost_today(pool: object, pricing: PricingConfig) -> float | None:
    """Return today's estimated cost for one butler, or None on any failure.

    The whole body (not just the query) is inside the try/except: a
    malformed/unexpected ``sessions_summary`` shape must degrade this one
    butler's cost to None (surfaced via cost_source_error), never crash the
    entire board endpoint for every other butler.
    """
    try:
        data = await asyncio.wait_for(sessions_summary(pool, "today"), timeout=_STATUS_TIMEOUT_S)
        total_cost = 0.0
        for model_id, stats in data.get("by_model", {}).items():
            total_cost += estimate_session_cost(
                pricing,
                model_id,
                stats.get("input_tokens", 0),
                stats.get("output_tokens", 0),
                cached_input_tokens=stats.get("cached_input_tokens", 0),
                context_tokens=stats.get("context_tokens"),
            )
        return total_cost
    except Exception:
        return None


def _derive_board_activity(
    *,
    status: str,
    eligibility: str,
    heartbeat_unavailable: bool,
    active_session_count: int,
    cadence_status: str,
) -> tuple[str, str]:
    """Canonical liveness derivation -- see module docstring above."""
    if status == "down":
        return "offline", "red"
    if eligibility == "quarantined":
        return "quarantined", "red"
    if heartbeat_unavailable:
        return "unknown", "neutral"
    if active_session_count > 0:
        return "running", "green"
    if cadence_status == "overdue":
        return "overdue", "amber"
    return "idle", "neutral"


async def _fetch_board_row(
    info: ButlerConnectionInfo,
    *,
    mgr: MCPClientManager,
    db: DatabaseManager,
    pricing: PricingConfig,
    registry_map: dict[str, dict],
    registry_source_error: bool,
    sessions_24h: int,
    now: datetime,
) -> BoardRow:
    """Assemble one board row: MCP status, registry, session facts, cadence."""
    summary = await _probe_butler(mgr, info, sessions_24h=sessions_24h)

    reg = registry_map.get(info.name)
    if registry_source_error or reg is None:
        eligibility = "unavailable"
    else:
        raw_eligibility = reg["eligibility_state"]
        eligibility = (
            raw_eligibility
            if raw_eligibility in ("active", "quarantined", "stale")
            else "unavailable"
        )

    quarantine_reason = reg["quarantine_reason"] if reg else None
    quarantined_dt = (
        _board_as_utc(reg["quarantined_at"]) if reg and reg.get("quarantined_at") else None
    )
    quarantined_at = quarantined_dt.isoformat() if quarantined_dt else None

    last_heartbeat_dt = _board_as_utc(reg["last_seen_at"]) if reg else None
    last_heartbeat_at = last_heartbeat_dt.isoformat() if last_heartbeat_dt else None
    heartbeat_age = (now - last_heartbeat_dt).total_seconds() if last_heartbeat_dt else None

    schema_unreachable = False
    last_session_at: str | None = None
    active_session_count = 0
    pool = None
    try:
        pool = db.pool(info.name)
    except KeyError:
        schema_unreachable = True

    if pool is not None:
        try:
            last_row = await asyncio.wait_for(
                pool.fetchrow(
                    "SELECT completed_at FROM sessions"
                    " WHERE completed_at IS NOT NULL"
                    " ORDER BY completed_at DESC LIMIT 1"
                ),
                timeout=_STATUS_TIMEOUT_S,
            )
            if last_row and last_row["completed_at"] is not None:
                last_session_dt = _board_as_utc(last_row["completed_at"])
                last_session_at = last_session_dt.isoformat() if last_session_dt else None
            active_count_row = await asyncio.wait_for(
                pool.fetchval("SELECT count(*) FROM sessions WHERE completed_at IS NULL"),
                timeout=_STATUS_TIMEOUT_S,
            )
            active_session_count = int(active_count_row or 0)
        except Exception as exc:
            logger.warning("Board: session query failed for butler %s: %s", info.name, exc)
            schema_unreachable = True

    heartbeat_unavailable = registry_source_error or schema_unreachable

    hourly_stripe, hourly_total, stripe_source_error = (
        await _fetch_board_hourly_stripe(pool) if pool is not None else ([0] * 24, 0, True)
    )
    max_concurrent = await _fetch_board_max_concurrent(pool) if pool is not None else None
    cost_today = await _fetch_board_cost_today(pool, pricing) if pool is not None else None
    crons = await _fetch_enabled_crons(pool) if pool is not None else []

    cadence_seconds = _cron_cadence_seconds(crons, now=now)
    cadence_label = _cadence_label(cadence_seconds)

    last_activity_candidates = [
        dt
        for dt in (_board_parse_iso(last_session_at), _board_parse_iso(last_heartbeat_at))
        if dt is not None
    ]
    last_activity = max(last_activity_candidates) if last_activity_candidates else None
    silence_seconds = (now - last_activity).total_seconds() if last_activity else None

    if silence_seconds is None:
        cadence_status = "unknown"
    elif cadence_seconds is not None:
        cadence_status = (
            "overdue"
            if silence_seconds > cadence_seconds * _CADENCE_OVERDUE_FACTOR
            else "on_schedule"
        )
    else:
        cadence_status = "overdue" if silence_seconds > _DEFAULT_STALE_SECONDS else "on_schedule"

    # active_session_count is only trustworthy when heartbeat/session data is
    # reachable -- otherwise LOAD must show unknown, not a confident 0%.
    active_effective = 0 if heartbeat_unavailable else active_session_count
    load_pct = (
        None
        if heartbeat_unavailable or not max_concurrent
        else round((active_effective / max_concurrent) * 100)
    )

    activity, cell_tone = _derive_board_activity(
        status=summary.status,
        eligibility=eligibility,
        heartbeat_unavailable=heartbeat_unavailable,
        active_session_count=active_effective,
        cadence_status=cadence_status,
    )

    return BoardRow(
        name=info.name,
        type=info.type,
        description=info.description,
        status=summary.status,
        activity=activity,
        cell_tone=cell_tone,
        eligibility=eligibility,
        quarantine_reason=quarantine_reason,
        quarantined_at=quarantined_at,
        sessions_24h=summary.sessions_24h,
        cost_today=cost_today,
        load_pct=load_pct,
        max_concurrent=max_concurrent,
        active_session_count=active_effective,
        last_session_at=last_session_at,
        last_heartbeat_at=last_heartbeat_at,
        heartbeat_age_seconds=heartbeat_age,
        heartbeat_unavailable=heartbeat_unavailable,
        schema_unreachable=schema_unreachable,
        hourly_stripe=hourly_stripe,
        hourly_total=hourly_total,
        stripe_source_error=stripe_source_error,
        cadence_seconds=cadence_seconds,
        cadence_label=cadence_label,
        silence_seconds=silence_seconds,
        cadence_status=cadence_status,
    )


def _compute_board_aggregates(
    rows: list[BoardRow], *, registry_source_error: bool
) -> BoardAggregates:
    """Fold board rows into fleet-wide aggregates, honoring the degraded-mode doctrine."""
    total = len(rows)
    butler_count = sum(1 for r in rows if r.type == "butler")
    staffer_count = sum(1 for r in rows if r.type == "staffer")
    active = sum(1 for r in rows if r.activity == "running")
    offline = sum(1 for r in rows if r.activity == "offline")
    quarantined = sum(1 for r in rows if r.activity == "quarantined")
    overdue = sum(1 for r in rows if r.activity == "overdue")
    total_sessions_24h = sum(r.hourly_total for r in rows)

    known_costs = [r.cost_today for r in rows if r.cost_today is not None]
    cost_source_error = any(r.cost_today is None for r in rows)
    total_spend_today = sum(known_costs)

    known_loads = [r.load_pct for r in rows if r.load_pct is not None]
    avg_load_pct = round(sum(known_loads) / len(known_loads)) if known_loads else None

    sessions_source_error = any(r.stripe_source_error for r in rows)

    has_per_entry_errors = any(r.schema_unreachable for r in rows)
    sources_partially_degraded = (
        registry_source_error or cost_source_error or sessions_source_error or has_per_entry_errors
    )

    return BoardAggregates(
        total=total,
        butler_count=butler_count,
        staffer_count=staffer_count,
        active=active,
        offline=offline,
        quarantined=quarantined,
        overdue=overdue,
        total_sessions_24h=total_sessions_24h,
        total_spend_today=total_spend_today,
        avg_load_pct=avg_load_pct,
        heartbeat_source_error=registry_source_error,
        registry_source_error=registry_source_error,
        cost_source_error=cost_source_error,
        sessions_source_error=sessions_source_error,
        has_per_entry_errors=has_per_entry_errors,
        sources_partially_degraded=sources_partially_degraded,
    )


@router.get("/board", response_model=ApiResponse[BoardResponse])
async def get_butlers_board(
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[BoardResponse]:
    """Return the consolidated fleet status board in one round trip.

    See the module comment above this section for the canonical liveness
    model and consolidation rationale. Row order is the roster's stable
    discovery order -- rows never reshuffle between polls.
    """
    now = datetime.now(UTC)

    registry_map: dict[str, dict] = {}
    registry_source_error = False
    try:
        sw_pool = db.pool("switchboard")
        registry_rows = await asyncio.wait_for(
            sw_pool.fetch(
                "SELECT name, last_seen_at, eligibility_state, quarantined_at, quarantine_reason"
                " FROM butler_registry"
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
        for row in registry_rows:
            registry_map[row["name"]] = dict(row)
    except Exception as exc:
        logger.warning("Board: registry query failed: %s", exc)
        registry_source_error = True

    config_names = [info.name for info in configs]
    sessions_by_butler = await _fetch_sessions_24h(db, butler_names=config_names)

    rows = list(
        await asyncio.gather(
            *[
                _fetch_board_row(
                    info,
                    mgr=mgr,
                    db=db,
                    pricing=pricing,
                    registry_map=registry_map,
                    registry_source_error=registry_source_error,
                    sessions_24h=sessions_by_butler.get(info.name, 0),
                    now=now,
                )
                for info in configs
            ]
        )
    )

    aggregates = _compute_board_aggregates(rows, registry_source_error=registry_source_error)

    return ApiResponse[BoardResponse](
        data=BoardResponse(rows=rows, aggregates=aggregates, generated_at=now.isoformat())
    )


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


@router.get("/{name}", response_model=ApiResponse[ButlerDetail])
async def get_butler_detail(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ButlerDetail]:
    """Return detailed information for a single butler.

    Looks up the butler by name in the roster directory, parses its config,
    discovers skills, and attempts to get live status via MCP.
    """
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    try:
        config = load_config(butler_dir)
    except ConfigError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    modules = [
        ModuleInfo(name=mod_name, enabled=True, config=mod_cfg or None)
        for mod_name, mod_cfg in config.modules.items()
    ]

    schedules = [ScheduleEntry(name=s.name, cron=s.cron, prompt=s.prompt) for s in config.schedules]

    skills = _discover_skills(butler_dir)
    status = await _get_live_status(name, mcp_manager)
    sessions_map = await _fetch_sessions_24h(db, butler_names=[name])
    last_session_started_at = await _fetch_last_session_started_at(db, name)
    registered_duration = await _fetch_registered_duration(db, name)
    process_facts = _build_process_facts(connection_info, roster_dir, registered_duration)

    detail = ButlerDetail(
        name=config.name,
        port=config.port,
        type=config.type.value,
        status=status,
        description=config.description,
        db_name=config.db_name,
        db_schema=config.db_schema,
        modules=modules,
        schedules=schedules,
        skills=skills,
        sessions_24h=sessions_map.get(name, 0),
        last_session_started_at=last_session_started_at,
        process_facts=process_facts,
    )

    return ApiResponse[ButlerDetail](data=detail)


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


def _read_optional_text(path: Path) -> str | None:
    """Read a text file and return its contents, or None if the file does not exist."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _extract_mcp_result_text(result: object) -> str | None:
    """Extract text content from an MCP tool result."""
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return None

    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            text_parts.append(text)

    if not text_parts:
        return None
    return "\n".join(text_parts)


def _parse_mcp_result_payload(raw_text: str | None) -> object:
    """Parse MCP text payload as JSON when possible, else return plain text."""
    if raw_text is None:
        return None
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text


def _normalize_tool_info(tool: object) -> MCPToolInfo | None:
    """Normalize FastMCP tool metadata from dict- or object-shaped records."""
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("inputSchema", tool.get("input_schema"))
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        input_schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", None))

    if not isinstance(name, str) or not name:
        return None
    if description is not None and not isinstance(description, str):
        description = str(description)
    if input_schema is not None and not isinstance(input_schema, dict):
        input_schema = None

    return MCPToolInfo(name=name, description=description, input_schema=input_schema)


@router.get("/{name}/config", response_model=ApiResponse[ButlerConfigResponse])
async def get_butler_config(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[ButlerConfigResponse]:
    """Return the butler's configuration files as a structured response.

    Reads ``butler.toml`` and parses it as a dict.  Also reads the markdown
    config files (``CLAUDE.md``, ``AGENTS.md``, ``MANIFESTO.md``) as raw text.
    Missing markdown files are returned as ``null``.
    """
    # Validate butler exists in discovered configs
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    toml_path = butler_dir / "butler.toml"

    if not toml_path.is_file():
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        butler_toml = tomllib.loads(toml_path.read_bytes().decode())
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.error("Failed to read butler.toml for %s: %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to read config for butler: {name}")

    config_response = ButlerConfigResponse(
        butler_toml=butler_toml,
        claude_md=_read_optional_text(butler_dir / "CLAUDE.md"),
        agents_md=_read_optional_text(butler_dir / "AGENTS.md"),
        manifesto_md=_read_optional_text(butler_dir / "MANIFESTO.md"),
    )

    return ApiResponse[ButlerConfigResponse](data=config_response)


def _discover_skills(butler_dir: Path) -> list[str]:
    """List skill names from the butler's .agents/skills/ directory."""
    skills_dir = butler_dir / ".agents" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            skills.append(entry.name)

    return skills


async def _get_live_status(name: str, mcp_manager: MCPClientManager) -> str:
    """Attempt to determine a butler's live status via MCP ping.

    Retries once on stale-connection errors (evicts the cached client first).
    """
    for attempt in range(2):
        try:
            client = await mcp_manager.get_client(name)
            await client.ping()
            return "online"
        except _STALE_CONNECTION_ERRORS:
            await mcp_manager.invalidate_client(name)
            if attempt == 0:
                continue
            return "offline"
        except ButlerUnreachableError:
            return "offline"
        except Exception:
            logger.warning("Unexpected error pinging butler %s", name, exc_info=True)
            return "offline"
    return "offline"


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------


def _read_skills(butler_dir: Path) -> list[SkillInfo]:
    """Read skill names and SKILL.md content from the butler's .agents/skills/ directory."""
    skills_dir = butler_dir / ".agents" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[SkillInfo] = []
    for entry in sorted(skills_dir.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.is_dir() and skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
            skills.append(SkillInfo(name=entry.name, content=content))

    return skills


@router.get("/{name}/skills", response_model=ApiResponse[list[SkillInfo]])
async def list_butler_skills(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[list[SkillInfo]]:
    """Return skills for a single butler with name and SKILL.md content."""
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    skills = _read_skills(butler_dir)

    return ApiResponse[list[SkillInfo]](data=skills)


# ---------------------------------------------------------------------------
# MCP debug endpoints
# ---------------------------------------------------------------------------


@router.get("/{name}/mcp/tools", response_model=ApiResponse[list[MCPToolInfo]])
async def list_butler_mcp_tools(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[list[MCPToolInfo]]:
    """Return MCP tools exposed by a single butler."""
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_LIST_TOOLS_TIMEOUT_S,
        )
        raw_tools = await asyncio.wait_for(
            client.list_tools(),
            timeout=_MCP_LIST_TOOLS_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(status_code=503, detail=f"Butler '{name}' is unreachable")
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"MCP tool listing for butler '{name}' timed out",
        )
    except Exception as exc:
        logger.warning("Unexpected MCP list_tools failure for %s", name, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list MCP tools for butler '{name}': {exc}",
        )

    if not isinstance(raw_tools, list):
        logger.warning(
            "Unexpected list_tools payload type for butler %s: %s",
            name,
            type(raw_tools).__name__,
        )
        raw_tools = []

    tools: list[MCPToolInfo] = []
    for raw in raw_tools:
        normalized = _normalize_tool_info(raw)
        if normalized is not None:
            tools.append(normalized)

    return ApiResponse[list[MCPToolInfo]](data=tools)


@router.post("/{name}/mcp/call", response_model=ApiResponse[MCPToolCallResponse])
async def call_butler_mcp_tool(
    name: str,
    request: MCPToolCallRequest,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[MCPToolCallResponse]:
    """Invoke an MCP tool on a butler for debugging."""
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    tool_name = request.tool_name.strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name must not be empty")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool(tool_name, request.arguments),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(status_code=503, detail=f"Butler '{name}' is unreachable")
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"MCP tool call '{tool_name}' to butler '{name}' timed out",
        )
    except Exception as exc:
        logger.warning(
            "Unexpected MCP tool call failure for %s.%s",
            name,
            tool_name,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"MCP tool call '{tool_name}' failed for butler '{name}': {exc}",
        )

    raw_text = _extract_mcp_result_text(result)
    parsed_result = _parse_mcp_result_payload(raw_text)
    is_error = bool(getattr(result, "is_error", False))
    response = MCPToolCallResponse(
        tool_name=tool_name,
        arguments=request.arguments,
        result=parsed_result,
        raw_text=raw_text,
        is_error=is_error,
    )
    return ApiResponse[MCPToolCallResponse](data=response)


# ---------------------------------------------------------------------------
# Module health endpoint
# ---------------------------------------------------------------------------


async def _get_module_health_via_mcp(
    name: str,
    mcp_manager: MCPClientManager,
    module_names: list[str],
) -> list[ModuleStatus]:
    """Call the butler's MCP ``status()`` tool and extract per-module health.

    Expects the current status payload shape:
    ``{"modules": {"mod": {"status": ...}}}``.

    New optional OAuth/credential fields are populated when the butler's
    status() tool emits them; older butlers that don't yet emit these fields
    will return None for all three (forward-compatible graceful degradation):

    - ``oauth_status``: ``"granted"`` | ``"reauth_needed"`` | ``"not_configured"``
    - ``oauth_expires_at``: ISO-8601 datetime string (parsed to datetime)
    - ``credential_health``: ``"ok"`` | ``"warning"`` | ``"error"``
    """
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("status", {}),
            timeout=_STATUS_TIMEOUT_S,
        )

        status_data: dict = {}
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                status_data = json.loads(text)

        raw_modules = status_data.get("modules", {})
        if not isinstance(raw_modules, dict):
            logger.warning(
                "Unexpected module status payload for butler %s: expected object, got %s",
                name,
                type(raw_modules).__name__,
            )
            raw_modules = {}
        butler_health = status_data.get("health", "unknown")

        modules: list[ModuleStatus] = []
        for mod_name in module_names:
            mod_info = raw_modules.get(mod_name)
            if mod_info is None:
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status="error",
                        error="Module configured but not loaded by butler",
                    )
                )
                continue

            daemon_status = (
                mod_info.get("status", "unknown") if isinstance(mod_info, dict) else "unknown"
            )

            # Extract OAuth/credential fields when present; default to None for
            # butlers that haven't implemented these fields yet.  Unknown values
            # are silently coerced to None so future butler versions with new
            # enum variants don't break the dashboard for existing deployments.
            _VALID_OAUTH_STATUS = {"granted", "reauth_needed", "not_configured"}
            _VALID_CREDENTIAL_HEALTH = {"ok", "warning", "error"}

            oauth_status_raw = mod_info.get("oauth_status") if isinstance(mod_info, dict) else None
            if oauth_status_raw not in _VALID_OAUTH_STATUS:
                if oauth_status_raw is not None:
                    logger.warning(
                        "Unknown oauth_status %r for butler %s module %s; ignoring",
                        oauth_status_raw,
                        name,
                        mod_name,
                    )
                oauth_status = None
            else:
                oauth_status = oauth_status_raw

            oauth_expires_at_raw = (
                mod_info.get("oauth_expires_at") if isinstance(mod_info, dict) else None
            )

            credential_health_raw = (
                mod_info.get("credential_health") if isinstance(mod_info, dict) else None
            )
            if credential_health_raw not in _VALID_CREDENTIAL_HEALTH:
                if credential_health_raw is not None:
                    logger.warning(
                        "Unknown credential_health %r for butler %s module %s; ignoring",
                        credential_health_raw,
                        name,
                        mod_name,
                    )
                credential_health = None
            else:
                credential_health = credential_health_raw

            oauth_expires_at = None
            if oauth_expires_at_raw:
                try:
                    oauth_expires_at = datetime.fromisoformat(oauth_expires_at_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid oauth_expires_at value for butler %s module %s: %r",
                        name,
                        mod_name,
                        oauth_expires_at_raw,
                    )

            if daemon_status == "active":
                if butler_health == "ok":
                    mod_status = "connected"
                elif butler_health == "degraded":
                    mod_status = "degraded"
                else:
                    mod_status = "unknown"
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status=mod_status,
                        oauth_status=oauth_status,
                        oauth_expires_at=oauth_expires_at,
                        credential_health=credential_health,
                    )
                )
            else:
                # failed or cascade_failed
                error_msg = mod_info.get("error") if isinstance(mod_info, dict) else None
                phase = mod_info.get("phase") if isinstance(mod_info, dict) else None
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status="error",
                        phase=phase,
                        error=error_msg or f"Module {daemon_status}",
                        oauth_status=oauth_status,
                        oauth_expires_at=oauth_expires_at,
                        credential_health=credential_health,
                    )
                )

        return modules

    except (ButlerUnreachableError, TimeoutError):
        return [
            ModuleStatus(name=mod_name, enabled=True, status="unknown") for mod_name in module_names
        ]
    except Exception:
        logger.warning(
            "Unexpected error fetching module health for butler %s",
            name,
            exc_info=True,
        )
        return [
            ModuleStatus(name=mod_name, enabled=True, status="unknown") for mod_name in module_names
        ]


@router.get("/{name}/modules", response_model=ApiResponse[list[ModuleStatus]])
async def get_butler_modules(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[list[ModuleStatus]]:
    """Return module list with health status for a single butler."""
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    try:
        config = load_config(butler_dir)
    except ConfigError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    module_names = list(config.modules.keys())

    if not module_names:
        return ApiResponse[list[ModuleStatus]](data=[])

    module_statuses = await _get_module_health_via_mcp(name, mcp_manager, module_names)

    return ApiResponse[list[ModuleStatus]](data=module_statuses)


# ---------------------------------------------------------------------------
# Trigger endpoint
# ---------------------------------------------------------------------------

# Timeout (in seconds) for the trigger call to the butler's MCP server.
_TRIGGER_TIMEOUT_S = 120.0


@router.post("/{name}/trigger", response_model=ApiResponse[TriggerResponse])
async def trigger_butler(
    name: str,
    request: TriggerRequest,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TriggerResponse]:
    """Trigger a runtime session on the named butler with the provided prompt.

    Sends the prompt to the butler's MCP ``trigger`` tool and returns
    the session result.  Returns 503 if the butler is unreachable or
    the request times out.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    from butlers.api.routers.model_settings import _validate_complexity_tier

    _validate_complexity_tier(request.complexity)

    summary = {"prompt": request.prompt[:200], "complexity": request.complexity}
    trigger_args: dict = {"prompt": request.prompt, "complexity": request.complexity}
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_TRIGGER_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("trigger", trigger_args),
            timeout=_TRIGGER_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error="Butler unreachable"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable",
        )
    except TimeoutError:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error="Request timed out"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Trigger request to butler '{name}' timed out",
        )

    # Parse the MCP tool result
    session_id: str | None = None
    success = True
    output: str | None = None

    if result.content:
        text = result.content[0].text if hasattr(result.content[0], "text") else ""
        if text:
            try:
                data = json.loads(text)
                session_id = data.get("session_id")
                success = data.get("success", True)
                output = data.get("output")
            except (json.JSONDecodeError, AttributeError):
                output = text

    if hasattr(result, "is_error") and result.is_error:
        success = False

    trigger_response = TriggerResponse(
        session_id=session_id,
        success=success,
        output=output,
    )

    if success:
        await log_audit_entry(db, name, "trigger", summary)
    else:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error=output or "Trigger failed"
        )

    return ApiResponse[TriggerResponse](data=trigger_response)


# ---------------------------------------------------------------------------
# Tick endpoint
# ---------------------------------------------------------------------------


@router.post("/{name}/tick", response_model=ApiResponse[TickResponse])
async def force_butler_tick(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TickResponse] | JSONResponse:
    """Force a scheduler tick on the specified butler.

    Connects to the butler's MCP server and calls the tick tool,
    which triggers the scheduler to run immediately.  Returns 503 if the
    butler is unreachable or the request times out.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    summary: dict = {}
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("tick", {}),
            timeout=_STATUS_TIMEOUT_S,
        )

        message: str | None = None
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                message = text

        tick_resp = TickResponse(success=True, message=message)
        await log_audit_entry(db, name, "tick", summary)
        return ApiResponse[TickResponse](data=tick_resp)

    except ButlerUnreachableError:
        logger.warning("Butler %s is unreachable for tick", name)
        await log_audit_entry(db, name, "tick", summary, result="error", error="Butler unreachable")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "butler_unreachable",
                    "message": f"Butler '{name}' is unreachable",
                    "butler": name,
                }
            },
        )
    except TimeoutError:
        logger.warning("Tick request to butler %s timed out", name)
        await log_audit_entry(db, name, "tick", summary, result="error", error="Request timed out")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "butler_timeout",
                    "message": f"Tick request to butler '{name}' timed out",
                    "butler": name,
                }
            },
        )
