"""Dashboard briefing endpoint.

GET /api/dashboard/briefing

Returns a six-field Briefing object:
    greet        "Good {time_of_day}."
    headline     Templated body for the computed state_class.
    elaboration  LLM-written paragraph or templated fallback.
    source       "llm" or "fallback"
    state_class  One of: urgent, busy, mild, degraded-quiet, degraded, quiet.
    generated_at ISO 8601 wall-clock timestamp of composition.

Access: owner-only. HTTP 403 for non-owner sessions, HTTP 401 for
unauthenticated (via ApiKeyMiddleware).

Caching: per-owner LRU+TTL, 5-minute TTL. Cache hit preserves
the original generated_at.

Robustness: the endpoint never raises to the caller. LLM failures,
timeouts, and voice-lint rejections fall through to the templated
fallback. Classification exceptions fall through to the degraded class
(a classifier bug is itself a swallowed failure -- it must not compose
"quiet" either). HTTP 500 is reserved for failures in the templated
fallback itself (code or import errors, not normal operation).

State sourcing (bu-gcz9e.1 -- "one attention model on the dashboard"): the
headline is classified from the SAME composed attention model the Overview
dashboard page renders, not a second, independently-drifting one:
    - butler liveness: GET /api/butlers/board (the same canonical,
      cadence-aware verdict the /butlers status board and the Overview
      page's attention list use -- replaces the former bespoke
      butler_registry liveness CASE).
    - audit-derived issues: the shared audit-group CTE also used by the
      Issues page (unchanged from the prior implementation).
    - pending approvals: the same all-pools ``pending_actions`` fan-out
      settings_console.py uses.
    - failed notifications: GET /api/notifications/stats's ``failed`` count
      (the same terminal-failure definition and all-time window the
      Overview page's useNotificationStats() reads) -- replaces the former
      feed that counted every SENT notification as attention.
    - QA: the same circuit-breaker-tripped / last-patrol-failed / dispatched /
      novel-findings priority frontend/src/components/overview/model.ts::
      summarizeQaState uses.

A source that cannot be read is tracked via DegradedSources and surfaces as
the "degraded" state_class instead of silently composing "quiet"
(classify.classify's docstring documents the exact priority).

Design reference: openspec/changes/archive/2026-05-15-dashboard-overview-briefing/
    specs/dashboard-briefing/spec.md
Design notes: openspec/changes/archive/2026-05-15-dashboard-overview-briefing/design.md
Spec reference: openspec/specs/dashboard-briefing/spec.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.audit_grouping import attention_item_from_audit_group_row, build_audit_group_query
from butlers.api.briefing.cache import BriefingCache, get_cache
from butlers.api.briefing.classify import classify, headline_for, time_of_day
from butlers.api.briefing.fallback import elaborate_fallback
from butlers.api.briefing.lint import first_violation, voice_lint_passes
from butlers.api.briefing.prompts import elaborate_llm
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
from butlers.core.general_settings import load_general_settings
from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard-briefing"])


# ---------------------------------------------------------------------------
# Prometheus counters
# ---------------------------------------------------------------------------

briefing_reads_total = get_or_create_counter(
    "briefing_reads_total",
    "Number of GET /api/dashboard/briefing requests.",
)

briefing_cache_hits_total = get_or_create_counter(
    "briefing_cache_hits_total",
    "Number of GET /api/dashboard/briefing requests served from cache.",
)

briefing_elaboration_llm_total = get_or_create_counter(
    "briefing_elaboration_llm_total",
    "Number of briefing elaborations produced by the LLM.",
)

briefing_elaboration_fallback_total = get_or_create_counter(
    "briefing_elaboration_fallback_total",
    "Number of briefing elaborations served from the templated fallback.",
)

briefing_elaboration_rejected_total = get_or_create_counter(
    "briefing_elaboration_rejected_total",
    "Number of LLM elaborations rejected by the voice lint.",
)

briefing_classification_error_total = get_or_create_counter(
    "briefing_classification_error_total",
    "Number of classification exceptions caught and downgraded to degraded.",
)


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class Briefing(BaseModel):
    """The six-field dashboard briefing object returned by the endpoint."""

    greet: str
    headline: str
    elaboration: str
    source: str  # "llm" or "fallback"
    state_class: str
    generated_at: str  # ISO 8601


# ---------------------------------------------------------------------------
# Dependency stub (overridden at startup or in tests)
# ---------------------------------------------------------------------------


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Owner-contact assertion (mirrors system.py pattern)
# ---------------------------------------------------------------------------


async def _assert_owner_contact(pool: Any) -> Any:
    """Raise HTTP 403 unless an owner entity is found in the DB.

    Asserts ``'owner' = ANY(roles)`` on public.entities. Returns the owner's
    entity id on success (used as the opaque per-owner briefing-cache key, which
    must match ``briefing.cache.resolve_owner_id``).

    In v1, the dashboard is owner-only and there is no per-request
    identity in the request. The assertion checks that at least one
    owner entity exists (i.e., the system is bootstrapped). A fuller
    v2 implementation would extract the caller identity from the session
    and verify it.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT id
            FROM public.entities
            WHERE 'owner' = ANY(roles)
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Owner-entity assertion query failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Owner contact assertion failed"},
        )

    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Owner contact not found"},
        )

    return row["id"]


