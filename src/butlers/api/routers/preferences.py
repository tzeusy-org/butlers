"""User-preferences dashboard endpoint.

Exposes the owner's active preference facts stored in the memory facts table.
Preference facts use the ``preferences:<domain>_<name>`` predicate namespace.

Endpoint:
    GET /api/preferences
        Returns all active preference facts for the owner, optionally filtered
        by predicate name. Matches the ``get_preferences`` MCP tool contract.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers.memory import (
    _is_missing_memory_schema_error,
    _memory_relation,
    _memory_schema_absent_at_start,
)
from butlers.core.owner import resolve_owner_entity_id_two_step as _resolve_owner_entity_id_two_step

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PreferenceEntry(BaseModel):
    """A single active preference fact for the owner."""

    predicate: str
    value: str | None
    scope: str | None
    importance: float
    permanence: str | None
    updated_at: str | None
    effective_confidence: float


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


def _all_pools(db: DatabaseManager) -> list[tuple[str, Any]]:
    """Return all available butler pools, skipping missing ones.

    Raises HTTPException(503) when no pool is available at all.
    """
    pools: list[tuple[str, Any]] = []
    for name in sorted(db.butler_names):
        try:
            pools.append((name, db.pool(name)))
        except KeyError:
            continue
    if not pools:
        raise HTTPException(status_code=503, detail="No database pools available")
    return pools


# ---------------------------------------------------------------------------
# Owner resolution — delegates to the shared butlers.core.owner helper
# ---------------------------------------------------------------------------


async def _resolve_owner_entity_id(pool: Any) -> uuid.UUID | None:
    """Resolve the owner entity_id from a single pool.

    Delegates to the shared ``butlers.core.owner.resolve_owner_entity_id_two_step``
    helper, which reads the canonical owner role from ``public.entities``.

    Returns the entity UUID, or ``None`` when the owner cannot be found.
    """
    return await _resolve_owner_entity_id_two_step(pool)


# ---------------------------------------------------------------------------
# Core query — mirrors get_preferences MCP tool
# ---------------------------------------------------------------------------


async def _fetch_preferences(
    db: DatabaseManager,
    *,
    predicate: str | None,
) -> list[dict[str, Any]]:
    """Query active preference facts for the owner entity.

    Tries each available pool in turn. A missing ``facts`` relation is an
    optional absence only when the entire memory schema was absent at API
    startup; a source that disappeared later (or whose lifecycle is unknown)
    makes the preferences surface unavailable rather than falling through to
    a same-named public relation. Owner resolution uses the shared owner
    helper.

    Args:
        db: DatabaseManager providing access to all butler pools.
        predicate: Optional exact predicate filter
            (e.g. ``"preferences:general_timezone"``).

    Returns:
        List of preference dicts ordered by ``predicate ASC``.
        Returns empty list when no owner entity or no matching preferences are
        found across all available pools.

    Raises:
        HTTPException: 503 when a non-optional memory facts source cannot be
            queried.
    """
    pools = _all_pools(db)

    predicate_pattern = predicate if predicate is not None else "preferences:%"

    predicate_operator = "=" if predicate is not None else "LIKE"

    for name, pool in pools:
        # Resolve owner from this pool's shared public schema.
        try:
            owner_entity_id = await _resolve_owner_entity_id(pool)
        except Exception:
            logger.debug("Failed to resolve owner entity from pool; skipping", exc_info=True)
            continue

        if owner_entity_id is None:
            return []

        # Query facts from this pool's explicitly owned memory schema.  The
        # relation helper intentionally preserves legacy unqualified behavior
        # for pools without a configured schema.
        facts_relation = _memory_relation(db, name, "facts")
        sql = f"""
            SELECT
                f.predicate,
                f.content        AS value,
                f.scope,
                f.importance,
                f.permanence,
                f.created_at     AS updated_at,
                f.confidence,
                f.decay_rate,
                f.last_confirmed_at
            FROM {facts_relation} f
            WHERE f.entity_id = $1
              AND f.validity = 'active'
              AND f.predicate {predicate_operator} $2
            ORDER BY f.predicate ASC
        """
        try:
            if predicate is not None:
                rows = await pool.fetch(sql, owner_entity_id, predicate)
            else:
                rows = await pool.fetch(sql, owner_entity_id, predicate_pattern)
        except Exception as exc:
            if _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=_memory_schema_absent_at_start(db, name),
            ):
                logger.debug(
                    "Skipping preferences source for %s; memory schema was absent at startup",
                    name,
                    exc_info=True,
                )
                continue
            logger.warning(
                "Preferences facts source for %s is unavailable",
                name,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Preferences are unavailable because a memory facts source could not be queried"
                ),
            ) from exc

        now = datetime.now(UTC)
        results: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)

            confidence_raw = d.get("confidence")
            confidence = float(confidence_raw) if confidence_raw is not None else 1.0
            decay_rate_raw = d.get("decay_rate")
            decay_rate = float(decay_rate_raw) if decay_rate_raw is not None else 0.0
            last_confirmed_at = d.get("last_confirmed_at") or d.get("updated_at")

            if last_confirmed_at is not None and decay_rate > 0.0:
                if last_confirmed_at.tzinfo is None:
                    last_confirmed_at = last_confirmed_at.replace(tzinfo=UTC)
                days_elapsed = max(0.0, (now - last_confirmed_at).total_seconds() / 86400.0)
                effective_confidence = round(confidence * math.exp(-decay_rate * days_elapsed), 4)
            else:
                effective_confidence = round(confidence, 4)

            updated_at = d.get("updated_at")
            results.append(
                {
                    "predicate": d["predicate"],
                    "value": d["value"],
                    "scope": d["scope"],
                    "importance": float(d["importance"]),
                    "permanence": d["permanence"],
                    "updated_at": updated_at.isoformat() if updated_at else None,
                    "effective_confidence": effective_confidence,
                }
            )

        return results

    # No pool had a queryable facts table.
    return []


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[list[PreferenceEntry]])
async def get_preferences(
    predicate: str | None = Query(
        default=None,
        description=(
            "Optional exact predicate filter "
            "(e.g. ``preferences:general_timezone``). "
            "When omitted, all active preference facts are returned."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[PreferenceEntry]]:
    """Return active user-preference facts for the owner.

    Queries the memory module's ``facts`` table for rows where
    ``predicate LIKE 'preferences:%'`` and ``validity = 'active'``, scoped
    to the owner entity resolved from ``public.entities``. A memory schema
    that was absent at startup is skipped; source loss after startup is
    surfaced as unavailable rather than falling through to a public shadow
    relation.

    Returns 503 when no database pool is available or a required memory
    source cannot be queried.
    Returns an empty list when the owner has no recorded preferences.
    """
    rows = await _fetch_preferences(db, predicate=predicate)
    entries = [PreferenceEntry(**row) for row in rows]
    return ApiResponse[list[PreferenceEntry]](data=entries)
