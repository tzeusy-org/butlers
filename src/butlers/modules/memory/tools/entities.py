"""Memory entity tools — create, retrieve, update, and resolve named entities."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _serialize_row

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = frozenset({"person", "organization", "place", "other"})

# Minimum composite score to include a candidate in resolution results.
_MIN_SCORE: float = 0.0

# Base scores for name-match quality (out of 100).
_SCORE_EXACT_NAME: float = 100.0
_SCORE_EXACT_ALIAS: float = 80.0
_SCORE_PREFIX: float = 50.0
_SCORE_FUZZY: float = 20.0

# Graph neighborhood weight: added on top of name-match score.
_GRAPH_BOOST_MAX: float = 20.0


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


async def entity_create(
    pool: Pool,
    canonical_name: str,
    entity_type: Literal["person", "organization", "place", "other"],
    *,
    tenant_id: str,
    aliases: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new entity and return its UUID.

    Args:
        pool: asyncpg connection pool.
        canonical_name: The canonical name of the entity.
        entity_type: One of 'person', 'organization', 'place', 'other'.
        tenant_id: Tenant scope for isolation.
        aliases: Optional list of alternative names for the entity.
        metadata: Optional JSONB metadata dict.

    Returns:
        Dict with key ``entity_id`` (UUID string).

    Raises:
        ValueError: If the (tenant_id, canonical_name, entity_type) already exists
                    or if entity_type is invalid.
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type '{entity_type}'. Must be one of: {sorted(VALID_ENTITY_TYPES)}"
        )

    aliases_list = aliases or []
    metadata_json = json.dumps(metadata or {})

    try:
        entity_id = await pool.fetchval(
            """
            INSERT INTO entities (tenant_id, canonical_name, entity_type, aliases, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            tenant_id,
            canonical_name,
            entity_type,
            aliases_list,
            metadata_json,
        )
    except Exception as exc:
        exc_str = str(exc)
        if "uq_entities_tenant_canonical_type" in exc_str or "unique" in exc_str.lower():
            raise ValueError(
                f"Entity with canonical_name='{canonical_name}' and "
                f"entity_type='{entity_type}' already exists for this tenant."
            ) from exc
        raise

    return {"entity_id": str(entity_id)}


async def entity_get(
    pool: Pool,
    entity_id: str,
    *,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Return the full entity record including aliases and metadata.

    Args:
        pool: asyncpg connection pool.
        entity_id: UUID string of the entity.
        tenant_id: Tenant scope for isolation.

    Returns:
        Serialized entity dict or None if not found.
    """
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, canonical_name, entity_type, aliases, metadata,
               created_at, updated_at
        FROM entities
        WHERE id = $1 AND tenant_id = $2
        """,
        uuid.UUID(entity_id),
        tenant_id,
    )

    if row is None:
        return None

    return _serialize_row(dict(row))


