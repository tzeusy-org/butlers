"""Contact resolution — resolve name strings to contact IDs.

When multiple candidates exist, salience scores from relationship domain data
are passed as context_hints.domain_scores to entity_resolve, which combines
them with generic entity scoring to produce a ranked candidate list.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_NONE = "none"

# Relationship type weights for salience scoring
RELATIONSHIP_WEIGHTS = {
    "spouse": 50,
    "partner": 50,
    "parent": 30,
    "child": 30,
    "sibling": 30,
    "close friend": 20,
    "best friend": 20,
    "friend": 10,
    "colleague": 5,
    "acquaintance": 2,
}

# Group type weights for salience scoring
GROUP_TYPE_WEIGHTS = {
    "couple": 15,
    "family": 10,
    "friends": 5,
    "team": 3,
}


def _display_name_from_row(row: asyncpg.Record | dict[str, Any]) -> str:
    first = (row.get("first_name") if isinstance(row, dict) else row["first_name"]) or ""
    last = (row.get("last_name") if isinstance(row, dict) else row["last_name"]) or ""
    nickname = (row.get("nickname") if isinstance(row, dict) else row["nickname"]) or ""
    full = " ".join(part for part in [first, last] if part).strip()
    return full or nickname or first or "Unknown"


def _generate_inferred_reason(candidate: dict[str, Any]) -> str:
    """Generate a human-readable reason for why a candidate was inferred.

    Analyzes the candidate's salience metadata to identify dominant signals
    like relationship type and interaction frequency.
    """
    reasons = []

    # Extract relationship type if present
    relationship = candidate.get("_relationship_type")
    if relationship:
        reasons.append(relationship)

    # Extract interaction frequency if present
    interaction_count = candidate.get("_interaction_count", 0)
    if interaction_count > 0:
        if interaction_count >= 10:
            reasons.append("very frequent contact")
        elif interaction_count >= 5:
            reasons.append("frequent contact")
        else:
            reasons.append("recent contact")

    # Fallback to generic reason if no specific signals
    if not reasons:
        return "highest salience score"

    return ", ".join(reasons)


async def contact_resolve(
    pool: asyncpg.Pool,
    name: str,
    context: str | None = None,
    *,
    memory_pool: asyncpg.Pool | None = None,
    memory_tenant_id: str = "relationship",
) -> dict[str, Any]:
    """Resolve a name string to a contact_id.

    Resolution strategy (in order):
    1. Exact full-name match (case-insensitive) -> HIGH confidence, single contact_id.
    2. Multiple candidates -> compute salience scores, call entity_resolve (when memory_pool
       is provided and contacts have entity_ids), apply 30-point gap threshold.
    3. Partial match (first name or last name, case-insensitive) -> MEDIUM confidence.
    4. Context-boosted: if context is provided and a candidate's details/notes match,
       boost that candidate's relevance.
    5. No match -> {contact_id: None, confidence: "none", candidates: []}.

    When ``memory_pool`` is provided, salience scores are passed as
    ``context_hints.domain_scores`` to ``entity_resolve``, which combines the
    relationship-domain signals with its own generic scoring (name-match quality
    + graph neighbourhood) to produce the final ranked candidate list.
    Entity resolution is fail-open: if it fails the local scoring is used instead.

    Args:
        pool: asyncpg connection pool for the relationship butler database.
        name: The name string to resolve.
        context: Optional free-text context for boosting candidate scores.
        memory_pool: Optional asyncpg pool for the memory module database.
            When provided, entity_resolve is called with salience domain_scores.
        memory_tenant_id: Tenant ID used when querying the memory module.

    Returns:
        {
            "contact_id": uuid | None,
            "confidence": "high" | "medium" | "none",
            "candidates": [
                {
                    "contact_id": uuid,
                    "name": str,
                    "confidence": str,
                    "score": int | float,
                    "salience": int
                }
            ],
            "inferred": bool,
            "inferred_reason": str | None
        }
    """
    name = name.strip()
    if not name:
        return {
            "contact_id": None,
            "confidence": CONFIDENCE_NONE,
            "candidates": [],
            "inferred": False,
            "inferred_reason": None,
        }

    # Step 1: Exact match (case-insensitive, listed contacts only)
    exact_rows = await pool.fetch(
        """
        SELECT id, first_name, last_name, nickname, company, job_title, metadata, entity_id
        FROM contacts
        WHERE listed = true
          AND (
            LOWER(COALESCE(first_name, '')) = LOWER($1)
            OR LOWER(COALESCE(nickname, '')) = LOWER($1)
            OR LOWER(
                TRIM(CONCAT_WS(' ', COALESCE(first_name, ''), COALESCE(last_name, '')))
            ) = LOWER($1)
          )
        ORDER BY updated_at DESC
        """,
        name,
    )

    if len(exact_rows) == 1:
        row = exact_rows[0]
        return {
            "contact_id": row["id"],
            "confidence": CONFIDENCE_HIGH,
            "candidates": [
                {
                    "contact_id": row["id"],
                    "name": _display_name_from_row(row),
                    "confidence": CONFIDENCE_HIGH,
                    "score": 100,
                    "salience": 0,
                }
            ],
            "inferred": False,
            "inferred_reason": None,
        }

    if len(exact_rows) > 1:
        # Multiple exact matches -- ambiguous, need disambiguation
        candidates = _build_candidates(exact_rows, base_score=90)

        # Integrate with entity_resolve when memory_pool is available
        if memory_pool is not None:
            candidates = await _resolve_via_entity_resolve(
                pool,
                memory_pool,
                candidates,
                name,
                context,
                memory_tenant_id,
            )
        else:
            # Fallback: local salience + context boost
            candidates = await _compute_salience(pool, candidates)
            if context:
                candidates = await _boost_by_context(pool, candidates, context)

        candidates.sort(key=lambda c: c["score"], reverse=True)

        if len(candidates) >= 2 and candidates[0]["score"] - candidates[1]["score"] >= 30:
            winner = candidates[0]
            inferred_reason = _generate_inferred_reason(winner)
            return {
                "contact_id": winner["contact_id"],
                "confidence": CONFIDENCE_HIGH,
                "candidates": candidates,
                "inferred": True,
                "inferred_reason": inferred_reason,
            }
        return {
            "contact_id": None,
            "confidence": CONFIDENCE_MEDIUM,
            "candidates": candidates,
            "inferred": False,
            "inferred_reason": None,
        }

    # Step 2: Partial match -- first name, last name, or substring
    name_parts = name.split()
    partial_rows = await pool.fetch(
        """
        SELECT id, first_name, last_name, nickname, company, job_title, metadata, entity_id
        FROM contacts
        WHERE listed = true
          AND (
            first_name ILIKE '%' || $1 || '%'
            OR last_name ILIKE '%' || $1 || '%'
            OR nickname ILIKE '%' || $1 || '%'
            OR company ILIKE '%' || $1 || '%'
            OR EXISTS (
                SELECT 1 FROM unnest(
                    string_to_array(
                        TRIM(CONCAT_WS(' ', COALESCE(first_name, ''), COALESCE(last_name, ''))),
                        ' '
                    )
                ) AS word
                WHERE LOWER(word) = LOWER($1)
            )
          )
        ORDER BY first_name, last_name, nickname
        """,
        name,
    )

    # Also try matching individual input words against contact name words
    if not partial_rows and len(name_parts) > 1:
        # Multi-word input with no substring match -- try each word
        conditions = []
        params: list[Any] = []
        for i, part in enumerate(name_parts, start=1):
            conditions.append(f"first_name ILIKE '%' || ${i} || '%'")
            conditions.append(f"last_name ILIKE '%' || ${i} || '%'")
            conditions.append(f"nickname ILIKE '%' || ${i} || '%'")
            params.append(part)
        query = f"""
            SELECT id, first_name, last_name, nickname, company, job_title, metadata, entity_id
            FROM contacts
            WHERE listed = true AND ({" OR ".join(conditions)})
            ORDER BY first_name, last_name, nickname
        """
        partial_rows = await pool.fetch(query, *params)

    if not partial_rows:
        return {
            "contact_id": None,
            "confidence": CONFIDENCE_NONE,
            "candidates": [],
            "inferred": False,
            "inferred_reason": None,
        }

    # Score partial matches
    candidates = _score_partial_matches(partial_rows, name, name_parts)

    if len(candidates) >= 2:
        # Integrate with entity_resolve when memory_pool is available
        if memory_pool is not None:
            candidates = await _resolve_via_entity_resolve(
                pool,
                memory_pool,
                candidates,
                name,
                context,
                memory_tenant_id,
            )
        else:
            # Fallback: local salience + context boost
            candidates = await _compute_salience(pool, candidates)
            if context:
                candidates = await _boost_by_context(pool, candidates, context)
    elif context:
        # Single candidate still benefits from context boosting for score accuracy
        candidates = await _boost_by_context(pool, candidates, context)

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # If there's exactly one candidate, return it with MEDIUM confidence
    if len(candidates) == 1:
        return {
            "contact_id": candidates[0]["contact_id"],
            "confidence": CONFIDENCE_MEDIUM,
            "candidates": candidates,
            "inferred": False,
            "inferred_reason": None,
        }

    # If one candidate clearly leads by ≥30 points, return HIGH confidence with auto-selection
    if len(candidates) >= 2 and candidates[0]["score"] - candidates[1]["score"] >= 30:
        winner = candidates[0]
        inferred_reason = _generate_inferred_reason(winner)
        return {
            "contact_id": winner["contact_id"],
            "confidence": CONFIDENCE_HIGH,
            "candidates": candidates,
            "inferred": True,
            "inferred_reason": inferred_reason,
        }

    # Multiple candidates without clear winner → MEDIUM confidence, no auto-selection
    return {
        "contact_id": None,
        "confidence": CONFIDENCE_MEDIUM,
        "candidates": candidates,
        "inferred": False,
        "inferred_reason": None,
    }


def _build_candidates(rows: list[asyncpg.Record], base_score: int = 50) -> list[dict[str, Any]]:
    """Build candidate list from DB rows with a base score."""
    return [
        {
            "contact_id": row["id"],
            "name": _display_name_from_row(row),
            "confidence": CONFIDENCE_MEDIUM,
            "score": base_score,
            "salience": 0,
            "_entity_id": str(row["entity_id"]) if row.get("entity_id") else None,
        }
        for row in rows
    ]


def _score_partial_matches(
    rows: list[asyncpg.Record],
    query_name: str,
    query_parts: list[str],
) -> list[dict[str, Any]]:
    """Score partial matches based on how well the name matches the query."""
    candidates = []
    query_lower = query_name.lower()

    for row in rows:
        contact_name = _display_name_from_row(row)
        contact_lower = contact_name.lower()
        contact_parts = [p.lower() for p in contact_name.split()]
        score = 0

        # Check if query matches the beginning of first name (strongest partial signal)
        if contact_parts and contact_parts[0].startswith(query_lower):
            score = 70
        # Check if query matches the beginning of last name
        elif len(contact_parts) > 1 and contact_parts[-1].startswith(query_lower):
            score = 65
        # Check if any word in contact name exactly matches any query word
        elif any(cp == qp.lower() for cp in contact_parts for qp in query_parts):
            score = 60
        # Check if query is a substring of the contact name
        elif query_lower in contact_lower:
            score = 50
        # Any other match (from the SQL)
        else:
            score = 40

        candidates.append(
            {
                "contact_id": row["id"],
                "name": contact_name,
                "confidence": CONFIDENCE_MEDIUM,
                "score": score,
                "salience": 0,
                "_entity_id": str(row["entity_id"]) if row.get("entity_id") else None,
            }
        )

    return candidates


async def _resolve_via_entity_resolve(
    pool: asyncpg.Pool,
    memory_pool: asyncpg.Pool,
    candidates: list[dict[str, Any]],
    name: str,
    context: str | None,
    memory_tenant_id: str,
) -> list[dict[str, Any]]:
    """Integrate entity_resolve with salience domain_scores.

    Steps:
    1. Compute salience scores for each candidate from relationship domain data.
    2. Map contact_id -> entity_id for each candidate.
    3. Call entity_resolve with salience scores as context_hints.domain_scores.
    4. Map entity_resolve results back to candidates, merging entity-level scores.

    Falls back gracefully to local salience + context boost if entity_resolve
    fails or no candidates have entity_ids.

    Only called when len(candidates) >= 2.
    """
    from butlers.modules.memory.tools.entities import entity_resolve

    # Step 1: Compute salience scores for all candidates
    candidates = await _compute_salience(pool, candidates)

    # Step 2: Collect entity_ids and build salience domain_scores
    domain_scores: dict[str, float] = {}
    entity_id_to_contact: dict[str, str] = {}

    for cand in candidates:
        entity_id = cand.get("_entity_id")
        if entity_id:
            domain_scores[entity_id] = float(cand["salience"])
            entity_id_to_contact[entity_id] = cand["contact_id"]

    # If no candidates have entity_ids, fall back to local context boost
    if not domain_scores:
        if context:
            candidates = await _boost_by_context(pool, candidates, context)
        return candidates

    # Step 3: Build context_hints
    context_hints: dict[str, Any] = {"domain_scores": domain_scores}
    if context:
        context_hints["topic"] = context

    # Step 4: Call entity_resolve with domain_scores
    try:
        entity_candidates = await entity_resolve(
            memory_pool,
            name,
            tenant_id=memory_tenant_id,
            entity_type="person",
            context_hints=context_hints,
        )
    except Exception:
        logger.exception("entity_resolve failed for name=%r; falling back to local scoring", name)
        if context:
            candidates = await _boost_by_context(pool, candidates, context)
        return candidates

    # Step 5: Merge entity_resolve scores back into candidates
    # Build a map of entity_id -> entity score from entity_resolve results
    entity_scores: dict[str, float] = {ec["entity_id"]: ec["score"] for ec in entity_candidates}

    # For candidates with an entity_id, replace their score with the entity_resolve
    # composite score (which already includes name-match quality + graph boost + domain_scores).
    # For candidates without an entity_id, add local context boost only.
    contact_ids_with_entity = set(entity_id_to_contact.values())

    for cand in candidates:
        entity_id = cand.get("_entity_id")
        if entity_id and entity_id in entity_scores:
            # entity_resolve already incorporates salience via domain_scores;
            # use its composite score as the candidate score
            cand["score"] = entity_scores[entity_id]
        elif cand["contact_id"] not in contact_ids_with_entity and context:
            # No entity link — fall back to context boost for this candidate
            await _boost_single_by_context(pool, cand, context)

    return candidates


async def _compute_salience(
    pool: asyncpg.Pool,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute salience scores for candidates based on relationship data.

    Only called when len(candidates) >= 2.

    For each candidate, queries:
    - relationships: type-to-user weight
    - interactions: count in last 90 days + recency
    - quick_facts + notes: row count (density)
    - contacts.stay_in_touch_days: cadence importance
    - group_members → groups.type: group type weight

    Returns candidates with 'salience' field added and score updated.
    """
    # Extract all candidate IDs for batch queries
    candidate_ids = [c["contact_id"] for c in candidates]

    # Batch query 1: All contact data (stay_in_touch_days)
    contact_data_rows = await pool.fetch(
        "SELECT id, stay_in_touch_days FROM contacts WHERE id = ANY($1)",
        candidate_ids,
    )
    contact_data = {row["id"]: row for row in contact_data_rows}

    # Batch query 2: Relationship types
    rel_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (contact_id)
            CASE
                WHEN r.contact_id = ANY($1) THEN r.contact_id
                ELSE r.related_contact_id
            END as contact_id,
            rt.forward_label
        FROM relationships r
        JOIN relationship_types rt ON r.relationship_type_id = rt.id
        WHERE r.contact_id = ANY($1) OR r.related_contact_id = ANY($1)
        ORDER BY contact_id, r.created_at DESC
        """,
        candidate_ids,
    )
    relationships = {row["contact_id"]: row["forward_label"] for row in rel_rows}

    # Batch query 3: Interaction counts and recency (last 90 days)
    interaction_rows = await pool.fetch(
        """
        SELECT
            contact_id,
            COUNT(*) FILTER (WHERE interaction_date >= NOW() - INTERVAL '90 days') as count_90d,
            MAX(interaction_date) as most_recent
        FROM interactions
        WHERE contact_id = ANY($1)
        GROUP BY contact_id
        """,
        candidate_ids,
    )
    interactions = {row["contact_id"]: row for row in interaction_rows}

    # Batch query 4: Fact and note counts
    fact_note_rows = await pool.fetch(
        """
        SELECT
            contact_id,
            SUM(CASE WHEN source = 'fact' THEN 1 ELSE 0 END) as fact_count,
            SUM(CASE WHEN source = 'note' THEN 1 ELSE 0 END) as note_count
        FROM (
            SELECT contact_id, 'fact' as source FROM quick_facts WHERE contact_id = ANY($1)
            UNION ALL
            SELECT contact_id, 'note' as source FROM notes WHERE contact_id = ANY($1)
        ) combined
        GROUP BY contact_id
        """,
        candidate_ids,
    )
    fact_notes = {
        row["contact_id"]: {"fact_count": row["fact_count"], "note_count": row["note_count"]}
        for row in fact_note_rows
    }

    # Batch query 5: Group memberships
    group_rows = await pool.fetch(
        """
        SELECT gm.contact_id, g.type
        FROM group_members gm
        JOIN groups g ON gm.group_id = g.id
        WHERE gm.contact_id = ANY($1)
        """,
        candidate_ids,
    )
    groups = {}
    for row in group_rows:
        cid = row["contact_id"]
        if cid not in groups:
            groups[cid] = []
        groups[cid].append(row["type"])

    # Now compute salience for each candidate using the batched data
    now = datetime.datetime.now(datetime.UTC)
    for candidate in candidates:
        cid = candidate["contact_id"]
        salience = 0

        # 1. Relationship type weight
        if cid in relationships:
            rel_type = relationships[cid]
            salience += RELATIONSHIP_WEIGHTS.get(rel_type, 0)
            # Store for inferred_reason generation
            candidate["_relationship_type"] = rel_type

        # 2. Interaction frequency (last 90 days, +2 per, cap +20)
        if cid in interactions:
            interaction_count = interactions[cid]["count_90d"]
            salience += min(interaction_count * 2, 20)
            # Store for inferred_reason generation
            candidate["_interaction_count"] = interaction_count

            # 3. Interaction recency (<7d +15, <30d +10, <90d +5)
            most_recent = interactions[cid]["most_recent"]
            if most_recent:
                delta = now - most_recent.replace(tzinfo=datetime.UTC)
                if delta.days < 7:
                    salience += 15
                elif delta.days < 30:
                    salience += 10
                elif delta.days < 90:
                    salience += 5

        # 4. Fact & note density (+1 per, cap +10)
        if cid in fact_notes:
            density = min(
                (fact_notes[cid]["fact_count"] or 0) + (fact_notes[cid]["note_count"] or 0), 10
            )
            salience += density

        # 5. Stay-in-touch cadence
        if cid in contact_data:
            stay_in_touch_days = contact_data[cid]["stay_in_touch_days"]
            if stay_in_touch_days:
                if stay_in_touch_days <= 7:
                    salience += 10
                elif stay_in_touch_days <= 14:
                    salience += 7
                elif stay_in_touch_days <= 30:
                    salience += 5

        # 6. Group membership weight
        if cid in groups:
            for group_type in groups[cid]:
                salience += GROUP_TYPE_WEIGHTS.get(group_type, 0)

        # Store salience and add to score.
        # When entity_resolve integration is active, the entity score will override this;
        # when falling back to local scoring, this addition is the final score.
        candidate["salience"] = salience
        candidate["score"] += salience

    return candidates


async def _boost_by_context(
    pool: asyncpg.Pool,
    candidates: list[dict[str, Any]],
    context: str,
) -> list[dict[str, Any]]:
    """Boost candidate scores based on context matching against metadata and notes."""
    for candidate in candidates:
        await _boost_single_by_context(pool, candidate, context)
    return candidates


async def _boost_single_by_context(
    pool: asyncpg.Pool,
    candidate: dict[str, Any],
    context: str,
) -> None:
    """Boost a single candidate's score based on context matching. Modifies in-place."""
    context_words = [w.lower() for w in context.split() if len(w) > 2]
    cid = candidate["contact_id"]

    # Check metadata and explicit profile fields
    detail_row = await pool.fetchrow(
        """
        SELECT metadata, company, job_title, first_name, last_name, nickname
        FROM contacts
        WHERE id = $1
        """,
        cid,
    )
    if detail_row:
        metadata = detail_row["metadata"]
        metadata_text = (
            json.dumps(metadata).lower()
            if isinstance(metadata, dict)
            else str(metadata or "").lower()
        )
        profile_text = " ".join(
            [
                str(detail_row["company"] or ""),
                str(detail_row["job_title"] or ""),
                str(detail_row["first_name"] or ""),
                str(detail_row["last_name"] or ""),
                str(detail_row["nickname"] or ""),
            ]
        ).lower()
        for word in context_words:
            if word in metadata_text or word in profile_text:
                candidate["score"] += 10
                break

    # Check notes
    note_rows = await pool.fetch("SELECT body FROM notes WHERE contact_id = $1 LIMIT 10", cid)
    for note in note_rows:
        note_text = str(note["body"] or "").lower()
        for word in context_words:
            if word in note_text:
                candidate["score"] += 5
                break

    # Check interactions
    interaction_rows = await pool.fetch(
        "SELECT summary FROM interactions WHERE contact_id = $1 AND summary IS NOT NULL LIMIT 10",
        cid,
    )
    for interaction in interaction_rows:
        int_text = interaction["summary"].lower()
        for word in context_words:
            if word in int_text:
                candidate["score"] += 5
                break
