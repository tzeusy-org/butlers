"""Memory preference tools — set and get user preferences as facts."""

from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _storage, get_embedding_engine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREFERENCE_PERMANENCE_DEFAULT = "stable"
PREFERENCE_IMPORTANCE_DEFAULT = 8.0
PREFERENCE_RETENTION_CLASS = "operational"
PREFERENCE_PREDICATE_PREFIX = "preferences:"
PREFERENCE_GENERAL_DOMAIN = "general"
PREFERENCE_GENERAL_SCOPE = "global"


# ---------------------------------------------------------------------------
# Owner entity resolution
# ---------------------------------------------------------------------------


async def _resolve_owner(pool: Pool) -> tuple[uuid.UUID, str]:
    """Resolve the owner entity from public.contacts / public.entities.

    Returns:
        Tuple of (entity_id, canonical_name).

    Raises:
        ValueError: When no owner entity can be resolved.
    """
    # Primary path: contacts table with entity_id FK.
    # Note: public.contacts.roles was dropped in core_016; roles are on public.entities.
    row = await pool.fetchrow(
        """
        SELECT e.id, e.canonical_name
        FROM public.contacts c
        JOIN public.entities e ON c.entity_id = e.id
        WHERE 'owner' = ANY(e.roles)
          AND c.entity_id IS NOT NULL
        LIMIT 1
        """
    )
    if row:
        return row["id"], row["canonical_name"]

    # Fallback: entities with owner role directly.
    row = await pool.fetchrow(
        """
        SELECT id, canonical_name
        FROM public.entities
        WHERE 'owner' = ANY(roles)
        LIMIT 1
        """
    )
    if row:
        return row["id"], row["canonical_name"]

    raise ValueError(
        "Owner entity could not be resolved. "
        "Ensure the butler has started up successfully (owner entity bootstrap) "
        "or create an owner contact via the identity setup workflow."
    )


# ---------------------------------------------------------------------------
# Scope derivation
# ---------------------------------------------------------------------------


def _derive_scope(predicate: str) -> str:
    """Derive the fact scope from the domain segment of a preferences predicate.

    Rules:
    - ``preferences:general_*``  → ``"global"``
    - ``preferences:<domain>_*`` → ``"<domain>"``

    Args:
        predicate: A validated preferences predicate starting with ``preferences:``.

    Returns:
        Scope string derived from the domain segment.
    """
    # Strip the "preferences:" prefix, then extract the domain (first segment
    # separated by underscore).
    remainder = predicate[len(PREFERENCE_PREDICATE_PREFIX) :]
    domain = remainder.split("_")[0]
    if domain == PREFERENCE_GENERAL_DOMAIN:
        return PREFERENCE_GENERAL_SCOPE
    return domain


# ---------------------------------------------------------------------------
# set_preference implementation
# ---------------------------------------------------------------------------