# ---------------------------------------------------------------------------
# State fetch helpers
# ---------------------------------------------------------------------------

# Board activities that the Overview page's attention list flags -- mirrors
# frontend/src/hooks/use-butler-status-board.ts::NEEDS_YOU_ACTIVITIES exactly,
# so the briefing headline and the attention list can never disagree about
# which butlers need a look.
_BOARD_ACTIVITY_SEVERITY = {
    "offline": "high",
    "quarantined": "high",
    "overdue": "medium",
    "unknown": "medium",
}
_NEEDS_ATTENTION_ACTIVITIES = frozenset(_BOARD_ACTIVITY_SEVERITY)

# Maps a board activity onto the pre-existing butler_statuses "status"
# vocabulary (degraded/error drive classify()'s degraded-quiet branch).
_BOARD_ACTIVITY_TO_HEALTH_STATUS = {
    "offline": "down",
    "quarantined": "quarantined",
    "overdue": "stale",
    "unknown": "degraded",
}


_UNHEALTHY_STATUSES = {"degraded", "down", "error", "stale", "quarantined"}


def _compute_overview_totals(state: dict) -> dict:
    attention_items = state.get("attention_items", [])
    butler_statuses = state.get("butler_statuses", [])
    return {
        "attention_total": len(attention_items),
        "attention_high": sum(1 for item in attention_items if item.get("severity") == "high"),
        "attention_medium": sum(1 for item in attention_items if item.get("severity") == "medium"),
        "attention_low": sum(1 for item in attention_items if item.get("severity") == "low"),
        "butlers_total": len(butler_statuses),
        "butlers_unhealthy": sum(
            1
            for item in butler_statuses
            if str(item.get("status", "")).lower() in _UNHEALTHY_STATUSES
        ),
    }


