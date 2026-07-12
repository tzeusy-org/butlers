"""Settings Console aggregator — GET /api/settings/console.

Implements §7.1 of the settings-redesign OpenSpec change:

  GET  /api/settings/console
       ├── header_counts: active_butlers, spend_mtd_usd, open_approvals,
       │                  models_verified, models_total
       └── attention[]: tone, kind, text, action_route
           Ordering: red items first, then amber.
           Capped at 5 visible items; remainder counted in attention_truncated_count.
           Cache: 10s in-memory (single-actor, single-owner deployments).

Settings Console deltas are fanned onto the unified fleet event bus
(``WS /api/events/stream``, ``butlers.api.routers.events.emit_event``) as
"header_delta" / "attention_add" / "attention_remove" events, via the
standalone ``run_settings_console_delta_loop`` background task started once
from the API lifespan (bu-3quv8). The dashboard's
``use-settings-console-live.ts`` subscribes there (single-socket doctrine,
bu-qvnce.14). The earlier dedicated ``WS /api/settings/stream`` route was
retired in bu-01r64.2 once the bus fully covered this traffic.

Partial-failure mode: when a sub-system aggregation fails, the exception is
caught per-subsystem and surfaces an amber attention item instead of erroring
the whole response.

CRITICAL: This module is read-only aggregation. No mutations. No audit calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
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
from butlers.api.routers.events import emit_event
from butlers.core.model_routing import price_mtd_from_ledger

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
    """Aggregate header counts for the console overview.

    Each field is ``None`` when its subsystem aggregation failed -- a
    confident ``0`` would otherwise be indistinguishable from a truthful
    "no active butlers" / "no open approvals" result. The corresponding
    subsystem failure is always ALSO surfaced as an amber attention item, but
    a header-only consumer must not have to cross-reference that list to
    tell "genuinely zero" from "unknown".
    """

    active_butlers: int | None
    spend_mtd_usd: float | None
    open_approvals: int | None
    models_verified: int | None
    models_total: int | None


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
    db: DatabaseManager | None,
) -> tuple[float | None, float | None, AttentionItem | None]:
    """Return (spend_mtd_usd, ceiling_usd, optional_attention_item).

    Prices month-to-date spend from ``public.token_usage_ledger`` via
    :func:`butlers.core.model_routing.price_mtd_from_ledger` -- the exact
    helper ``check_monthly_ceiling`` (the spawn-deny gate) and
    ``GET /api/spend/forecast`` price MTD from (bu-7o89u.1) -- so this figure
    can never diverge from the number that halts the fleet. Previously this
    summed a rolling-30d per-butler ``sessions_summary`` fan-out under an
    "MTD" label, which both mislabeled the window and could fire ceiling
    alarms the gate itself was not enforcing.

    Ceiling is read from the same singleton ``public.spend_ceiling`` row
    ``check_monthly_ceiling`` reads, so the "near ceiling" attention item
    below compares true MTD against the exact ceiling the gate enforces.

    Never raises; a ledger failure -- or no ``DatabaseManager`` wired (there
    is no MCP fallback for ledger rows, mirroring the forecast endpoint) --
    returns ``(None, None, amber-attention-item)`` so ``HeaderCounts`` renders
    "unavailable" rather than a fabricated ``$0``.
    """
    if db is None:
        return (
            None,
            None,
            AttentionItem(
                tone="amber",
                kind="subsystem_error",
                text="Could not fetch spend data — totals may be unavailable.",
                action_route="/settings/spend",
            ),
        )
    try:
        pool = db.pool("switchboard")
        mtd_usd = await price_mtd_from_ledger(pool)

        ceiling_usd: float | None = None
        ceiling_row = await pool.fetchrow(
            "SELECT monthly_usd FROM public.spend_ceiling WHERE id = 1"
        )
        if ceiling_row:
            ceiling_usd = float(ceiling_row["monthly_usd"])

        return round(mtd_usd, 2), ceiling_usd, None
    except Exception as exc:
        logger.warning("console: spend-mtd aggregation failed: %s", exc)
        return (
            None,
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
        _get_spend_mtd(db),
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
    # _get_spend_mtd sets ceiling and spend_mtd together (both real or both
    # None on failure), so ceiling is not None here implies spend_mtd is a
    # real, ledger-priced float -- never a fabricated placeholder.
    if (
        ceiling is not None
        and spend_mtd is not None
        and ceiling > 0
        and projection_confidence != "low"
    ):
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
            # None (not a confident 0) when the subsystem aggregation failed
            # -- see HeaderCounts docstring.
            "active_butlers": None if butler_err is not None else active_butlers,
            "spend_mtd_usd": None if spend_err is not None else spend_mtd,
            "open_approvals": None if approval_err is not None else open_approvals,
            "models_verified": None if model_count_err is not None else models_verified,
            "models_total": None if model_count_err is not None else models_total,
        },
        "attention": [item.model_dump() for item in visible],
        "attention_truncated_count": truncated,
    }


def _compute_console_deltas(
    prev_payload: dict[str, Any],
    new_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Diff two console payloads into (header_delta, added_items, removed_kinds).

    Pure, no I/O -- used by the standalone bus-emitting background loop
    (``run_settings_console_delta_loop``).
    """
    old_counts = prev_payload["header_counts"]
    new_counts = new_payload["header_counts"]
    header_delta = {k: v for k, v in new_counts.items() if old_counts.get(k) != v}

    old_items = {item["kind"]: item for item in prev_payload["attention"]}
    new_items = {item["kind"]: item for item in new_payload["attention"]}

    added = [item for kind, item in new_items.items() if kind not in old_items]
    removed = [kind for kind in old_items if kind not in new_items]

    return header_delta, added, removed


