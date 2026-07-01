"""Settings Console aggregator — GET /api/settings/console + WS /api/settings/stream.

Implements §7.1 of the settings-redesign OpenSpec change:

  GET  /api/settings/console
       ├── header_counts: active_butlers, spend_mtd_usd, open_approvals,
       │                  models_verified, models_total
       └── attention[]: tone, kind, text, action_route
           Ordering: red items first, then amber.
           Capped at 5 visible items; remainder counted in attention_truncated_count.
           Cache: 10s in-memory (single-actor, single-owner deployments).

  WS /api/settings/stream
       Multiplexes header_delta / attention_add / attention_remove events.
       Reconnect emits a full snapshot (event type "snapshot").
       Auth: ?api_key=<DASHBOARD_API_KEY> at handshake time (opt-in; absent when not configured).

Partial-failure mode: when a sub-system aggregation fails, the exception is
caught per-subsystem and surfaces an amber attention item instead of erroring
the whole response.

CRITICAL: This module is read-only aggregation. No mutations. No audit calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import ApiResponse
from butlers.api.pricing import PricingConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings-console"])

# ---------------------------------------------------------------------------
# In-memory cache (10-second TTL, global for single-owner deployments)
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 10.0
_cache_ts: float = 0.0
_cache_payload: dict[str, Any] | None = None
_cache_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

_ATTENTION_TONE = str  # "red" | "amber"


class AttentionItem(BaseModel):
    """A single attention item surfaced in the console strip."""

    tone: str  # "red" | "amber"
    kind: str
    text: str
    action_route: str


class HeaderCounts(BaseModel):
    """Aggregate header counts for the console overview."""

    active_butlers: int
    spend_mtd_usd: float
    open_approvals: int
    models_verified: int
    models_total: int


class ConsoleResponse(BaseModel):
    """Full response for GET /api/settings/console."""

    header_counts: HeaderCounts
    attention: list[AttentionItem]
    attention_truncated_count: int


# ---------------------------------------------------------------------------
# Per-subsystem aggregation helpers (each wrapped in try/except to isolate)
# ---------------------------------------------------------------------------

_QUERY_TIMEOUT_S = 8.0


def _get_db_manager() -> DatabaseManager | None:
    """Return the dashboard DatabaseManager if available, otherwise None."""
    from butlers.api.deps import get_db_manager

    try:
        return get_db_manager()
    except RuntimeError:
        return None


async def _count_active_butlers(
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
) -> tuple[int, AttentionItem | None]:
    """Count butlers that respond to a status ping within timeout.

    Returns (count, None) on success.  Never raises — failures return (0, amber-item).
    """
    try:
        _STATUS_TIMEOUT_S = 3.0

        async def _ping(info: ButlerConnectionInfo) -> bool:
            try:
                client = await asyncio.wait_for(
                    mgr.get_client(info.name), timeout=_STATUS_TIMEOUT_S
                )
                await asyncio.wait_for(client.call_tool("status", {}), timeout=_STATUS_TIMEOUT_S)
                return True
            except Exception:
                return False

        # Fan out pings in parallel; treat any that answer as "active"
        results = await asyncio.gather(*[_ping(info) for info in configs])
        return sum(results), None
    except Exception as exc:
        logger.warning("console: active-butlers aggregation failed: %s", exc)
        return 0, AttentionItem(
            tone="amber",
            kind="subsystem_error",
            text="Could not reach the butler roster — status may be stale.",
            action_route="/butlers",
        )


async def _get_spend_mtd(
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    pricing: PricingConfig,
    db: DatabaseManager | None,
) -> tuple[float, float | None, AttentionItem | None]:
    """Return (spend_mtd_usd, ceiling_usd, optional_attention_item).

    Ceiling is needed to generate the "near ceiling" attention item.
    Never raises.
    """
    try:
        from butlers.api.routers.spend import (
            _get_butler_session_stats,
            _get_butler_session_stats_from_db,
        )

        tasks = [
            (
                _get_butler_session_stats_from_db(db, info, pricing, "30d")
                if db is not None
                else _get_butler_session_stats(mgr, info, pricing, "30d")
            )
            for info in configs
        ]
        raw = await asyncio.gather(*tasks)

        # DB path may return None for pools without data; fallback per butler
        if db is not None:
            fallback_tasks = [
                _get_butler_session_stats(mgr, info, pricing, "30d")
                for info, result in zip(configs, raw, strict=False)
                if result is None
            ]
            fallback_results = await asyncio.gather(*fallback_tasks)
            fi = iter(fallback_results)
            results = [r if r is not None else next(fi) for r in raw]
        else:
            results = raw

        mtd = sum(r[1] for r in results if r is not None)

        # Read ceiling from DB
        ceiling_usd: float | None = None
        if db is not None:
            try:
                pool = db.pool("switchboard")
                row = await pool.fetchrow(
                    "SELECT monthly_usd FROM public.spend_ceiling WHERE id = 1"
                )
                if row:
                    ceiling_usd = float(row["monthly_usd"])
            except Exception:
                pass

        return round(mtd, 2), ceiling_usd, None
    except Exception as exc:
        logger.warning("console: spend-mtd aggregation failed: %s", exc)
        return (
            0.0,
            None,
            AttentionItem(
                tone="amber",
                kind="subsystem_error",
                text="Could not fetch spend data — totals may be unavailable.",
                action_route="/settings/spend",
            ),
        )


async def _count_open_approvals(db: DatabaseManager | None) -> tuple[int, AttentionItem | None]:
    """Count pending (open) approval actions across all pools.

    Returns (count, None) on success.  Never raises.
    """
    if db is None:
        return 0, None
    try:
        from butlers.api.routers.approvals import _find_all_approvals_pools

        pools = await asyncio.wait_for(
            _find_all_approvals_pools(db),
            timeout=_QUERY_TIMEOUT_S,
        )
        total = 0
        for pool in pools:
            try:
                count = await pool.fetchval(
                    "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
                )
                total += count or 0
            except Exception:
                pass
        return total, None
    except Exception as exc:
        logger.warning("console: open-approvals aggregation failed: %s", exc)
        return 0, AttentionItem(
            tone="amber",
            kind="subsystem_error",
            text="Could not reach the approvals subsystem.",
            action_route="/approvals",
        )


async def _count_models(db: DatabaseManager | None) -> tuple[int, int, AttentionItem | None]:
    """Return (verified_count, total_count, optional_attention_item).

    Never raises.
    """
    if db is None:
        return 0, 0, None
    try:
        pool = db.pool("switchboard")
        total = await asyncio.wait_for(
            pool.fetchval("SELECT COUNT(*) FROM public.model_catalog WHERE enabled = true"),
            timeout=_QUERY_TIMEOUT_S,
        )
        verified = await asyncio.wait_for(
            pool.fetchval(
                "SELECT COUNT(*) FROM public.model_catalog "
                "WHERE enabled = true AND last_verified_ok = true"
            ),
            timeout=_QUERY_TIMEOUT_S,
        )
        return int(verified or 0), int(total or 0), None
    except Exception as exc:
        logger.warning("console: model-count aggregation failed: %s", exc)
        return (
            0,
            0,
            AttentionItem(
                tone="amber",
                kind="subsystem_error",
                text="Could not read the model catalog.",
                action_route="/settings/models",
            ),
        )


async def _check_cli_auth(db: DatabaseManager | None) -> list[AttentionItem]:
    """Return attention items for any unauthenticated CLI auth providers.

    Never raises.
    """
    items: list[AttentionItem] = []
    try:
        from butlers.cli_auth.health import probe_all
        from butlers.cli_auth.registry import PROVIDERS

        health_results = await asyncio.wait_for(probe_all(), timeout=_QUERY_TIMEOUT_S)
        for p in PROVIDERS.values():
            if not p.is_available() and p.auth_mode != "api_key":
                continue
            health = health_results.get(p.name)
            if health is not None and health.state in ("not_authenticated", "probe_failed"):
                items.append(
                    AttentionItem(
                        tone="red",
                        kind="auth_renewal",
                        text=f"CLI runtime '{p.display_name}' needs re-authentication.",
                        action_route="/secrets?tab=runtimes",
                    )
                )
    except Exception as exc:
        logger.debug("console: cli-auth check skipped: %s", exc)
    return items


async def _check_model_errors(db: DatabaseManager | None) -> list[AttentionItem]:
    """Return attention items for models in error or rate-limited state.

    Never raises.
    """
    items: list[AttentionItem] = []
    if db is None:
        return items
    try:
        pool = db.pool("switchboard")
        # Models that failed last verification
        rows = await asyncio.wait_for(
            pool.fetch(
                "SELECT alias FROM public.model_catalog "
                "WHERE enabled = true AND last_verified_ok = false"
            ),
            timeout=_QUERY_TIMEOUT_S,
        )
        if rows:
            aliases = ", ".join(r["alias"] for r in rows[:3])
            suffix = f" (+{len(rows) - 3} more)" if len(rows) > 3 else ""
            items.append(
                AttentionItem(
                    tone="red",
                    kind="model_error",
                    text=f"Model verification failed: {aliases}{suffix}.",
                    action_route="/settings/models",
                )
            )
    except Exception as exc:
        logger.debug("console: model-error check skipped: %s", exc)
    return items


async def _check_failed_webhooks(db: DatabaseManager | None) -> list[AttentionItem]:
    """Return attention item if production webhook deliveries exhausted in the last 24h.

    Queries ``last_delivery_ok`` (set by the production dispatch path) rather
    than ``last_test_ok`` (set only by the test-fire endpoint) so the attention
    item derives from real delivery failures, not operator-initiated tests.

    Never raises.
    """
    items: list[AttentionItem] = []
    if db is None:
        return items
    try:
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
        pool = db.pool("switchboard")
        count = await asyncio.wait_for(
            pool.fetchval(
                "SELECT COUNT(*) FROM public.webhooks "
                "WHERE last_delivery_ok = false AND last_delivery_at >= $1",
                cutoff,
            ),
            timeout=_QUERY_TIMEOUT_S,
        )
        if count and count > 0:
            items.append(
                AttentionItem(
                    tone="amber",
                    kind="webhook_failure",
                    text=f"{count} webhook endpoint(s) failed in the last 24h.",
                    action_route="/settings/permissions",
                )
            )
    except Exception as exc:
        logger.debug("console: webhook-failure check skipped: %s", exc)
    return items


# ---------------------------------------------------------------------------
# Full aggregation
# ---------------------------------------------------------------------------

_ATTENTION_CAP = 5


async def _build_console_payload(
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    pricing: PricingConfig,
    db: DatabaseManager | None,
) -> dict[str, Any]:
    """Aggregate all subsystems and return the raw payload dict."""

    # Fan out independent sub-queries in parallel
    (
        (active_butlers, butler_err),
        (spend_mtd, ceiling, spend_err),
        (open_approvals, approval_err),
        (models_verified, models_total, model_count_err),
        cli_auth_items,
        model_error_items,
        failed_webhook_items,
    ) = await asyncio.gather(
        _count_active_butlers(configs, mgr),
        _get_spend_mtd(configs, mgr, pricing, db),
        _count_open_approvals(db),
        _count_models(db),
        _check_cli_auth(db),
        _check_model_errors(db),
        _check_failed_webhooks(db),
    )

    # Collect subsystem errors that failed
    subsystem_errors = [
        e for e in [butler_err, spend_err, approval_err, model_count_err] if e is not None
    ]

    # Build attention items — order: red first, then amber
    red_items: list[AttentionItem] = []
    amber_items: list[AttentionItem] = []

    # Open approvals → red
    if open_approvals > 0:
        red_items.append(
            AttentionItem(
                tone="red",
                kind="open_approvals",
                text=f"{open_approvals} approval(s) are waiting for your review.",
                action_route="/approvals",
            )
        )

    # CLI auth issues → red
    red_items.extend(cli_auth_items)

    # Model errors → red
    red_items.extend(model_error_items)

    # Spend near ceiling → amber.  Suppressed while the month-end projection is
    # low-confidence (days_elapsed < 3) so an early-month spike does not raise a
    # false alarm (dashboard-spend-dashboard §5.2 projection_confidence gate).
    from datetime import date

    from butlers.api.routers.spend import projection_confidence_for

    days_elapsed = date.today().day  # 1-based, inclusive of today
    projection_confidence = projection_confidence_for(days_elapsed)
    if ceiling is not None and ceiling > 0 and projection_confidence != "low":
        ratio = spend_mtd / ceiling
        if ratio >= 0.90:
            pct = int(ratio * 100)
            amber_items.append(
                AttentionItem(
                    tone="amber",
                    kind="spend_ceiling",
                    text=f"Monthly spend is at {pct}% of the ${ceiling:.0f} ceiling.",
                    action_route="/settings/spend",
                )
            )

    # Failed webhooks → amber
    amber_items.extend(failed_webhook_items)

    # Subsystem errors → amber
    amber_items.extend(subsystem_errors)

    all_items = red_items + amber_items
    visible = all_items[:_ATTENTION_CAP]
    truncated = max(0, len(all_items) - _ATTENTION_CAP)

    return {
        "header_counts": {
            "active_butlers": active_butlers,
            "spend_mtd_usd": spend_mtd,
            "open_approvals": open_approvals,
            "models_verified": models_verified,
            "models_total": models_total,
        },
        "attention": [item.model_dump() for item in visible],
        "attention_truncated_count": truncated,
    }


# ---------------------------------------------------------------------------
# GET /api/settings/console
# ---------------------------------------------------------------------------


@router.get("/console", response_model=ApiResponse[ConsoleResponse])
async def get_settings_console(
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[ConsoleResponse]:
    """Aggregate header counts + attention items for the Settings Console page.

    Cached for 10 seconds (in-process, single-actor deployments).
    Sub-system failures are surfaced as amber attention items rather than
    errors so one slow sub-system does NOT prevent the console from rendering.
    """
    global _cache_ts, _cache_payload

    now = time.monotonic()

    async with _cache_lock:
        if _cache_payload is not None and (now - _cache_ts) < _CACHE_TTL_S:
            return ApiResponse[ConsoleResponse](data=ConsoleResponse(**_cache_payload))

        payload = await _build_console_payload(configs, mgr, pricing, db)
        _cache_ts = now
        _cache_payload = payload

    return ApiResponse[ConsoleResponse](data=ConsoleResponse(**payload))


# ---------------------------------------------------------------------------
# WS /api/settings/stream
# ---------------------------------------------------------------------------


async def _auth_ws(websocket: WebSocket) -> bool:
    """Validate the ?api_key= param at WebSocket upgrade time.

    Returns True when auth passes (or no auth is configured).
    Closes the socket with WS code 4401 on failure (spec
    dashboard-settings-console; matches /api/approvals/stream and
    /api/spend/stream).
    """
    configured_key: str | None = os.environ.get("DASHBOARD_API_KEY") or None
    if configured_key is None:
        # Auth not configured — open access
        return True

    provided = websocket.query_params.get("api_key", "")
    if secrets.compare_digest(provided, configured_key):
        return True

    await websocket.close(code=4401)
    return False


@router.websocket("/stream")
async def settings_stream(
    websocket: WebSocket,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    pricing: PricingConfig = Depends(get_pricing),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> None:
    """WebSocket stream for the Settings Console.

    Protocol:
      - Authenticate via ?api_key=<DASHBOARD_API_KEY> at handshake time (no-op when not configured).
      - On connect: emit a full "snapshot" event.
      - Thereafter: emit "header_delta" when counts change, and
        "attention_add" / "attention_remove" when attention items change.
      - Reconnect always emits a fresh snapshot.

    Event shape:
      { "type": "snapshot", "data": <ConsoleResponse dict> }
      { "type": "header_delta", "data": { ...changed fields... } }
      { "type": "attention_add", "data": <AttentionItem dict> }
      { "type": "attention_remove", "data": { "kind": "..." } }
    """
    # Auth gate runs BEFORE accept so an auth failure closes with 4401 at the
    # upgrade (mirrors /api/approvals/stream and /api/spend/stream).
    if not await _auth_ws(websocket):
        return

    await websocket.accept()

    _POLL_INTERVAL_S = 5.0

    try:
        # Emit full snapshot on connect
        payload = await _build_console_payload(configs, mgr, pricing, db)
        await websocket.send_json({"type": "snapshot", "data": payload})

        prev_payload = payload

        while True:
            await asyncio.sleep(_POLL_INTERVAL_S)
            new_payload = await _build_console_payload(configs, mgr, pricing, db)

            # Compute header_delta
            old_counts = prev_payload["header_counts"]
            new_counts = new_payload["header_counts"]
            delta = {k: v for k, v in new_counts.items() if old_counts.get(k) != v}
            if delta:
                await websocket.send_json({"type": "header_delta", "data": delta})

            # Compute attention changes
            old_items = {item["kind"]: item for item in prev_payload["attention"]}
            new_items = {item["kind"]: item for item in new_payload["attention"]}

            for kind, item in new_items.items():
                if kind not in old_items:
                    await websocket.send_json({"type": "attention_add", "data": item})

            for kind in old_items:
                if kind not in new_items:
                    await websocket.send_json({"type": "attention_remove", "data": {"kind": kind}})

            prev_payload = new_payload

    except WebSocketDisconnect:
        logger.debug("Settings stream WebSocket disconnected")
    except Exception as exc:
        logger.warning("Settings stream error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