async def _fetch_dashboard_state(
    pool: Any,
    now: datetime,
    *,
    db: DatabaseManager,
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    pricing: PricingConfig,
) -> dict:
    """Build the internal dashboard state used for classification and prose.

    Reads (concurrently, each isolated from the others' failures):
        audit-derived attention items (shared CTE, also used by the Issues page)
        butler liveness from GET /api/butlers/board (the canonical verdict)
        pending approvals across all butler pools
        failed-notification count (GET /api/notifications/stats)
        QA state (last patrol, 24h novel/dispatched counts)

    The public API still returns the six-field Briefing object. This richer
    state is intentionally internal so the local runtime has enough context to
    name the important current fact without exposing a second wire contract.
    """
    state: dict = {
        "now": now,
        "attention_items": [],
        "notification_items": [],
        "audit_issues": [],
        "butler_statuses": [],
        "degraded_sources": [],
        "overview_totals": {
            "attention_total": 0,
            "attention_high": 0,
            "attention_medium": 0,
            "attention_low": 0,
            "butlers_total": 0,
            "butlers_unhealthy": 0,
        },
    }

    # Run all five independent fetches concurrently; each is internally
    # guarded and never raises (a failure degrades that source's contribution
    # to empty/None plus a `degraded=True` flag -- see classify.classify).
    (
        audit_result,
        board_result,
        approvals_result,
        notifications_result,
        qa_result,
    ) = await asyncio.gather(
        _fetch_audit_issues(pool),
        _fetch_board_state(configs, mgr, db, pricing),
        _fetch_approvals_state(db),
        _fetch_notifications_state(db),
        _fetch_qa_state(db),
        return_exceptions=False,
    )

    audit_issues, audit_attention, audit_degraded = audit_result
    board_attention, butler_statuses, board_degraded = board_result
    approvals_pending, approvals_degraded = approvals_result
    failed_notifications, notifications_degraded = notifications_result
    qa_state, qa_degraded = qa_result

    attention_items: list[dict] = []

    if failed_notifications > 0:
        attention_items.append(
            {
                "severity": "medium",
                "type": "notification",
                "butler": None,
                "description": (
                    f"{failed_notifications} failed notification"
                    f"{'' if failed_notifications == 1 else 's'}"
                ),
                "link": "/notifications?status=failed",
                "occurrences": failed_notifications,
                "source": "notification",
            }
        )

    attention_items.extend(audit_attention)
    attention_items.extend(board_attention)

    if approvals_pending > 0:
        attention_items.append(
            {
                "severity": "medium",
                "type": "approval",
                "butler": None,
                "description": (
                    f"{approvals_pending} pending approval{'' if approvals_pending == 1 else 's'}"
                ),
                "link": "/approvals",
                "occurrences": approvals_pending,
                "source": "approval",
            }
        )

    qa_item = _qa_attention_item(qa_state)
    if qa_item is not None:
        attention_items.append(qa_item)

    degraded_sources = []
    if audit_degraded:
        degraded_sources.append("audit")
    if board_degraded:
        degraded_sources.append("board")
    if approvals_degraded:
        degraded_sources.append("approvals")
    if notifications_degraded:
        degraded_sources.append("notifications")
    if qa_degraded:
        degraded_sources.append("qa")

    state["attention_items"] = attention_items
    state["audit_issues"] = audit_issues
    state["butler_statuses"] = butler_statuses
    state["degraded_sources"] = degraded_sources
    state["overview_totals"] = _compute_overview_totals(state)
    return state


async def _fetch_audit_issues(pool: Any) -> tuple[list, list, bool]:
    """Fetch grouped audit errors and return (audit_issues, attention_items, degraded).

    Uses the shared CTE helper for consistent grouping with the Issues page.
    """
    try:
        rows = await pool.fetch(
            build_audit_group_query(
                where_extra="\n                  AND created_at >= NOW() - INTERVAL '24 hours'",
                limit=20,
            )
        )
        audit_issues = []
        attention_items = []
        for row in rows:
            item = attention_item_from_audit_group_row(row)
            audit_issues.append(item)
            attention_items.append(item)
        return audit_issues, attention_items, False
    except Exception as exc:
        logger.warning("Could not fetch audit-derived attention items: %s", exc)
        return [], [], True