async def entity_update(
    pool: Pool,
    entity_id: str,
    *,
    tenant_id: str,
    canonical_name: str | None = None,
    aliases: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update entity fields.

    - ``canonical_name``: replaces the current value when provided.
    - ``aliases``: replace-all semantics — pass the full desired list.
    - ``metadata``: merge semantics — keys are merged into existing metadata.

    Args:
        pool: asyncpg connection pool.
        entity_id: UUID string of the entity to update.
        tenant_id: Tenant scope for isolation.
        canonical_name: New canonical name (optional).
        aliases: Full replacement aliases list (optional).
        metadata: Metadata keys to merge in (optional).

    Returns:
        Updated serialized entity dict or None if not found.
    """
    eid = uuid.UUID(entity_id)

    current = await pool.fetchrow(
        "SELECT id, metadata FROM entities WHERE id = $1 AND tenant_id = $2",
        eid,
        tenant_id,
    )
    if current is None:
        return None

    set_clauses: list[str] = ["updated_at = now()"]
    params: list[Any] = []
    param_idx = 1

    if canonical_name is not None:
        params.append(canonical_name)
        set_clauses.append(f"canonical_name = ${param_idx}")
        param_idx += 1

    if aliases is not None:
        params.append(aliases)
        set_clauses.append(f"aliases = ${param_idx}")
        param_idx += 1

    if metadata is not None:
        existing_metadata: dict[str, Any] = dict(current["metadata"]) if current["metadata"] else {}
        merged = {**existing_metadata, **metadata}
        params.append(json.dumps(merged))
        set_clauses.append(f"metadata = ${param_idx}::jsonb")
        param_idx += 1

    params.append(eid)
    params.append(tenant_id)
    where_id_idx = param_idx
    where_tenant_idx = param_idx + 1

    row = await pool.fetchrow(
        f"""
        UPDATE entities
        SET {", ".join(set_clauses)}
        WHERE id = ${where_id_idx} AND tenant_id = ${where_tenant_idx}
        RETURNING id, tenant_id, canonical_name, entity_type, aliases, metadata,
                  created_at, updated_at
        """,
        *params,
    )

    if row is None:
        return None

    return _serialize_row(dict(row))


async def entity_resolve(
    pool: Pool,
    name: str,
    *,
    tenant_id: str,
    entity_type: str | None = None,
    context_hints: dict[str, Any] | None = None,
    enable_fuzzy: bool = False,
) -> list[dict[str, Any]]:
    """Resolve an ambiguous name string to a ranked list of entity candidates.

    Performs four-tier candidate discovery (exact canonical, exact alias,
    prefix/substring, optional fuzzy) and composite scoring combining
    name-match quality with graph neighborhood similarity when context_hints
    are provided.

    Args:
        pool: asyncpg connection pool.
        name: The ambiguous name string to resolve.
        tenant_id: Tenant scope for isolation.
        entity_type: Optional entity type filter (person/organization/place/other).
        context_hints: Optional dict with keys ``topic`` (str),
            ``mentioned_with`` (list of names), ``domain_scores``
            (dict of entity_id -> numeric score).
        enable_fuzzy: When True, includes fuzzy (edit distance <= 2) candidates.

    Returns:
        List of candidate dicts ordered by score DESC, then canonical_name ASC.
        Each dict has keys: entity_id, canonical_name, entity_type, score,
        name_match, aliases.
        Returns empty list when no candidates found above minimum threshold.
    """
    if not name or not name.strip():
        return []

    name_stripped = name.strip()
    name_lower = name_stripped.lower()
    hints = context_hints or {}

    # Build type filter clause
    type_params: list[Any] = [tenant_id, name_lower]
    type_filter = ""
    if entity_type is not None:
        type_params.append(entity_type)
        type_filter = f" AND entity_type = ${len(type_params)}"

    # -------------------------------------------------------------------------
    # Step 1: candidate discovery via UNION ALL across match tiers
    # -------------------------------------------------------------------------
    # Each tier returns: id, canonical_name, entity_type, aliases, match_type
    # We use DISTINCT ON to keep the best match type per entity (ordered by tier priority).
    #
    # Tier numbering: 1=exact_canonical, 2=exact_alias, 3=prefix, 4=fuzzy
    # Lower tier number = higher priority.

    discovery_sql = f"""
        SELECT DISTINCT ON (id)
            id, canonical_name, entity_type, aliases, match_type
        FROM (
            -- Tier 1: exact canonical_name match (case-insensitive)
            SELECT id, canonical_name, entity_type, aliases, 1 AS tier, 'exact' AS match_type
            FROM entities
            WHERE tenant_id = $1
              AND LOWER(canonical_name) = $2
              {type_filter}

            UNION ALL

            -- Tier 2: exact alias match (case-insensitive)
            SELECT id, canonical_name, entity_type, aliases, 2 AS tier, 'alias' AS match_type
            FROM entities
            WHERE tenant_id = $1
              AND $2 = ANY(SELECT LOWER(a) FROM UNNEST(aliases) AS a)
              {type_filter}

            UNION ALL

            -- Tier 3: prefix/substring match on canonical_name and aliases
            SELECT id, canonical_name, entity_type, aliases, 3 AS tier, 'prefix' AS match_type
            FROM entities
            WHERE tenant_id = $1
              AND (
                  LOWER(canonical_name) LIKE ($2 || '%')
                  OR LOWER(canonical_name) LIKE ('%' || $2 || '%')
                  OR EXISTS (
                      SELECT 1 FROM UNNEST(aliases) AS a
                      WHERE LOWER(a) LIKE ($2 || '%')
                         OR LOWER(a) LIKE ('%' || $2 || '%')
                  )
              )
              -- Exclude already-exact matches
              AND LOWER(canonical_name) != $2
              AND NOT ($2 = ANY(SELECT LOWER(a) FROM UNNEST(aliases) AS a))
              {type_filter}
        ) candidates
        ORDER BY id, tier ASC
    """

    raw_rows = await pool.fetch(discovery_sql, *type_params)

    # Optionally add fuzzy candidates (edit distance <= 2)
    fuzzy_rows: list[Any] = []
    if enable_fuzzy and len(name_stripped) > 2:
        fuzzy_rows = await _fetch_fuzzy_candidates(
            pool, name_stripped, tenant_id, entity_type, type_params
        )

    # -------------------------------------------------------------------------
    # Step 2: build candidate set (dedup by entity id, prefer higher-priority tier)
    # -------------------------------------------------------------------------
    # match_type priority: exact > alias > prefix > fuzzy
    _TIER_RANK = {"exact": 0, "alias": 1, "prefix": 2, "fuzzy": 3}

    candidates: dict[str, dict[str, Any]] = {}

    for row in raw_rows:
        eid = str(row["id"])
        existing_rank = _TIER_RANK.get(candidates.get(eid, {}).get("match_type", "fuzzy"), 3)
        if eid not in candidates or _TIER_RANK[row["match_type"]] < existing_rank:
            candidates[eid] = {
                "entity_id": eid,
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
                "aliases": list(row["aliases"]) if row["aliases"] else [],
                "match_type": row["match_type"],
            }

    for row in fuzzy_rows:
        eid = str(row["id"])
        if eid not in candidates:
            candidates[eid] = {
                "entity_id": eid,
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
                "aliases": list(row["aliases"]) if row["aliases"] else [],
                "match_type": "fuzzy",
            }

    if not candidates:
        return []

    # -------------------------------------------------------------------------
    # Step 3: compute name-match base scores
    # -------------------------------------------------------------------------
    _MATCH_BASE: dict[str, float] = {
        "exact": _SCORE_EXACT_NAME,
        "alias": _SCORE_EXACT_ALIAS,
        "prefix": _SCORE_PREFIX,
        "fuzzy": _SCORE_FUZZY,
    }

    for cand in candidates.values():
        cand["score"] = _MATCH_BASE[cand["match_type"]]

    # -------------------------------------------------------------------------
    # Step 4: graph neighborhood scoring when context_hints provided
    # -------------------------------------------------------------------------
    if hints:
        entity_ids = list(candidates.keys())
        await _apply_graph_neighborhood_scores(pool, entity_ids, candidates, hints)

    # -------------------------------------------------------------------------
    # Step 5: apply domain_scores from context_hints
    # -------------------------------------------------------------------------
    domain_scores: dict[str, float] = hints.get("domain_scores", {}) or {}
    for eid, ds in domain_scores.items():
        if eid in candidates:
            try:
                candidates[eid]["score"] += float(ds)
            except (TypeError, ValueError):
                logger.warning("Skipping non-numeric domain_score for entity %s: %r", eid, ds)

    # -------------------------------------------------------------------------
    # Step 6: filter, sort, and return
    # -------------------------------------------------------------------------
    results = [c for c in candidates.values() if c["score"] > _MIN_SCORE]
    results.sort(key=lambda c: (-c["score"], c["canonical_name"]))

    return [
        {
            "entity_id": c["entity_id"],
            "canonical_name": c["canonical_name"],
            "entity_type": c["entity_type"],
            "score": round(c["score"], 4),
            "name_match": c["match_type"],
            "aliases": c["aliases"],
        }
        for c in results
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_fuzzy_candidates(
    pool: Pool,
    name: str,
    tenant_id: str,
    entity_type: str | None,
    existing_params: list[Any],
) -> list[Any]:
    """Fetch candidates via trigram similarity (pg_trgm).

    Falls back to an empty list if pg_trgm is not available, preserving
    graceful degradation in environments without the extension.
    """
    try:
        fuzzy_params: list[Any] = [tenant_id, name]
        fuzzy_type_filter = ""
        if entity_type is not None:
            fuzzy_params.append(entity_type)
            fuzzy_type_filter = f" AND entity_type = ${len(fuzzy_params)}"

        rows = await pool.fetch(
            f"""
            SELECT id, canonical_name, entity_type, aliases
            FROM entities
            WHERE tenant_id = $1
              AND (
                  similarity(canonical_name, $2) > 0.3
                  OR EXISTS (
                      SELECT 1 FROM UNNEST(aliases) AS a
                      WHERE similarity(a, $2) > 0.3
                  )
              )
              AND LOWER(canonical_name) != LOWER($2)
              AND NOT (LOWER($2) = ANY(SELECT LOWER(a) FROM UNNEST(aliases) AS a))
              {fuzzy_type_filter}
            LIMIT 20
            """,
            *fuzzy_params,
        )
        return list(rows)
    except Exception as exc:
        # pg_trgm not installed or other DB error — degrade gracefully
        logger.debug("Fuzzy candidate fetch failed (pg_trgm may not be available): %s", exc)
        return []


async def _apply_graph_neighborhood_scores(
    pool: Pool,
    entity_ids: list[str],
    candidates: dict[str, dict[str, Any]],
    hints: dict[str, Any],
) -> None:
    """Compute graph neighborhood similarity and boost candidate scores.

    Fetches facts associated with each candidate entity, then computes
    keyword overlap between fact predicates/content and the provided hints.

    Modifies ``candidates`` in-place, adding to the ``score`` field.
    """
    if not entity_ids:
        return

    # Build hint terms from topic and mentioned_with
    hint_terms: set[str] = set()

    topic: str | None = hints.get("topic")
    if topic and isinstance(topic, str):
        hint_terms.update(_tokenize(topic))

    mentioned_with: list | None = hints.get("mentioned_with")
    if mentioned_with and isinstance(mentioned_with, list):
        for item in mentioned_with:
            if isinstance(item, str):
                hint_terms.update(_tokenize(item))

    if not hint_terms:
        # No keyword hints to compare against — skip graph scoring
        return

    # Fetch facts for all candidate entities in a single query
    uuid_ids = [uuid.UUID(eid) for eid in entity_ids]
    fact_rows = await pool.fetch(
        """
        SELECT entity_id, predicate, content
        FROM facts
        WHERE entity_id = ANY($1)
          AND validity = 'active'
        LIMIT 500
        """,
        uuid_ids,
    )

    # Group fact text per entity
    entity_fact_terms: dict[str, set[str]] = {eid: set() for eid in entity_ids}
    for row in fact_rows:
        eid = str(row["entity_id"])
        if eid in entity_fact_terms:
            entity_fact_terms[eid].update(_tokenize(row["predicate"] or ""))
            entity_fact_terms[eid].update(_tokenize(row["content"] or ""))

    # Compute Jaccard-like overlap and apply boost
    for eid, fact_terms in entity_fact_terms.items():
        if not fact_terms:
            continue
        intersection = hint_terms & fact_terms
        union = hint_terms | fact_terms
        if union:
            overlap = len(intersection) / len(union)
            boost = overlap * _GRAPH_BOOST_MAX
            candidates[eid]["score"] += boost


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens for keyword overlap computation."""
    import re

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return set(tokens)