async def set_preference(
    pool: Pool,
    predicate: str,
    value: str,
    *,
    permanence: str = PREFERENCE_PERMANENCE_DEFAULT,
    importance: float = PREFERENCE_IMPORTANCE_DEFAULT,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a user preference as a fact with preference-appropriate defaults.

    Validates that the predicate uses the ``preferences:`` namespace, auto-resolves
    the owner entity, derives the scope from the predicate domain segment, and
    delegates to ``storage.store_fact`` with high-permanence defaults.

    Supersession is handled transparently by the storage layer: if an active fact
    for the same ``(entity_id, scope, predicate)`` already exists it is superseded
    and the response includes ``superseded_id``.

    Args:
        pool: asyncpg connection pool.
        predicate: Preference predicate in ``preferences:<domain>_<name>`` format.
        value: Preference value string (stored as fact content).
        permanence: Permanence level override (default ``"stable"``).
        importance: Importance score override (default ``8.0``).
        metadata: Optional JSONB metadata to merge into the stored fact.

    Returns:
        MCP-friendly dict with keys:
        - ``id`` (str): UUID of the stored fact.
        - ``superseded_id`` (str | None): UUID of the superseded fact, if any.
        - ``action`` (str): ``"created"`` or ``"updated"``.
        - ``predicate`` (str): Stored predicate.
        - ``scope`` (str): Derived scope.
        - ``owner_entity_id`` (str): Resolved owner entity UUID.

    Raises:
        ValueError: On predicate validation failure or owner resolution failure.
    """
    # Enforce the documented 'preferences:<domain>_<name>' format so that
    # _derive_scope always receives a predicate with a non-empty domain and name.
    has_prefix = predicate.startswith(PREFERENCE_PREDICATE_PREFIX)
    remainder = predicate[len(PREFERENCE_PREDICATE_PREFIX) :] if has_prefix else ""
    domain_and_name = remainder.split("_", 1)
    if (
        not has_prefix
        or not remainder
        or len(domain_and_name) != 2
        or not domain_and_name[0]
        or not domain_and_name[1]
    ):
        raise ValueError(
            f"Invalid preference predicate {predicate!r}. "
            "Preference predicates must start with 'preferences:' and use the format "
            "'preferences:<domain>_<name>' (e.g. 'preferences:travel_flight_seat'). "
            "Valid domains: travel, health, finance, relationship, home, general."
        )

    scope = _derive_scope(predicate)
    owner_entity_id, owner_name = await _resolve_owner(pool)

    embedding_engine = get_embedding_engine()
    result = await _storage.store_fact(
        pool,
        owner_name,
        predicate,
        value,
        embedding_engine,
        importance=importance,
        permanence=permanence,
        scope=scope,
        entity_id=owner_entity_id,
        retention_class=PREFERENCE_RETENTION_CLASS,
        metadata=metadata,
    )

    if isinstance(result, dict):
        fact_id = result["id"]
        superseded_id = result.get("supersedes_id")
    else:
        fact_id = result
        superseded_id = await pool.fetchval(
            "SELECT supersedes_id FROM facts WHERE id = $1",
            fact_id,
        )

    return {
        "id": str(fact_id),
        "superseded_id": str(superseded_id) if superseded_id else None,
        "action": "updated" if superseded_id else "created",
        "predicate": predicate,
        "scope": scope,
        "owner_entity_id": str(owner_entity_id),
    }


# ---------------------------------------------------------------------------
# get_preferences implementation
# ---------------------------------------------------------------------------


async def get_preferences(
    pool: Pool,
    *,
    scope: str | None = None,
    predicate_pattern: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve active user preferences for the owner entity.

    Queries facts where ``predicate LIKE 'preferences:%'`` and
    ``validity = 'active'`` for the owner entity, returning a simplified
    list optimised for LLM consumption.

    Args:
        pool: asyncpg connection pool.
        scope: Optional scope filter (e.g. ``"travel"``).
        predicate_pattern: Optional LIKE pattern (e.g. ``"preferences:health_%"``).

    Returns:
        List of preference dicts, ordered by ``predicate ASC``.
        Each entry has: ``predicate``, ``value``, ``scope``, ``importance``,
        ``permanence``, ``updated_at``, ``effective_confidence``.
        Returns empty list when no owner entity or no matching preferences exist.
    """
    try:
        owner_entity_id, _ = await _resolve_owner(pool)
    except ValueError:
        return []

    effective_predicate_pattern = predicate_pattern or "preferences:%"

    conditions = [
        "f.entity_id = $1",
        "f.validity = 'active'",
        "f.predicate LIKE $2",
    ]
    params: list[Any] = [owner_entity_id, effective_predicate_pattern]

    if scope is not None:
        conditions.append(f"f.scope = ${len(params) + 1}")
        params.append(scope)

    where_clause = " AND ".join(conditions)

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
        FROM facts f
        WHERE {where_clause}
        ORDER BY f.predicate ASC
    """

    rows = await pool.fetch(sql, *params)

    from datetime import UTC, datetime  # noqa: PLC0415 (late import ok in async function)

    now = datetime.now(UTC)
    results = []
    for row in rows:
        d = dict(row)
        # Compute effective confidence using standard exponential decay formula:
        # effective = confidence * exp(-decay_rate * days_elapsed)
        # Use explicit None checks to preserve 0.0 values (falsy check would coerce to default).
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

        results.append(
            {
                "predicate": d["predicate"],
                "value": d["value"],
                "scope": d["scope"],
                "importance": float(d["importance"]),
                "permanence": d["permanence"],
                "updated_at": d["updated_at"].isoformat() if d["updated_at"] else None,
                "effective_confidence": effective_confidence,
            }
        )

    return results
