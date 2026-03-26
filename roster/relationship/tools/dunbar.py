"""Dunbar scoring engine — tier assignment and urgency ranking.

Implements the Dunbar social layer model for the relationship butler.  Contacts
are ranked by a decay-weighted interaction score and assigned to concentric
tiers (5/15/50/150/500/1500).  An urgency formula combines overdue severity,
tier weight, and contextual signals (upcoming dates, pending gifts, positive
notes) to drive weekly reach-out suggestions.

Design decisions and rationale: openspec/changes/dunbar-tier-scoring/design.md
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (D5, D6)
# ---------------------------------------------------------------------------

#: The fixed Dunbar layer sizes in ascending order.
DUNBAR_TIERS: tuple[int, ...] = (5, 15, 50, 150, 500, 1500)

#: Valid tier values as a set (for validation in dunbar_tier_set).
VALID_TIERS: set[int] = set(DUNBAR_TIERS)

#: Dunbar layer boundaries as list-of-tuples: (tier_value, cumulative_rank_end).
#: Contacts ranked 1..5 → tier 5, ranks 6..15 → tier 15, etc.
DUNBAR_LAYERS: list[tuple[int, int]] = [
    (5, 5),
    (15, 15),
    (50, 50),
    (150, 150),
    (500, 500),
    (1500, 10_000_000),  # sentinel — everyone else
]

#: Cumulative upper rank boundaries for each tier.
#: Rank 1-5 → tier 5, 6-15 → tier 15, etc.
_TIER_UPPER_RANK: dict[int, int] = {5: 5, 15: 15, 50: 50, 150: 150, 500: 500}

#: Default expected contact cadence per tier (days).  Tier 1500 has no default.
TIER_CADENCE: dict[int, int] = {
    5: 14,
    15: 21,
    50: 45,
    150: 120,
    500: 270,
}

#: Tier cadences including tier 1500 (None = never suggested).
TIER_CADENCES: dict[int, int | None] = {**TIER_CADENCE, 1500: None}

#: Tier weight for the urgency formula.
TIER_WEIGHT: dict[int, float] = {
    5: 5.0,
    15: 3.0,
    50: 2.0,
    150: 1.0,
    500: 0.5,
}

#: Tier weights including tier 1500 (0.0 = excluded from urgency).
TIER_WEIGHTS: dict[int, float] = {**TIER_WEIGHT, 1500: 0.0}

#: Exponential decay lambda: ln(2) / 30-day half-life.
_LAMBDA: float = math.log(2) / 30.0

#: Hysteresis buffer: a contact must fall this many ranks *beyond* a tier
#: boundary before being moved down.
_HYSTERESIS: int = 2

#: Context bonus amounts.
_BONUS_UPCOMING_DATE: float = 2.0
_BONUS_PENDING_GIFT: float = 1.0
_BONUS_POSITIVE_NOTE: float = 0.5

#: Days window for "upcoming date" context bonus.
_UPCOMING_DATE_DAYS: int = 14


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rank_to_tier(rank: int) -> int:
    """Map a 1-based rank to the Dunbar tier it falls in."""
    if rank <= 5:
        return 5
    if rank <= 15:
        return 15
    if rank <= 50:
        return 50
    if rank <= 150:
        return 150
    if rank <= 500:
        return 500
    return 1500


def _rank_to_tier_with_hysteresis(current_rank: int, previous_tier: int | None) -> int:
    """Map rank to tier, applying downward hysteresis.

    If the contact previously held a tier, they must exceed the tier boundary
    by ``_HYSTERESIS`` positions before moving down.  Upward transitions are
    immediate.

    If ``previous_tier`` is None (no prior tier), no hysteresis is applied.
    """
    natural_tier = _rank_to_tier(current_rank)

    if previous_tier is None:
        return natural_tier

    if natural_tier > previous_tier:
        # Downward move — check hysteresis
        # Find the upper boundary rank for previous_tier
        upper = _TIER_UPPER_RANK.get(previous_tier)
        if upper is None:
            # previous_tier is 1500 — can only go down if rank is somehow > 500
            return natural_tier
        if current_rank <= upper + _HYSTERESIS:
            return previous_tier  # Stay in current tier
        return natural_tier

    # Upward move or no change — apply immediately
    return natural_tier


# ---------------------------------------------------------------------------
# Core scoring function (D1, D2)
# ---------------------------------------------------------------------------


async def compute_dunbar_scores(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Compute decay scores for all listed, entity-linked contacts.

    Returns a list of dicts ordered by score descending:
        {contact_id, entity_id, score, days_since_last}

    Only contacts with ``listed=true`` and a non-NULL ``entity_id`` are
    included.  Contacts with no interaction facts receive score=0.0.

    The decay formula is::

        score = sum(exp(-lambda * days_since_interaction_i))

    where ``lambda = ln(2) / 30`` (30-day half-life).

    Spec reference: D2 — exponential decay score from interaction facts.
    """
    rows = await pool.fetch(
        """
        SELECT
            c.id          AS contact_id,
            c.entity_id   AS entity_id,
            COALESCE(
                SUM(
                    CASE
                        WHEN f.valid_at IS NOT NULL
                        THEN EXP(
                            -$1::float
                            * GREATEST(
                                EXTRACT(EPOCH FROM (now() - f.valid_at)) / 86400.0,
                                0.0
                            )
                        )
                        ELSE NULL
                    END
                ),
                0.0
            )              AS score,
            MAX(f.valid_at) AS last_interaction_at
        FROM contacts c
        LEFT JOIN facts f
            ON  f.subject   = 'contact:' || c.id::text
            AND f.predicate = 'interaction'
            AND f.scope     = 'relationship'
            AND f.validity  = 'active'
        WHERE c.listed    = true
          AND c.entity_id IS NOT NULL
        GROUP BY c.id, c.entity_id
        ORDER BY score DESC
        """,
        _LAMBDA,
    )
    return [
        {
            "contact_id": row["contact_id"],
            "entity_id": row["entity_id"],
            "score": float(row["score"]),
            "last_interaction_at": row["last_interaction_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Tier ranking (D3, D4)
# ---------------------------------------------------------------------------


async def _fetch_overrides(pool: asyncpg.Pool) -> dict[uuid.UUID, int]:
    """Return a mapping of entity_id → override_tier for all active overrides."""
    rows = await pool.fetch(
        """
        SELECT entity_id, content
        FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope     = 'relationship'
          AND validity  = 'active'
          AND entity_id IS NOT NULL
        """
    )
    result: dict[uuid.UUID, int] = {}
    for row in rows:
        try:
            tier = int(row["content"])
            if tier in DUNBAR_TIERS:
                result[row["entity_id"]] = tier
        except (ValueError, TypeError):
            pass
    return result


def get_tier_ranking(
    scores: list[dict[str, Any]],
    overrides: dict[uuid.UUID, int] | None = None,
    previous_tiers: dict[uuid.UUID, int] | None = None,
) -> list[dict[str, Any]]:
    """Assign Dunbar tiers to a scored contact list.

    Args:
        scores: Output of ``compute_dunbar_scores`` — list of
            {contact_id, entity_id, score, ...} ordered by score desc.
        overrides: Mapping of entity_id → manually pinned tier.  Contacts
            with an override are placed in that tier but still sorted by
            score within it.
        previous_tiers: Mapping of entity_id → current tier for hysteresis
            computation.  Pass None to skip hysteresis (e.g., first run).

    Returns:
        List of dicts with added fields: ``dunbar_tier``, ``dunbar_score``,
        ``dunbar_rank``, ``dunbar_tier_override`` (bool).
    """
    if overrides is None:
        overrides = {}
    if previous_tiers is None:
        previous_tiers = {}

    # Only contacts with score > 0 participate in rank-based tier assignment.
    # Zero-score contacts (no interactions) are always tier 1500 unless overridden.
    scored_entries = [e for e in scores if e["score"] > 0.0]
    zero_entries = [e for e in scores if e["score"] == 0.0]

    result: list[dict[str, Any]] = []

    for rank_0, entry in enumerate(scored_entries, start=1):
        entity_id = entry["entity_id"]
        score = entry["score"]

        # Manual override takes priority
        if entity_id in overrides:
            tier = overrides[entity_id]
            is_override = True
        else:
            prev = previous_tiers.get(entity_id)
            tier = _rank_to_tier_with_hysteresis(rank_0, prev)
            is_override = False

        result.append(
            {
                **entry,
                "dunbar_rank": rank_0,
                "dunbar_tier": tier,
                "dunbar_score": round(score, 2),
                "dunbar_tier_override": is_override,
            }
        )

    # Assign zero-score contacts to tier 1500 (or their override)
    zero_rank = len(scored_entries) + 1
    for entry in zero_entries:
        entity_id = entry["entity_id"]
        score = entry["score"]
        if entity_id in overrides:
            tier = overrides[entity_id]
            is_override = True
        else:
            tier = 1500
            is_override = False
        result.append(
            {
                **entry,
                "dunbar_rank": zero_rank,
                "dunbar_tier": tier,
                "dunbar_score": round(score, 2),
                "dunbar_tier_override": is_override,
            }
        )

    return result


async def compute_tier_ranking(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Compute and return the full Dunbar tier ranking for all eligible contacts.

    Convenience wrapper that calls ``compute_dunbar_scores`` then
    ``get_tier_ranking`` with database-fetched overrides.

    Returns the same shape as ``get_tier_ranking``.
    """
    scores = await compute_dunbar_scores(pool)
    overrides = await _fetch_overrides(pool)
    return get_tier_ranking(scores, overrides)


# ---------------------------------------------------------------------------
# Urgency ranking (D6)
# ---------------------------------------------------------------------------


async def compute_urgency(
    pool: asyncpg.Pool,
    tier_ranking: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compute urgency scores for all contacts eligible for reach-out suggestions.

    Formula::

        urgency = (days_overdue / tier_cadence) * tier_weight + context_bonus

    Contacts not yet overdue have ``days_overdue=0`` and may still surface
    via a non-zero context bonus.  Tier 1500 contacts are excluded unless
    they have ``stay_in_touch_days`` set.

    Context bonuses (additive):
    - +2.0  upcoming important date within 14 days
    - +1.0  pending gift (active gift fact, status not 'given')
    - +0.5  most recent note fact has positive emotion metadata

    Args:
        pool: asyncpg pool connected to the relationship schema.
        tier_ranking: Pre-computed tier ranking from ``compute_tier_ranking``.
            If None, will be computed internally.

    Returns:
        List of dicts ordered by ``urgency`` descending.  Each entry includes:
        contact_id, entity_id, dunbar_tier, dunbar_score, dunbar_tier_override,
        days_since_last, days_overdue, effective_cadence, tier_weight,
        context_bonus, urgency.
    """
    if tier_ranking is None:
        tier_ranking = await compute_tier_ranking(pool)

    if not tier_ranking:
        return []

    # Fetch stay_in_touch_days for all contacts in the ranking
    contact_ids = [entry["contact_id"] for entry in tier_ranking]
    sitd_rows = await pool.fetch(
        """
        SELECT id, stay_in_touch_days
        FROM contacts
        WHERE id = ANY($1::uuid[])
        """,
        contact_ids,
    )
    sitd_map: dict[uuid.UUID, int | None] = {
        row["id"]: row["stay_in_touch_days"] for row in sitd_rows
    }

    # Fetch context signals in one pass per signal type
    # -- upcoming important dates within 14 days --
    upcoming_cids = await _upcoming_date_contact_ids(pool, contact_ids)
    # -- pending gifts --
    pending_gift_cids = await _pending_gift_contact_ids(pool, contact_ids)
    # -- most recent note with positive emotion --
    positive_note_cids = await _positive_note_contact_ids(pool, contact_ids)

    results: list[dict[str, Any]] = []

    for entry in tier_ranking:
        contact_id = entry["contact_id"]
        entity_id = entry["entity_id"]
        tier = entry["dunbar_tier"]
        last_at = entry.get("last_interaction_at")

        # Compute days since last interaction
        if last_at is None:
            days_since: float = float("inf")
        else:
            now = datetime.now(UTC)
            # Ensure last_at is tz-aware
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=UTC)
            delta = (now - last_at).total_seconds() / 86400.0
            days_since = max(delta, 0.0)

        # Determine effective cadence
        stay_days = sitd_map.get(contact_id)
        if stay_days is not None:
            effective_cadence: int | None = stay_days
        elif tier == 1500:
            effective_cadence = None  # No proactive suggestions
        else:
            effective_cadence = TIER_CADENCE[tier]

        # Tier 1500 exclusion (unless stay_in_touch_days overrides)
        if effective_cadence is None:
            continue

        # Compute days_overdue
        if days_since == float("inf"):
            # No interactions ever — fully overdue from day 0
            days_overdue: float = float(effective_cadence)
        else:
            days_overdue = max(days_since - effective_cadence, 0.0)

        # Tier weight (tier 1500 contacts with stay_in_touch_days use 0.5)
        weight = TIER_WEIGHT.get(tier, 0.5)

        # Context bonus
        context_bonus = 0.0
        if contact_id in upcoming_cids:
            context_bonus += _BONUS_UPCOMING_DATE
        if contact_id in pending_gift_cids:
            context_bonus += _BONUS_PENDING_GIFT
        if contact_id in positive_note_cids:
            context_bonus += _BONUS_POSITIVE_NOTE

        # Urgency — contacts not yet overdue (days_overdue=0) rank by context only
        urgency = (days_overdue / effective_cadence) * weight + context_bonus

        # Filter: exclude non-overdue contacts with zero context bonus
        # (spec requirement: "non-overdue zero-context contacts MUST be filtered OUT of results")
        if days_overdue == 0.0 and context_bonus == 0.0:
            continue

        results.append(
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": tier,
                "dunbar_score": entry["dunbar_score"],
                "dunbar_tier_override": entry.get("dunbar_tier_override", False),
                "last_interaction_at": entry.get("last_interaction_at"),
                "days_since_last": None if days_since == float("inf") else round(days_since, 1),
                "days_overdue": round(days_overdue, 1),
                "effective_cadence": effective_cadence,
                "tier_weight": weight,
                "context_bonus": context_bonus,
                "urgency": round(urgency, 4),
            }
        )

    # Sort by urgency descending
    results.sort(key=lambda r: r["urgency"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Context signal queries (internal)
# ---------------------------------------------------------------------------


async def _upcoming_date_contact_ids(
    pool: asyncpg.Pool,
    contact_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Return contact_ids with an important date within the next 14 days."""
    if not contact_ids:
        return set()
    rows = await pool.fetch(
        """
        WITH params AS (
            -- Precompute leap-year flags to avoid repeating the formula.
            SELECT
                EXTRACT(YEAR FROM now())::int AS this_year,
                -- A year is a leap year if: (div by 4 AND NOT div by 100) OR div by 400
                (EXTRACT(YEAR FROM now())::int % 4 = 0
                 AND (EXTRACT(YEAR FROM now())::int % 100 != 0
                      OR EXTRACT(YEAR FROM now())::int % 400 = 0)
                ) AS this_year_is_leap,
                ((EXTRACT(YEAR FROM now())::int + 1) % 4 = 0
                 AND ((EXTRACT(YEAR FROM now())::int + 1) % 100 != 0
                      OR (EXTRACT(YEAR FROM now())::int + 1) % 400 = 0)
                ) AS next_year_is_leap
        ),
        candidate AS (
            -- Build the nearest future (or today) occurrence of each anniversary.
            -- Skip Feb-29 rows when the current year is not a leap year.
            SELECT
                d.contact_id,
                d.month,
                d.day,
                CASE
                    WHEN make_date(p.this_year, d.month, d.day) >= now()::date
                    THEN p.this_year
                    ELSE p.this_year + 1
                END AS yr,
                p.this_year + 1 AS next_year,
                p.next_year_is_leap
            FROM important_dates d, params p
            WHERE d.contact_id = ANY($1::uuid[])
              AND NOT (d.month = 2 AND d.day = 29 AND NOT p.this_year_is_leap)
        )
        SELECT DISTINCT contact_id
        FROM candidate
        WHERE NOT (month = 2 AND day = 29 AND yr = next_year AND NOT next_year_is_leap)
          AND make_date(yr, month, day)
              BETWEEN now()::date AND (now() + INTERVAL '14 days')::date
        """,
        contact_ids,
    )
    return {row["contact_id"] for row in rows}


async def _pending_gift_contact_ids(
    pool: asyncpg.Pool,
    contact_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Return contact_ids with a pending gift (active gift fact, status not 'given')."""
    if not contact_ids:
        return set()
    # Gifts are stored as facts with predicate='gift', scope='relationship'.
    # Status is in metadata->>'status'.  Pending = status in (idea, purchased, wrapped).
    rows = await pool.fetch(
        """
        SELECT DISTINCT
            -- subject is 'contact:{uuid}:gift:{slug}' — extract the UUID part
            (regexp_match(subject, 'contact:([0-9a-f-]+):'))[1]::uuid AS contact_id
        FROM facts
        WHERE subject ~ ('^contact:(' || array_to_string(
                    ARRAY(SELECT id::text FROM contacts WHERE id = ANY($1::uuid[])),
                    '|'
              ) || '):')
          AND predicate  = 'gift'
          AND scope      = 'relationship'
          AND validity   = 'active'
          AND metadata->>'status' NOT IN ('given', 'thanked')
        """,
        contact_ids,
    )
    result: set[uuid.UUID] = set()
    for row in rows:
        if row["contact_id"] is not None:
            result.add(row["contact_id"])
    return result


async def _positive_note_contact_ids(
    pool: asyncpg.Pool,
    contact_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Return contact_ids whose most recent note has positive emotional context."""
    if not contact_ids:
        return set()
    # Notes are stored as facts with predicate='contact_note', scope='relationship'.
    # Emotion is in metadata->>'emotion'.
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (subject)
            -- subject = 'contact:{uuid}'
            (regexp_match(subject, 'contact:([0-9a-f-]+)$'))[1]::uuid AS contact_id,
            metadata->>'emotion' AS emotion
        FROM facts
        WHERE subject   = ANY(
                    ARRAY(
                        SELECT 'contact:' || id::text
                        FROM contacts
                        WHERE id = ANY($1::uuid[])
                    )
              )
          AND predicate = 'contact_note'
          AND scope     = 'relationship'
          AND validity  = 'active'
        ORDER BY subject, valid_at DESC NULLS LAST, created_at DESC
        """,
        contact_ids,
    )
    _positive_emotions = {"happy", "grateful", "excited", "positive", "joy", "love"}
    result: set[uuid.UUID] = set()
    for row in rows:
        if row["contact_id"] is not None:
            emotion = (row["emotion"] or "").lower()
            if emotion in _positive_emotions:
                result.add(row["contact_id"])
    return result


# ---------------------------------------------------------------------------
# MCP tool: dunbar_tier_set (D4)
# ---------------------------------------------------------------------------


async def dunbar_tier_set(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    tier: int | None,
) -> dict[str, Any]:
    """Set or clear a manual Dunbar tier override for a contact.

    - tier in VALID_TIERS: stores/supersedes dunbar_tier_override SPO fact
    - tier = None: retracts any active override (reverts to rank-based)

    Returns dict with contact_id, entity_id, tier, action ('set' | 'cleared').

    Raises:
        ValueError: If tier is not in VALID_TIERS (and not None), or if
            contact not found / has no entity_id.
    """
    if tier is not None and tier not in VALID_TIERS:
        valid_str = ", ".join(str(t) for t in sorted(VALID_TIERS))
        raise ValueError(
            f"Invalid tier value {tier!r}. "
            f"Valid Dunbar tier values are: {valid_str}. "
            "Pass tier=None to clear the override."
        )

    row = await pool.fetchrow("SELECT id, entity_id FROM contacts WHERE id = $1", contact_id)
    if row is None:
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    entity_id = row["entity_id"]
    if entity_id is None:
        raise ValueError(
            f"Contact {contact_id} has no linked entity. "
            "Dunbar tier overrides require a linked entity. "
            "The contact must be created via contact_create to get an entity."
        )

    entity_id_str = str(entity_id)

    # Retract any existing active overrides and optionally insert a new one atomically.
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Bulk-retract all active overrides for this entity in one statement.
            await conn.execute(
                """
                UPDATE facts
                SET validity = 'retracted'
                WHERE predicate = 'dunbar_tier_override'
                  AND scope = 'relationship'
                  AND validity = 'active'
                  AND entity_id = $1::uuid
                """,
                entity_id_str,
            )

            if tier is None:
                return {
                    "contact_id": str(contact_id),
                    "entity_id": entity_id_str,
                    "action": "cleared",
                    "message": (
                        "Dunbar tier override cleared. Contact will use rank-based tier assignment."
                    ),
                }

            # Store new override fact
            await conn.execute(
                """
                INSERT INTO facts (
                    subject,
                    predicate,
                    content,
                    scope,
                    entity_id,
                    validity,
                    permanence
                ) VALUES (
                    $1, 'dunbar_tier_override', $2, 'relationship',
                    $3::uuid, 'active', 'permanent'
                )
                """,
                f"contact:{contact_id}",
                str(tier),
                entity_id_str,
            )

    return {
        "contact_id": str(contact_id),
        "entity_id": entity_id_str,
        "action": "set",
        "tier": tier,
        "message": (
            f"Dunbar tier override set to {tier}. "
            f"Contact is pinned to tier {tier} regardless of computed rank."
        ),
    }


# ---------------------------------------------------------------------------
# Per-contact Dunbar lookup (convenience for contact_get enrichment)
# ---------------------------------------------------------------------------


async def get_contact_dunbar(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> dict[str, Any]:
    """Get Dunbar tier and score for a single contact.

    Returns dict with dunbar_tier, dunbar_score, dunbar_tier_override.
    Falls back to tier 1500, score 0.0 if contact has no entity_id or
    the contact is not listed.

    Implementation uses ``compute_tier_ranking`` (one pass over all contacts)
    and looks up the result by contact_id.  This avoids a double-compute
    that would occur if score were computed per-contact and tier derived
    separately.
    """
    row = await pool.fetchrow(
        "SELECT id, entity_id FROM contacts WHERE id = $1",
        contact_id,
    )
    if row is None or row["entity_id"] is None:
        return {"dunbar_tier": 1500, "dunbar_score": 0.0, "dunbar_tier_override": False}

    # Single pass: compute full tier ranking and look up this contact.
    ranked = await compute_tier_ranking(pool)
    for entry in ranked:
        if entry["contact_id"] == contact_id:
            return {
                "dunbar_tier": entry["dunbar_tier"],
                "dunbar_score": entry["dunbar_score"],
                "dunbar_tier_override": entry.get("dunbar_tier_override", False),
            }

    # Contact not in ranked list (not listed, or zero interactions and not scored)
    return {"dunbar_tier": 1500, "dunbar_score": 0.0, "dunbar_tier_override": False}


# ---------------------------------------------------------------------------
# Tier-aware overdue contacts (D5)
# ---------------------------------------------------------------------------


async def contacts_overdue_with_tiers(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return overdue contacts using tier-aware cadences.

    Effective cadence per contact:
    - If stay_in_touch_days is set: use that value (explicit override)
    - Otherwise: use the Dunbar tier's default cadence
      (tier 5=14d, 15=21d, 50=45d, 150=120d, 500=270d, 1500=never)

    Tier 1500 contacts with no stay_in_touch_days are excluded.
    Archived contacts (listed=false) are excluded.
    Contacts with no interactions and an effective cadence are always overdue.

    Returns contacts enriched with dunbar_tier, dunbar_score,
    effective_cadence, and days_since_last_interaction.
    """
    from butlers.tools.relationship.contacts import _parse_contact

    contact_rows = await pool.fetch(
        """
        SELECT
            c.*,
            MAX(f.valid_at) AS last_interaction_at,
            CASE
                WHEN MAX(f.valid_at) IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - MAX(f.valid_at))) / 86400.0
            END AS days_since_last_interaction
        FROM contacts c
        LEFT JOIN facts f
            ON f.subject = 'contact:' || c.id::text
           AND f.predicate = 'interaction'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE c.listed = true
        GROUP BY c.id
        ORDER BY c.first_name, c.last_name, c.nickname
        """
    )

    if not contact_rows:
        return []

    # Batch-compute Dunbar scores + tiers
    all_scores = await compute_dunbar_scores(pool)
    overrides = await _fetch_overrides(pool)
    ranked = get_tier_ranking(all_scores, overrides)
    dunbar_by_cid: dict[uuid.UUID, dict[str, Any]] = {
        entry["contact_id"]: entry for entry in ranked
    }

    results: list[dict[str, Any]] = []
    for row in contact_rows:
        contact = _parse_contact(row)
        cid = contact["id"]
        days_since = row["days_since_last_interaction"]

        dunbar_info = dunbar_by_cid.get(cid, {"dunbar_tier": 1500, "dunbar_score": 0.0})
        dunbar_tier = dunbar_info["dunbar_tier"]
        dunbar_score = dunbar_info.get("dunbar_score", 0.0)

        stay_in_touch = contact.get("stay_in_touch_days")
        if stay_in_touch is not None:
            effective_cadence: int | None = stay_in_touch
        else:
            effective_cadence = TIER_CADENCES.get(dunbar_tier)

        if effective_cadence is None:
            continue

        if days_since is None:
            is_overdue = True
        else:
            is_overdue = float(days_since) > float(effective_cadence)

        if not is_overdue:
            continue

        contact["dunbar_tier"] = dunbar_tier
        contact["dunbar_score"] = dunbar_score
        contact["effective_cadence"] = effective_cadence
        contact["days_since_last_interaction"] = (
            round(float(days_since), 1) if days_since is not None else None
        )
        results.append(contact)

    return results