def _map_board_rows(
    rows: list[Any], *, registry_source_error: bool
) -> tuple[list[dict], list[dict]]:
    """Map GET /api/butlers/board rows onto (attention_items, butler_statuses).

    Only "butler" rows are considered -- mirrors
    deriveOverviewTriageModel's ``boardRows.filter(row => row.type ===
    "butler")``; staffers such as QA report their own health via
    _fetch_qa_state instead.

    When the board's own registry query failed, every row uniformly degrades
    to activity "unknown" (see butlers.py's _derive_board_activity) -- that is
    one systemic outage, not N independent butler problems, so it is surfaced
    once via the caller's "board" degraded source instead of fabricating an
    attention item (and a degraded-quiet verdict) per butler.
    """
    attention_items: list[dict] = []
    butler_statuses: list[dict] = []

    for row in rows:
        if row.type != "butler":
            continue

        activity = row.activity
        is_systemic_unknown = registry_source_error and activity == "unknown"
        status = (
            "healthy"
            if is_systemic_unknown
            else _BOARD_ACTIVITY_TO_HEALTH_STATUS.get(activity, "healthy")
        )
        butler_statuses.append(
            {
                "name": row.name,
                "status": status,
                "type": row.type,
                "eligibility_state": row.eligibility,
                "last_seen_at": row.last_heartbeat_at,
                "quarantine_reason": row.quarantine_reason,
            }
        )

        if is_systemic_unknown or activity not in _NEEDS_ATTENTION_ACTIVITIES:
            continue

        attention_items.append(
            {
                "severity": _BOARD_ACTIVITY_SEVERITY[activity],
                "type": "runtime",
                "butler": row.name,
                "description": f"{row.name} is {activity}",
                "link": f"/butlers/{row.name}",
                "last_seen_at": row.last_heartbeat_at,
                "source": "board",
            }
        )

    return attention_items, butler_statuses


async def _fetch_board_state(
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    db: DatabaseManager,
    pricing: PricingConfig,
) -> tuple[list[dict], list[dict], bool]:
    """Return (attention_items, butler_statuses, degraded).

    Calls the SAME GET /api/butlers/board computation the /butlers status
    board and the Overview page's attention list render (bu-gcz9e.1 -- "one
    attention model", not a second, independently-drifting liveness verdict).
    Replaces the former bespoke butler_registry liveness CASE.
    """
    try:
        from butlers.api.routers.butlers import get_butlers_board

        response = await get_butlers_board(configs=configs, mgr=mgr, db=db, pricing=pricing)
    except Exception as exc:
        logger.warning("Could not fetch board state for briefing: %s", exc)
        return [], [], True

    board = response.data
    registry_source_error = board.aggregates.registry_source_error
    attention_items, butler_statuses = _map_board_rows(
        board.rows, registry_source_error=registry_source_error
    )
    return attention_items, butler_statuses, registry_source_error


async def _fetch_approvals_state(db: DatabaseManager) -> tuple[int, bool]:
    """Return (pending_count, degraded).

    Mirrors settings_console.py's ``_count_open_approvals`` (same
    ``_find_all_approvals_pools`` fan-out + ``pending_actions`` count), but
    tracks a per-pool failure as degraded instead of silently folding it into
    the total -- a swallowed approvals-pool failure must never read as a
    truthful "zero pending" (bu-gcz9e.1).
    """
    try:
        from butlers.api.routers.approvals import _find_all_approvals_pools

        pools = await _find_all_approvals_pools(db)
    except Exception as exc:
        logger.warning("Could not resolve approvals pools for briefing: %s", exc)
        return 0, True

    total = 0
    degraded = False
    for pool in pools:
        try:
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
            )
            total += count or 0
        except Exception as exc:
            logger.warning("Could not count pending approvals for one pool: %s", exc)
            degraded = True
    return total, degraded


async def _fetch_notifications_state(db: DatabaseManager) -> tuple[int, bool]:
    """Return (failed_count, degraded).

    Calls GET /api/notifications/stats's handler directly so the briefing
    uses the exact same terminal-failure definition and all-time window the
    Overview page's useNotificationStats() reads -- replaces the former feed
    that counted every SENT (not just failed) notification as attention.
    """
    try:
        from butlers.api.routers.notifications import notification_stats

        response = await notification_stats(since=None, until=None, db=db)
    except Exception as exc:
        logger.warning("Could not fetch notification stats for briefing: %s", exc)
        return 0, True

    stats = response.data
    return stats.failed, not stats.source_available