# ---------------------------------------------------------------------------
# Standalone background loop -- fans deltas onto the unified fleet event bus
# ---------------------------------------------------------------------------

#: Polling cadence for the standalone delta loop below.
_CONSOLE_DELTA_INTERVAL_S = 5.0


async def run_settings_console_delta_loop(
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    pricing: PricingConfig,
    db: DatabaseManager | None,
    *,
    interval_s: float = _CONSOLE_DELTA_INTERVAL_S,
) -> None:
    """Continuously aggregate the console payload and fan header_delta /
    attention_add / attention_remove onto the unified fleet event bus
    (``WS /api/events/stream``, see ``emit_event``) whenever it changes.

    Runs as a single standalone ``asyncio.Task`` started once from the API
    lifespan (see ``butlers.api.app.lifespan``), independent of any
    ``WS /api/settings/stream`` connection. Unlike that legacy per-connection
    loop -- which only computes deltas while a client happens to be attached,
    and duplicates the full aggregation once per connection -- this task
    always runs exactly once regardless of how many (if any) dashboards are
    open, so ``EventBusProvider`` subscribers (``use-settings-console-live.ts``)
    get live updates without a dashboard needing to open a second socket.

    Also keeps the ``GET /api/settings/console`` in-memory cache warm as a
    side effect: a request arriving between ticks is served from a payload
    this loop already computed, rather than triggering its own aggregation.

    Sleeps first (mirrors ``run_secrets_lifecycle_loop``) so process startup
    (including every test that runs the full app lifespan) never pays a
    real-aggregation burst before the first tick actually matters, and emits
    no deltas on that first tick (nothing to diff against yet) -- callers
    always have the REST snapshot for their initial state; deltas only need
    to cover changes from that point forward. Never raises -- a failed
    aggregation or emit is logged and the loop continues (mirrors
    ``run_secrets_lifecycle_loop``'s fault isolation).
    """
    global _cache_ts, _cache_payload

    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")

    prev_payload: dict[str, Any] | None = None

    while True:
        await asyncio.sleep(interval_s)

        try:
            new_payload = await _build_console_payload(configs, mgr, pricing, db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("settings_console_delta_loop: aggregation tick failed")
            continue

        if prev_payload is not None:
            try:
                header_delta, added, removed = _compute_console_deltas(prev_payload, new_payload)
                if header_delta:
                    emit_event("header_delta", header_delta)
                for item in added:
                    emit_event("attention_add", item)
                for kind in removed:
                    emit_event("attention_remove", {"kind": kind})
            except Exception:
                logger.debug(
                    "settings_console_delta_loop: emit_event failed (non-fatal)", exc_info=True
                )

        prev_payload = new_payload
        async with _cache_lock:
            _cache_payload = new_payload
            _cache_ts = time.monotonic()


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
