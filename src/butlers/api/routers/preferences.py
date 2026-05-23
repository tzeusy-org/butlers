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


def _all_pools(db: DatabaseManager) -> list[Any]:
    """Return all available butler pools, skipping missing ones.

    Raises HTTPException(503) when no pool is available at all.
    """
    pools = []
    for name in sorted(db.butler_names):
        try:
            pools.append(db.pool(name))
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
    helper which implements the canonical two-step fallback:
    1. ``public.contacts JOIN public.entities`` (primary path).
    2. ``public.entities`` directly (fallback for installs where the owner
       entity exists without a contact row, e.g. early bootstrap states).

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

    Tries each available pool in turn, skipping pools that lack the memory
    ``facts`` table (matching the fan-out behaviour of the memory router).
    Owner resolution uses the same two-step fallback as the MCP tool.

    Args:
        db: DatabaseManager providing access to all butler pools.
        predicate: Optional exact predicate filter
            (e.g. ``"preferences:general_timezone"``).

    Returns:
        List of preference dicts ordered by ``predicate ASC``.
        Returns empty list when no owner entity or no matching preferences are
        found across all available pools.
    """
    pools = _all_pools(db)

    predicate_pattern = predicate if predicate is not None else "preferences:%"

    if predicate is not None:
        sql = """
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
            FROM facts f
            WHERE f.entity_id = $1
              AND f.validity = 'active'
              AND f.predicate = $2
            ORDER BY f.predicate ASC
        """
    else:
        sql = """
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
            FROM facts f
            WHERE f.entity_id = $1
              AND f.validity = 'active'
              AND f.predicate LIKE $2
            ORDER BY f.predicate ASC
        """

    for pool in pools:
        # Resolve owner from this pool's shared public schema.
        try:
            owner_entity_id = await _resolve_owner_entity_id(pool)
        except Exception:
            logger.debug("Failed to resolve owner entity from pool; skipping", exc_info=True)
            continue

        if owner_entity_id is None:
            return []

        # Query facts from this pool's per-butler schema.
        try:
            if predicate is not None:
                rows = await pool.fetch(sql, owner_entity_id, predicate)
            else:
                rows = await pool.fetch(sql, owner_entity_id, predicate_pattern)
        except Exception:
            logger.debug(
                "Skipping pool for preferences query (pool may lack facts table)",
                exc_info=True,
            )
            continue

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
    to the owner entity resolved from ``public.contacts`` (or
    ``public.entities`` directly as a fallback). Skips pools that lack the
    memory schema, matching the fan-out behaviour of the memory router.

    Returns 503 when no database pool is available.
    Returns an empty list when the owner has no recorded preferences.
    """
    rows = await _fetch_preferences(db, predicate=predicate)
    entries = [PreferenceEntry(**row) for row in rows]
    return ApiResponse[list[PreferenceEntry]](data=entries)