def _is_missing_relation_error(exc: Exception, table: str) -> bool:
    """Return whether *exc* indicates *table* simply does not exist yet.

    Mirrors notifications.py::_is_missing_notifications_table_error and
    memory.py::_is_missing_memory_schema_error -- an un-migrated table is the
    expected case for a deployment that has not provisioned the QA module,
    NOT a degraded source. Any other exception is a genuine failure.
    """
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    return "relation" in msg and table in msg and "does not exist" in msg


async def _fetch_qa_state(db: DatabaseManager) -> tuple[dict | None, bool]:
    """Return (qa_state, degraded).

    qa_state mirrors the fields GET /api/qa/summary exposes that
    frontend/src/components/overview/model.ts::summarizeQaState reads to
    decide whether QA needs an attention row: the circuit-breaker tripped
    state (checked FIRST, mirroring summarizeQaState -- bu-y2xqi), the last
    non-running patrol's failure state, and the current 24h window's
    novel/dispatched counts. ``None`` when the QA tables are not provisioned
    (legitimately absent, not degraded).
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        return None, False

    try:
        last_patrol = await pool.fetchrow(
            """
            SELECT status, error_detail
            FROM public.qa_patrols
            WHERE status != 'running'
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        stats_24h = await pool.fetchrow(
            """
            SELECT
                COALESCE(SUM(novel_count), 0) AS novel_findings,
                COALESCE(SUM(dispatched_count), 0) AS dispatched_investigations
            FROM public.qa_patrols
            WHERE started_at >= NOW() - INTERVAL '24 hours'
            """
        )
    except Exception as exc:
        if _is_missing_relation_error(exc, "qa_patrols"):
            return None, False
        logger.warning("Could not fetch QA state for briefing: %s", exc)
        return None, True

    # Circuit breaker: reuse qa.py's canonical computation (the SAME
    # launched-attempt + latest-reset filter GET /api/qa/summary and the
    # dispatch-admission gate use) rather than reimplementing it here. This
    # is a distinct table (public.healing_attempts) from qa_patrols above, so
    # its own failure degrades the QA source without discarding the
    # patrol-derived signal already fetched -- mirrors the
    # per-pool-failure-but-keep-going pattern in _fetch_approvals_state.
    degraded = False
    circuit_breaker_tripped = False
    circuit_breaker_consecutive_failures = 0
    try:
        from butlers.api.routers.qa import (
            _CIRCUIT_BREAKER_THRESHOLD,
            _compute_circuit_breaker_state,
            _fetch_recent_circuit_breaker_rows,
        )

        cb_rows = await _fetch_recent_circuit_breaker_rows(pool, limit=_CIRCUIT_BREAKER_THRESHOLD)
        _, circuit_breaker_consecutive_failures, circuit_breaker_tripped = (
            _compute_circuit_breaker_state(cb_rows, threshold=_CIRCUIT_BREAKER_THRESHOLD)
        )
    except Exception as exc:
        if _is_missing_relation_error(exc, "healing_attempts") or _is_missing_relation_error(
            exc, "breaker_resets"
        ):
            pass
        else:
            logger.warning("Could not fetch QA circuit breaker state for briefing: %s", exc)
            degraded = True

    return (
        {
            "circuit_breaker_tripped": circuit_breaker_tripped,
            "circuit_breaker_consecutive_failures": circuit_breaker_consecutive_failures,
            "last_patrol_failed": bool(
                last_patrol is not None
                and (last_patrol["status"] == "failed" or last_patrol["error_detail"])
            ),
            "novel_findings": int(stats_24h["novel_findings"]) if stats_24h else 0,
            "dispatched_investigations": (
                int(stats_24h["dispatched_investigations"]) if stats_24h else 0
            ),
        },
        degraded,
    )


