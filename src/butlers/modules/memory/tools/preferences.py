"""Memory preferences tools — retrieve user preferences from the facts store."""

from __future__ import annotations

import datetime as _dt
import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)


def _compute_effective_confidence(row: dict[str, Any]) -> float:
    """Compute effective (decayed) confidence using the standard decay formula.

    Formula: confidence * exp(-decay_rate * days_elapsed)
    where days_elapsed = (now - last_confirmed_at or created_at) / 86400.

    When decay_rate is 0.0 (permanent), returns confidence unchanged.
    When last_confirmed_at and created_at are both None, returns 0.0.

    Args:
        row: Dict with keys: confidence, decay_rate, last_confirmed_at, created_at.

    Returns:
        Effective confidence float (0.0–1.0).
    """
    confidence = row.get("confidence")
    if confidence is None:
        confidence = 1.0
    decay_rate = row.get("decay_rate")
    if decay_rate is None:
        decay_rate = 0.0

    if decay_rate == 0.0:
        return confidence

    anchor = row.get("last_confirmed_at") or row.get("created_at")
    if anchor is None:
        return 0.0

    now = datetime.now(UTC)
    days_elapsed = max((now - anchor).total_seconds() / 86400.0, 0.0)
    return confidence * math.exp(-decay_rate * days_elapsed)


async def get_preferences(
    pool: Pool,
    *,
    scope: str | None = None,
    predicate_pattern: str | None = None,
    tenant_id: str = "owner",
) -> list[dict[str, Any]]:
    """Retrieve all active user preferences for the owner entity.

    Queries facts WHERE predicate LIKE 'preferences:%' AND validity = 'active'
    AND entity_id matches the owner entity resolved from shared.entities
    AND tenant_id matches the given tenant scope.

    Args:
        pool: asyncpg connection pool.
        scope: Optional filter on the scope column (exact match, e.g. 'travel').
        predicate_pattern: Optional SQL LIKE pattern for the predicate column
            (e.g. 'preferences:health_%'). Must start with 'preferences:'.
            When omitted, defaults to 'preferences:%'. Invalid patterns that do
            not start with 'preferences:' are coerced to the default.
        tenant_id: Tenant scope for multi-tenant isolation (default 'owner').
            Filters facts by tenant_id to prevent cross-tenant data exposure.

    Returns:
        List of preference dicts ordered by predicate ASC. Each dict contains:
        - predicate (str)
        - value (str) — from the content column
        - scope (str)
        - importance (float)
        - permanence (str)
        - effective_confidence (float) — computed via decay formula
        - updated_at (str) — ISO-8601 timestamp from created_at
    """
    # Resolve owner entity_id from shared.entities
    owner_entity_id = await _resolve_owner_entity_id(pool)
    if owner_entity_id is None:
        return []

    # Validate and coerce predicate_pattern to enforce 'preferences:' prefix
    effective_pattern = predicate_pattern or "preferences:%"
    if not effective_pattern.startswith("preferences:"):
        logger.debug(
            "get_preferences: invalid predicate_pattern %r; coercing to default 'preferences:%%'",
            effective_pattern,
        )
        effective_pattern = "preferences:%"

    # Build query with optional filters
    params: list[Any] = [owner_entity_id, tenant_id, effective_pattern]
    conditions = [
        "f.entity_id = $1",
        "f.tenant_id = $2",
        "f.validity = 'active'",
        "f.predicate LIKE $3",
    ]

    # Optional scope filter
    if scope is not None:
        params.append(scope)
        conditions.append(f"f.scope = ${len(params)}")

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT
            f.predicate,
            f.content,
            f.scope,
            f.importance,
            f.permanence,
            f.confidence,
            f.decay_rate,
            f.last_confirmed_at,
            f.created_at
        FROM facts f
        WHERE {where_clause}
        ORDER BY f.predicate ASC
    """

    try:
        rows = await pool.fetch(sql, *params)
    except Exception:
        logger.debug("get_preferences query failed", exc_info=True)
        return []

    results = []
    for row in rows:
        row_dict = dict(row)
        eff_conf = _compute_effective_confidence(row_dict)
        created_at = row_dict.get("created_at")
        if isinstance(created_at, _dt.datetime):
            updated_at = created_at.isoformat()
        else:
            updated_at = str(created_at or "")
        results.append(
            {
                "predicate": row_dict["predicate"],
                "value": row_dict["content"],
                "scope": row_dict["scope"],
                "importance": row_dict["importance"],
                "permanence": row_dict["permanence"],
                "effective_confidence": round(eff_conf, 6),
                "updated_at": updated_at,
            }
        )
    return results


async def _resolve_owner_entity_id(pool: Pool) -> Any | None:
    """Resolve the owner entity UUID from shared.entities.

    Looks up entities WHERE 'owner' = ANY(roles) and returns the id
    of the first match.

    Returns:
        UUID of the owner entity, or None if not found.
    """
    sql = """
        SELECT id AS entity_id
        FROM shared.entities
        WHERE 'owner' = ANY(roles)
        LIMIT 1
    """
    try:
        row = await pool.fetchrow(sql)
        return row["entity_id"] if row else None
    except Exception:
        logger.debug("Owner entity resolution failed", exc_info=True)
        return None
