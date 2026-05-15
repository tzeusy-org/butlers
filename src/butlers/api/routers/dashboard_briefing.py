"""Dashboard briefing endpoint.

GET /api/dashboard/briefing

Returns a six-field Briefing object:
    greet        "Good {time_of_day}."
    headline     Templated body for the computed state_class.
    elaboration  LLM-written paragraph or templated fallback.
    source       "llm" or "fallback"
    state_class  One of: urgent, busy, mild, degraded-quiet, quiet.
    generated_at ISO 8601 wall-clock timestamp of composition.

Access: owner-only. HTTP 403 for non-owner sessions, HTTP 401 for
unauthenticated (via ApiKeyMiddleware).

Caching: per-owner LRU+TTL, 5-minute TTL. Cache hit preserves
the original generated_at.

Robustness: the endpoint never raises to the caller. LLM failures,
timeouts, and voice-lint rejections fall through to the templated
fallback. Classification exceptions fall through to the quiet class.
HTTP 500 is reserved for failures in the templated fallback itself
(code or import errors, not normal operation).

Design reference: openspec/changes/dashboard-overview-briefing/spec.md
Design notes: openspec/changes/dashboard-overview-briefing/design.md
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel

from butlers.api.audit_grouping import attention_item_from_audit_group_row, build_audit_group_query
from butlers.api.briefing.cache import BriefingCache, get_cache
from butlers.api.briefing.classify import classify, headline_for, time_of_day
from butlers.api.briefing.fallback import elaborate_fallback
from butlers.api.briefing.lint import first_violation, voice_lint_passes
from butlers.api.briefing.prompts import elaborate_llm
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.core.general_settings import load_general_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard-briefing"])


# ---------------------------------------------------------------------------
# Prometheus counters
# ---------------------------------------------------------------------------

briefing_reads_total = Counter(
    "briefing_reads_total",
    "Number of GET /api/dashboard/briefing requests.",
)

briefing_cache_hits_total = Counter(
    "briefing_cache_hits_total",
    "Number of GET /api/dashboard/briefing requests served from cache.",
)

briefing_elaboration_llm_total = Counter(
    "briefing_elaboration_llm_total",
    "Number of briefing elaborations produced by the LLM.",
)

briefing_elaboration_fallback_total = Counter(
    "briefing_elaboration_fallback_total",
    "Number of briefing elaborations served from the templated fallback.",
)

briefing_elaboration_rejected_total = Counter(
    "briefing_elaboration_rejected_total",
    "Number of LLM elaborations rejected by the voice lint.",
)

briefing_classification_error_total = Counter(
    "briefing_classification_error_total",
    "Number of classification exceptions caught and downgraded to quiet.",
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
    """Raise HTTP 403 unless an owner contact is found in the DB.

    Joins public.contacts -> public.entities and asserts
    'owner' = ANY(e.roles). Returns the owner's contact id on success.

    In v1, the dashboard is owner-only and there is no per-request
    identity in the request. The assertion checks that at least one
    owner entity exists (i.e., the system is bootstrapped). A fuller
    v2 implementation would extract the caller identity from the session
    and verify it.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT c.id
            FROM public.contacts c
            JOIN public.entities e ON c.entity_id = e.id
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Owner-contact assertion query failed: %s", exc)
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
# State fetch helper
# ---------------------------------------------------------------------------