def _qa_attention_item(qa_state: dict | None) -> dict | None:
    """Return an attention item for QA state, or None.

    Mirrors frontend/src/components/overview/model.ts::summarizeQaState's
    priority order exactly (circuit-breaker tripped > failed patrol >
    dispatched investigations > novel findings), so the briefing and the
    attention list agree on when QA needs a look. A tripped breaker means the
    QA staffer has stopped dispatching entirely after repeated consecutive
    failures -- more severe than a single failed patrol run, so it is checked
    FIRST (bu-y2xqi).
    """
    if qa_state is None:
        return None

    if qa_state.get("circuit_breaker_tripped"):
        n = qa_state.get("circuit_breaker_consecutive_failures", 0)
        # severity="high" -- the briefing's attention-item vocabulary tops
        # out at "high" (no "critical" tier); this mirrors
        # attention_item_from_audit_group_row's "briefing maps critical to
        # high for display" convention. The Overview attention list's own
        # row for this same signal renders its finer "critical" severity
        # directly from summarizeQaState.
        return {
            "severity": "high",
            "type": "qa",
            "butler": "qa",
            "description": (
                f"QA circuit breaker tripped ({n} consecutive failure{'' if n == 1 else 's'})"
            ),
            "link": "/qa",
            "occurrences": n,
            "source": "qa",
        }

    if qa_state["last_patrol_failed"]:
        return {
            "severity": "high",
            "type": "qa",
            "butler": "qa",
            "description": "QA patrol failed",
            "link": "/qa",
            "source": "qa",
        }

    if qa_state["dispatched_investigations"] > 0:
        n = qa_state["dispatched_investigations"]
        return {
            "severity": "medium",
            "type": "qa",
            "butler": "qa",
            "description": f"{n} QA investigation{'' if n == 1 else 's'} dispatched",
            "link": "/qa",
            "occurrences": n,
            "source": "qa",
        }

    if qa_state["novel_findings"] > 0:
        n = qa_state["novel_findings"]
        return {
            "severity": "medium",
            "type": "qa",
            "butler": "qa",
            "description": f"{n} novel QA finding{'' if n == 1 else 's'}",
            "link": "/qa",
            "occurrences": n,
            "source": "qa",
        }

    return None


async def _owner_local_now(pool: Any, *, utc_now: datetime | None = None) -> datetime:
    """Return the current wall-clock time in the owner's configured timezone."""
    current = utc_now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)

    try:
        settings = await load_general_settings(pool)
        timezone_name = str(settings.get("timezone") or "UTC")
        return current.astimezone(ZoneInfo(timezone_name))
    except Exception as exc:
        logger.warning("Could not resolve owner timezone for dashboard briefing: %s", exc)
        return current.astimezone(UTC)


# ---------------------------------------------------------------------------
# Briefing composition
# ---------------------------------------------------------------------------


