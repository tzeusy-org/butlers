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

from fastapi import APIRouter, Depends, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel

from butlers.api.briefing.cache import BriefingCache, get_cache
from butlers.api.briefing.classify import classify, headline_for, time_of_day
from butlers.api.briefing.fallback import elaborate_fallback
from butlers.api.briefing.lint import first_violation, voice_lint_passes
from butlers.api.briefing.prompts import elaborate_llm
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse

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


async def _fetch_dashboard_state(pool: Any, now: datetime) -> dict:
    """Build a minimal dashboard state dict from DB for classification.

    Reads:
        attention items from a notifications/issues proxy query.
        butler statuses from the butler_registry.

    In v1 the attention_items list is derived from unread high-priority
    notifications; butler_statuses is derived from the registry heartbeat.
    Falls back to empty lists on any query failure (the classifier handles
    empty state gracefully).
    """
    state: dict = {
        "now": now,
        "attention_items": [],
        "butler_statuses": [],
    }

    # Attention items: unread notifications with severity or priority set.
    # Read from switchboard schema (cross-butler notifications table).
    try:
        rows = await pool.fetch(
            """
            SELECT
                id,
                COALESCE(metadata->>'severity', 'low') AS severity
            FROM notifications
            WHERE status = 'unread'
              AND created_at >= NOW() - INTERVAL '24 hours'
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        state["attention_items"] = [{"id": str(r["id"]), "severity": r["severity"]} for r in rows]
    except Exception as exc:
        logger.debug("Could not fetch attention items: %s", exc)

    # Butler statuses from the registry.
    try:
        rows = await pool.fetch(
            """
            SELECT name, status
            FROM butler_registry
            ORDER BY name ASC
            """
        )
        state["butler_statuses"] = [{"name": r["name"], "status": r["status"]} for r in rows]
    except Exception as exc:
        logger.debug("Could not fetch butler statuses: %s", exc)

    return state


# ---------------------------------------------------------------------------
# Briefing composition
# ---------------------------------------------------------------------------


async def _compose_briefing(
    state: dict,
    cache: BriefingCache,
    owner_id: Any,
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
        llm_text = await elaborate_llm(state, state_class)
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

    # Owner-only gate (HTTP 403 for non-owner, passes 401 from middleware).
    owner_id = await _assert_owner_contact(sw_pool)

    # Cache check.
    cached = cache.get(owner_id)
    if cached is not None:
        briefing_cache_hits_total.inc()
        return ApiResponse(data=Briefing(**cached))

    # Compose a fresh briefing.
    now = datetime.now(UTC)
    state = await _fetch_dashboard_state(sw_pool, now)

    briefing_dict = await _compose_briefing(state, cache, owner_id)
    return ApiResponse(data=Briefing(**briefing_dict))