_HIGH_SEVERITIES = {"critical", "error", "high"}
_MEDIUM_SEVERITIES = {"warning", "warn", "medium"}
_UNHEALTHY_STATUSES = {"degraded", "down", "error", "stale", "quarantined"}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Read asyncpg records and test doubles without assuming every column exists."""
    try:
        return row[key]
    except KeyError:
        return default


def _isoformat_or_none(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _normalize_severity(value: Any) -> str:
    severity = str(value or "low").lower()
    if severity in _HIGH_SEVERITIES:
        return "high"
    if severity in _MEDIUM_SEVERITIES:
        return "medium"
    return "low"


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return []


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


async def _fetch_dashboard_state(pool: Any, now: datetime) -> dict:
    """Build the internal dashboard state used for classification and prose.

    Reads:
        attention items from notifications and grouped audit issues.
        butler health from the butler_registry.

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
        "overview_totals": {
            "attention_total": 0,
            "attention_high": 0,
            "attention_medium": 0,
            "attention_low": 0,
            "butlers_total": 0,
            "butlers_unhealthy": 0,
        },
    }

    # Attention items: unread or failed recent notifications with message context.
    try:
        rows = await pool.fetch(
            """
            SELECT
                id,
                source_butler,
                channel,
                message,
                metadata,
                status,
                error,
                session_id,
                trace_id,
                created_at,
                COALESCE(
                    metadata->>'severity',
                    metadata->>'priority',
                    CASE WHEN status = 'failed' THEN 'high' ELSE 'low' END
                ) AS severity
            FROM notifications
            WHERE status IN ('sent', 'failed')
              AND created_at >= NOW() - INTERVAL '24 hours'
            ORDER BY
                CASE LOWER(COALESCE(metadata->>'severity', metadata->>'priority', status))
                    WHEN 'critical' THEN 0
                    WHEN 'error' THEN 0
                    WHEN 'high' THEN 0
                    WHEN 'failed' THEN 0
                    WHEN 'warning' THEN 1
                    WHEN 'warn' THEN 1
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                created_at DESC
            LIMIT 50
            """
        )
        notification_items = []
        for row in rows:
            severity = _normalize_severity(_row_get(row, "severity"))
            created_at = _isoformat_or_none(_row_get(row, "created_at"))
            source_butler = str(_row_get(row, "source_butler") or "unknown")
            message = str(_row_get(row, "message") or "Notification requires attention")
            notification = {
                "id": str(_row_get(row, "id")),
                "source_butler": source_butler,
                "channel": str(_row_get(row, "channel") or "unknown"),
                "message": message,
                "metadata": _row_get(row, "metadata") or {},
                "status": str(_row_get(row, "status") or "unread"),
                "severity": severity,
                "error": _row_get(row, "error"),
                "session_id": (
                    str(_row_get(row, "session_id")) if _row_get(row, "session_id") else None
                ),
                "trace_id": _row_get(row, "trace_id"),
                "created_at": created_at,
            }
            notification_items.append(notification)
            state["attention_items"].append(
                {
                    "severity": severity,
                    "type": "notification",
                    "butler": source_butler,
                    "description": message,
                    "link": "/notifications",
                    "error_message": _row_get(row, "error"),
                    "occurrences": 1,
                    "first_seen_at": created_at,
                    "last_seen_at": created_at,
                    "source": "notification",
                }
            )
        state["notification_items"] = notification_items
    except Exception as exc:
        logger.warning("Could not fetch attention items: %s", exc)

    # Group recent audit failures into issue-like attention items. Uses the
    # shared CTE (with tmp-path normalization) so grouping is consistent with
    # the Issues page. Window is 24 hours; capped at 20 groups.
    try:
        rows = await pool.fetch(
            build_audit_group_query(
                where_extra="\n                  AND created_at >= NOW() - INTERVAL '24 hours'",
                limit=20,
            )
        )
        audit_issues = []
        for row in rows:
            item = attention_item_from_audit_group_row(row)
            audit_issues.append(item)
            state["attention_items"].append(item)
        state["audit_issues"] = audit_issues
    except Exception as exc:
        logger.warning("Could not fetch audit-derived attention items: %s", exc)

    # Butler statuses from the registry.
    try:
        rows = await pool.fetch(
            """
            SELECT
                name,
                COALESCE(agent_type, 'butler') AS agent_type,
                description,
                modules,
                capabilities,
                last_seen_at,
                eligibility_state,
                quarantine_reason,
                CASE
                    WHEN eligibility_state = 'quarantined' THEN 'error'
                    WHEN eligibility_state = 'stale' THEN 'degraded'
                    WHEN last_seen_at IS NULL THEN 'degraded'
                    WHEN last_seen_at > NOW() + INTERVAL '5 minutes' THEN 'degraded'
                    WHEN last_seen_at < NOW() - (liveness_ttl_seconds * INTERVAL '1 second')
                        THEN 'degraded'
                    ELSE 'healthy'
                END AS status
            FROM butler_registry
            ORDER BY name ASC
            """
        )
        state["butler_statuses"] = [
            {
                "name": str(_row_get(row, "name") or "unknown"),
                "status": str(_row_get(row, "status") or "unknown"),
                "type": str(_row_get(row, "agent_type") or "butler"),
                "eligibility_state": _row_get(row, "eligibility_state"),
                "last_seen_at": _isoformat_or_none(_row_get(row, "last_seen_at")),
                "description": _row_get(row, "description"),
                "modules": _json_list(_row_get(row, "modules")),
                "capabilities": _json_list(_row_get(row, "capabilities")),
                "quarantine_reason": _row_get(row, "quarantine_reason"),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("Could not fetch butler statuses: %s", exc)

    state["overview_totals"] = _compute_overview_totals(state)
    return state


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
        3. Attempt LLM elaboration.
        4. Run voice lint on LLM response.
        5. Fall back to templated paragraph on any failure.
        6. Record wall-clock generated_at once, regardless of source.
        7. Store in cache.
    """
    now = state["now"]

    # Step 1: classify (conservative: any exception -> quiet).
    try:
        state_class = classify(state)
    except Exception as exc:
        logger.error("Classification failed, defaulting to quiet: %s", exc)
        briefing_classification_error_total.inc()
        state_class = "quiet"

    # Step 2: greet and headline.
    hour = now.hour if isinstance(now, datetime) else 12
    tod = time_of_day(hour)
    greet = f"Good {tod}."

    attention_items = state.get("attention_items", [])
    butler_statuses = state.get("butler_statuses", [])

    high_count = sum(1 for a in attention_items if a.get("severity") == "high")
    total = len(attention_items)
    degraded_count = sum(1 for b in butler_statuses if b.get("status") in ("degraded", "error"))

    n_for_class = {
        "urgent": high_count if high_count else 1,
        "busy": total,
        "mild": total,
        "degraded-quiet": degraded_count if degraded_count else 1,
        "quiet": 0,
    }
    headline = headline_for(state_class, n_for_class.get(state_class, 0))

    # Step 3 + 4: LLM elaboration with voice lint.
    elaboration: str | None = None
    source = "fallback"

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
) -> ApiResponse[Briefing]:
    """Return the dashboard briefing for the authenticated owner.

    - Owner-only: HTTP 403 for non-owner, HTTP 401 for unauthenticated.
    - 5-minute per-owner cache: cache hit preserves original generated_at.
    - LLM elaboration with voice lint; falls through to templated fallback.
    - Classification exception falls through to the quiet paragraph.
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
    state = await _fetch_dashboard_state(sw_pool, now)

    briefing_dict = await _compose_briefing(state, cache, owner_id, sw_pool)
    return ApiResponse(data=Briefing(**briefing_dict))