async def _compose_briefing(
    state: dict,
    cache: BriefingCache,
    owner_id: Any,
    pool: Any,
) -> dict:
    """Compose a fresh Briefing dict and populate the cache.

    Pipeline:
        1. Classify state -> state_class.
        2. Compute greet and headline.
        3. Attempt LLM elaboration (skipped for "degraded" -- see step 3 below).
        4. Run voice lint on LLM response.
        5. Fall back to templated paragraph on any failure.
        6. Record wall-clock generated_at once, regardless of source.
        7. Store in cache.
    """
    now = state["now"]

    # Step 1: classify. A classifier exception is itself a swallowed failure
    # -- it must not compose "quiet" any more than a swallowed fetch failure
    # may (see classify.classify's docstring), so it downgrades to "degraded".
    try:
        state_class = classify(state)
    except Exception as exc:
        logger.error("Classification failed, defaulting to degraded: %s", exc)
        briefing_classification_error_total.inc()
        state_class = "degraded"

    # Step 2: greet and headline.
    hour = now.hour if isinstance(now, datetime) else 12
    tod = time_of_day(hour)
    greet = f"Good {tod}."

    attention_items = state.get("attention_items", [])
    butler_statuses = state.get("butler_statuses", [])
    degraded_sources = state.get("degraded_sources", [])

    high_count = sum(1 for a in attention_items if a.get("severity") == "high")
    total = len(attention_items)
    degraded_count = sum(1 for b in butler_statuses if b.get("status") in ("degraded", "error"))

    n_for_class = {
        "urgent": high_count if high_count else 1,
        "busy": total,
        "mild": total,
        "degraded-quiet": degraded_count if degraded_count else 1,
        "degraded": len(degraded_sources) if degraded_sources else 1,
        "quiet": 0,
    }
    headline = headline_for(state_class, n_for_class.get(state_class, 0))

    # Step 3 + 4: LLM elaboration with voice lint. Skipped entirely for
    # "degraded" -- the true state is unknown by definition, so the
    # deterministic fallback paragraph is the only response that cannot
    # mischaracterize it (an LLM asked to narrate incomplete data risks
    # projecting confidence the state does not support).
    elaboration: str | None = None
    source = "fallback"

    if state_class != "degraded":
        try:
            llm_text = await elaborate_llm(pool, state, state_class)
            if llm_text:
                if voice_lint_passes(llm_text):
                    elaboration = llm_text
                    source = "llm"
                    briefing_elaboration_llm_total.inc()
                else:
                    violation = first_violation(llm_text)
                    logger.info("LLM elaboration rejected by voice lint (violation=%s)", violation)
                    briefing_elaboration_rejected_total.inc()
        except Exception as exc:
            logger.warning("LLM elaboration raised unexpectedly: %s", exc)

    # Step 5: fallback if LLM path did not produce a passing response.
    if elaboration is None:
        elaboration = elaborate_fallback(state, state_class)
        briefing_elaboration_fallback_total.inc()

    # Step 6: generated_at records wall-clock composition time, set once.
    generated_at = datetime.now(UTC).isoformat()

    briefing_dict = {
        "greet": greet,
        "headline": headline,
        "elaboration": elaboration,
        "source": source,
        "state_class": state_class,
        "generated_at": generated_at,
    }

    # Step 7: cache.
    cache.set(owner_id, briefing_dict)
    return briefing_dict


# ---------------------------------------------------------------------------
# GET /api/dashboard/briefing
# ---------------------------------------------------------------------------


@router.get("/briefing", response_model=ApiResponse[Briefing])
async def get_dashboard_briefing(
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[Briefing]:
    """Return the dashboard briefing for the authenticated owner.

    - Owner-only: HTTP 403 for non-owner, HTTP 401 for unauthenticated.
    - 5-minute per-owner cache: cache hit preserves original generated_at.
    - LLM elaboration with voice lint; falls through to templated fallback.
    - Classification exception falls through to the degraded paragraph.
    - Never raises HTTP 500 in normal operation.
    """
    briefing_reads_total.inc()

    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")
    try:
        settings_pool = db.credential_shared_pool()
    except KeyError:
        settings_pool = sw_pool

    # Owner-only gate (HTTP 403 for non-owner, passes 401 from middleware).
    owner_id = await _assert_owner_contact(sw_pool)

    # Cache check.
    cached = cache.get(owner_id)
    if cached is not None:
        briefing_cache_hits_total.inc()
        return ApiResponse(data=Briefing(**cached))

    # Compose a fresh briefing.
    now = await _owner_local_now(settings_pool)
    state = await _fetch_dashboard_state(
        sw_pool, now, db=db, configs=configs, mgr=mgr, pricing=pricing
    )

    briefing_dict = await _compose_briefing(state, cache, owner_id, sw_pool)
    return ApiResponse(data=Briefing(**briefing_dict))
