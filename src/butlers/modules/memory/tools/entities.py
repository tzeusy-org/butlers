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


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Safely parse a metadata value from asyncpg (may be dict, str, or None)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


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
    roles: list[str] | None = None,
) -> dict[str, Any]:
    """Insert a new entity and return its UUID.

    Args:
        pool: asyncpg connection pool.
        canonical_name: The canonical name of the entity.
        entity_type: One of 'person', 'organization', 'place', 'other'.
        tenant_id: Tenant scope for isolation.
        aliases: Optional list of alternative names for the entity.
        metadata: Optional JSONB metadata dict.
        roles: Optional list of identity roles (e.g. ['owner']).  Internal use
               only — not exposed to runtime MCP tool callers.

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
    roles_list = roles or []
    metadata_json = json.dumps(metadata or {})

    insert_sql = """
        INSERT INTO shared.entities
            (tenant_id, canonical_name, entity_type, aliases, metadata, roles)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        RETURNING id
    """
    insert_args = (tenant_id, canonical_name, entity_type, aliases_list, metadata_json, roles_list)

    try:
        entity_id = await pool.fetchval(insert_sql, *insert_args)
    except Exception as exc:
        exc_str = str(exc)
        if "uq_entities_tenant_canonical_type" not in exc_str and "unique" not in exc_str.lower():
            raise

        # The name slot may be occupied by a tombstoned (merged) entity.
        # Rename the tombstone to free the slot, then retry the insert.
        renamed = await pool.fetchval(
            """
            UPDATE shared.entities
            SET canonical_name = canonical_name || ' [merged:' || id::text || ']',
                updated_at = now()
            WHERE tenant_id = $1
              AND LOWER(canonical_name) = LOWER($2)
              AND entity_type = $3
              AND (metadata->>'merged_into') IS NOT NULL
            RETURNING id
            """,
            tenant_id,
            canonical_name,
            entity_type,
        )
        if renamed is None:
            # Conflict is with a live (non-merged) entity — genuine duplicate
            raise ValueError(
                f"Entity with canonical_name='{canonical_name}' and "
                f"entity_type='{entity_type}' already exists for this tenant."
            ) from exc

        entity_id = await pool.fetchval(insert_sql, *insert_args)

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
               roles, created_at, updated_at
        FROM shared.entities
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
        "SELECT id, metadata FROM shared.entities WHERE id = $1 AND tenant_id = $2",
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
        existing_metadata: dict[str, Any] = _parse_metadata(current["metadata"])
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
        UPDATE shared.entities
        SET {", ".join(set_clauses)}
        WHERE id = ${where_id_idx} AND tenant_id = ${where_tenant_idx}
        RETURNING id, tenant_id, canonical_name, entity_type, aliases, metadata,
                  roles, created_at, updated_at
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
            FROM shared.entities
            WHERE tenant_id = $1
              AND LOWER(canonical_name) = $2
              AND (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
              {type_filter}

            UNION ALL

            -- Tier 2: exact alias match (case-insensitive)
            SELECT id, canonical_name, entity_type, aliases, 2 AS tier, 'alias' AS match_type
            FROM shared.entities
            WHERE tenant_id = $1
              AND $2 = ANY(SELECT LOWER(a) FROM UNNEST(aliases) AS a)
              AND (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
              {type_filter}

            UNION ALL

            -- Tier 3: prefix/substring match on canonical_name and aliases
            SELECT id, canonical_name, entity_type, aliases, 3 AS tier, 'prefix' AS match_type
            FROM shared.entities
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
              AND (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
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
            FROM shared.entities
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
              AND (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
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


async def entity_neighbors(
    pool: Pool,
    entity_id: str,
    *,
    tenant_id: str,
    max_depth: int = 2,
    predicate_filter: list[str] | None = None,
    direction: Literal["outgoing", "incoming", "both"] = "both",
) -> list[dict[str, Any]]:
    """Traverse the entity graph via edge-facts and return neighboring entities.

    Uses a recursive CTE to follow facts where ``object_entity_id IS NOT NULL``,
    treating them as directed edges from ``entity_id`` (subject) to
    ``object_entity_id`` (object).

    Args:
        pool: asyncpg connection pool.
        entity_id: UUID string of the starting entity.
        tenant_id: Tenant scope for isolation.
        max_depth: Maximum traversal depth (1–5, default 2).
        predicate_filter: Optional list of predicates to restrict traversal.
        direction: Edge direction to follow: outgoing, incoming, or both.

    Returns:
        List of neighbor dicts ordered by depth then canonical_name, each with:
          - entity: ``{id, canonical_name, entity_type}``
          - predicate: the edge predicate at this hop
          - direction: ``'outgoing'`` or ``'incoming'`` relative to the source
          - content: the edge fact's content
          - depth: hop distance from start entity
          - fact_id: UUID string of the edge fact
          - path: list of entity ID strings along the traversal path
    """
    max_depth = max(1, min(max_depth, 5))
    eid = uuid.UUID(entity_id)

    # Validate entity existence.
    exists = await pool.fetchval(
        "SELECT 1 FROM shared.entities WHERE id = $1 AND tenant_id = $2",
        eid,
        tenant_id,
    )
    if not exists:
        raise ValueError(f"entity_id {entity_id!r} does not exist")

    params: list[Any] = [eid, tenant_id, max_depth]
    pred_clause = ""
    if predicate_filter:
        params.append(predicate_filter)
        pred_clause = f"\n          AND f.predicate = ANY(${len(params)})"

    if direction == "outgoing":
        base_sql = f"""
        SELECT f.object_entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'outgoing'::text AS dir,
               1 AS depth, ARRAY[$1::uuid, f.object_entity_id] AS path
        FROM facts f
        WHERE f.entity_id = $1 AND f.object_entity_id IS NOT NULL
          AND f.validity = 'active'{pred_clause}"""
        rec_sql = f"""
        SELECT f.object_entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'outgoing'::text AS dir,
               n.depth + 1, n.path || f.object_entity_id
        FROM neighbors n
        JOIN facts f ON f.entity_id = n.neighbor_id
        WHERE f.object_entity_id IS NOT NULL
          AND f.validity = 'active'
          AND f.object_entity_id != ALL(n.path)
          AND n.depth < $3{pred_clause}"""
    elif direction == "incoming":
        base_sql = f"""
        SELECT f.entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'incoming'::text AS dir,
               1 AS depth, ARRAY[$1::uuid, f.entity_id] AS path
        FROM facts f
        WHERE f.object_entity_id = $1
          AND f.validity = 'active'{pred_clause}"""
        rec_sql = f"""
        SELECT f.entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'incoming'::text AS dir,
               n.depth + 1, n.path || f.entity_id
        FROM neighbors n
        JOIN facts f ON f.object_entity_id = n.neighbor_id
        WHERE f.validity = 'active'
          AND f.entity_id != ALL(n.path)
          AND n.depth < $3{pred_clause}"""
    else:  # both
        base_sql = f"""
        SELECT f.object_entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'outgoing'::text AS dir,
               1 AS depth, ARRAY[$1::uuid, f.object_entity_id] AS path
        FROM facts f
        WHERE f.entity_id = $1 AND f.object_entity_id IS NOT NULL
          AND f.validity = 'active'{pred_clause}
        UNION ALL
        SELECT f.entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'incoming'::text AS dir,
               1 AS depth, ARRAY[$1::uuid, f.entity_id] AS path
        FROM facts f
        WHERE f.object_entity_id = $1
          AND f.validity = 'active'{pred_clause}"""
        rec_sql = f"""
        SELECT f.object_entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'outgoing'::text AS dir,
               n.depth + 1, n.path || f.object_entity_id
        FROM neighbors n
        JOIN facts f ON f.entity_id = n.neighbor_id
        WHERE f.object_entity_id IS NOT NULL
          AND f.validity = 'active'
          AND f.object_entity_id != ALL(n.path)
          AND n.depth < $3{pred_clause}
        UNION ALL
        SELECT f.entity_id AS neighbor_id, f.predicate, f.content, f.id AS fact_id,
               'incoming'::text AS dir,
               n.depth + 1, n.path || f.entity_id
        FROM neighbors n
        JOIN facts f ON f.object_entity_id = n.neighbor_id
        WHERE f.validity = 'active'
          AND f.entity_id != ALL(n.path)
          AND n.depth < $3{pred_clause}"""

    sql = f"""
    WITH RECURSIVE neighbors AS (
        {base_sql}
        UNION ALL
        {rec_sql}
    )
    SELECT
        n.neighbor_id AS entity_id,
        e.canonical_name,
        e.entity_type,
        n.predicate,
        n.dir,
        n.content,
        n.fact_id,
        n.depth,
        n.path
    FROM neighbors n
    JOIN shared.entities e ON e.id = n.neighbor_id AND e.tenant_id = $2
    ORDER BY n.depth, e.canonical_name
    """

    rows = await pool.fetch(sql, *params)

    return [
        {
            "entity": {
                "id": str(row["entity_id"]),
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
            },
            "predicate": row["predicate"],
            "direction": row["dir"],
            "content": row["content"],
            "depth": row["depth"],
            "fact_id": str(row["fact_id"]),
            "path": [str(uid) for uid in row["path"]],
        }
        for row in rows
    ]


async def _repoint_facts_on_pool(
    pool: Pool,
    src_uuid: uuid.UUID,
    tgt_uuid: uuid.UUID,
) -> dict[str, int]:
    """Re-point facts from source entity to target on a single pool's schema.

    Returns dict with facts_repointed, facts_superseded, edge_facts_repointed,
    edge_facts_superseded counts.
    """
    facts_repointed = 0
    facts_superseded = 0
    edge_facts_repointed = 0
    edge_facts_superseded = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Subject-side facts
            src_facts = await conn.fetch(
                "SELECT id, scope, predicate, confidence FROM facts "
                "WHERE entity_id = $1 AND validity = 'active'",
                src_uuid,
            )

            for src_fact in src_facts:
                conflict = await conn.fetchrow(
                    "SELECT id, confidence FROM facts "
                    "WHERE entity_id = $1 AND scope = $2 AND predicate = $3 "
                    "AND validity = 'active'",
                    tgt_uuid,
                    src_fact["scope"],
                    src_fact["predicate"],
                )

                if conflict is None:
                    await conn.execute(
                        "UPDATE facts SET entity_id = $1 WHERE id = $2",
                        tgt_uuid,
                        src_fact["id"],
                    )
                    facts_repointed += 1
                else:
                    src_confidence = src_fact["confidence"]
                    tgt_confidence = conflict["confidence"]

                    if src_confidence > tgt_confidence:
                        await conn.execute(
                            "UPDATE facts SET validity = 'superseded', supersedes_id = $1 "
                            "WHERE id = $2",
                            src_fact["id"],
                            conflict["id"],
                        )
                        await conn.execute(
                            "UPDATE facts SET entity_id = $1 WHERE id = $2",
                            tgt_uuid,
                            src_fact["id"],
                        )
                    else:
                        await conn.execute(
                            "UPDATE facts SET validity = 'superseded', supersedes_id = $1 "
                            "WHERE id = $2",
                            conflict["id"],
                            src_fact["id"],
                        )
                    facts_superseded += 1

            # Edge facts (object_entity_id)
            obj_facts = await conn.fetch(
                "SELECT id, entity_id, scope, predicate, confidence FROM facts "
                "WHERE object_entity_id = $1 AND validity = 'active'",
                src_uuid,
            )

            for obj_fact in obj_facts:
                edge_conflict = await conn.fetchrow(
                    "SELECT id, confidence FROM facts "
                    "WHERE entity_id = $1 AND object_entity_id = $2 "
                    "AND scope = $3 AND predicate = $4 "
                    "AND validity = 'active'",
                    obj_fact["entity_id"],
                    tgt_uuid,
                    obj_fact["scope"],
                    obj_fact["predicate"],
                )

                if edge_conflict is None:
                    await conn.execute(
                        "UPDATE facts SET object_entity_id = $1 WHERE id = $2",
                        tgt_uuid,
                        obj_fact["id"],
                    )
                    edge_facts_repointed += 1
                else:
                    src_conf = obj_fact["confidence"]
                    tgt_conf = edge_conflict["confidence"]

                    if src_conf > tgt_conf:
                        await conn.execute(
                            "UPDATE facts SET validity = 'superseded', supersedes_id = $1 "
                            "WHERE id = $2",
                            obj_fact["id"],
                            edge_conflict["id"],
                        )
                        await conn.execute(
                            "UPDATE facts SET object_entity_id = $1 WHERE id = $2",
                            tgt_uuid,
                            obj_fact["id"],
                        )
                    else:
                        await conn.execute(
                            "UPDATE facts SET validity = 'superseded', supersedes_id = $1 "
                            "WHERE id = $2",
                            edge_conflict["id"],
                            obj_fact["id"],
                        )
                    edge_facts_superseded += 1

    return {
        "facts_repointed": facts_repointed,
        "facts_superseded": facts_superseded,
        "edge_facts_repointed": edge_facts_repointed,
        "edge_facts_superseded": edge_facts_superseded,
    }


async def entity_merge(
    pool: Pool,
    source_entity_id: str,
    target_entity_id: str,
    *,
    tenant_id: str,
    extra_pools: list[Pool] | None = None,
) -> dict[str, Any]:
    """Merge a source entity into a target entity.

    Merge behavior:
    1. Re-point all facts referencing source entity_id to target entity_id,
       across the primary pool AND any extra_pools (for multi-schema setups).
       - If a conflict exists (target already has an active fact with same
         scope+predicate), keep the higher-confidence fact as active and
         supersede the lower-confidence one.
    1b. Re-point all facts referencing source as object_entity_id to target.
       - Same conflict resolution: if re-pointing would duplicate an existing
         active edge (same entity_id, scope, predicate, object_entity_id=target),
         the higher-confidence fact survives.
    2. Append source's aliases to target's alias list (deduplicated).
    3. Merge source's metadata into target's metadata (target wins on conflict).
    4. Tombstone source entity (mark as merged_into=target_entity_id, retained
       for audit, excluded from entity_resolve results).
    5. Emit a memory_event audit record for the merge.

    Args:
        pool: asyncpg connection pool.
        source_entity_id: UUID string of the entity to merge from (will be tombstoned).
        target_entity_id: UUID string of the entity to merge into (survives).
        tenant_id: Tenant scope for isolation.
        extra_pools: Additional pools to re-point facts on (for multi-butler setups
                     where facts may live in different schemas).

    Returns:
        Dict with keys:
          - target_entity_id: UUID string of the surviving entity.
          - source_entity_id: UUID string of the tombstoned entity.
          - facts_repointed: number of subject-side facts moved from source to target.
          - facts_superseded: number of subject-side facts superseded due to conflicts.
          - edge_facts_repointed: number of object-side edge facts re-pointed.
          - edge_facts_superseded: number of object-side edge facts superseded.
          - aliases_added: number of new aliases added to target.

    Raises:
        ValueError: If source or target entity not found for this tenant,
                    or if source == target.
    """
    if source_entity_id == target_entity_id:
        raise ValueError("source_entity_id and target_entity_id must be different.")

    src_uuid = uuid.UUID(source_entity_id)
    tgt_uuid = uuid.UUID(target_entity_id)

    # ---------------------------------------------------------------
    # 1. Validate + merge entity metadata + tombstone (single txn on shared schema)
    # ---------------------------------------------------------------
    async with pool.acquire() as conn:
        async with conn.transaction():
            src_row = await conn.fetchrow(
                "SELECT id, canonical_name, aliases, metadata, roles "
                "FROM shared.entities WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                src_uuid,
                tenant_id,
            )
            if src_row is None:
                raise ValueError(
                    f"Source entity '{source_entity_id}' not found for tenant '{tenant_id}'."
                )

            tgt_row = await conn.fetchrow(
                "SELECT id, canonical_name, aliases, metadata, roles "
                "FROM shared.entities WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                tgt_uuid,
                tenant_id,
            )
            if tgt_row is None:
                raise ValueError(
                    f"Target entity '{target_entity_id}' not found for tenant '{tenant_id}'."
                )

            src_metadata: dict[str, Any] = _parse_metadata(src_row["metadata"])
            if "merged_into" in src_metadata:
                raise ValueError(
                    f"Source entity '{source_entity_id}' is already tombstoned "
                    f"(merged_into={src_metadata['merged_into']!r})."
                )

            # Merge aliases (deduplicated, case-insensitive)
            src_aliases: list[str] = list(src_row["aliases"]) if src_row["aliases"] else []
            tgt_aliases: list[str] = list(tgt_row["aliases"]) if tgt_row["aliases"] else []
            tgt_alias_set = {a.lower() for a in tgt_aliases}
            new_aliases: list[str] = list(tgt_aliases)
            aliases_added = 0
            for alias in src_aliases:
                if alias.lower() not in tgt_alias_set:
                    new_aliases.append(alias)
                    tgt_alias_set.add(alias.lower())
                    aliases_added += 1

            # Merge roles (union)
            src_roles: list[str] = list(src_row["roles"]) if src_row["roles"] else []
            tgt_roles: list[str] = list(tgt_row["roles"]) if tgt_row["roles"] else []
            tgt_role_set = set(tgt_roles)
            merged_roles: list[str] = list(tgt_roles)
            for role in src_roles:
                if role not in tgt_role_set:
                    merged_roles.append(role)
                    tgt_role_set.add(role)

            # Merge metadata (target wins on conflict).
            # Strip system keys from source so flags like "unidentified" don't
            # propagate to a confirmed target entity.
            _SYSTEM_METADATA_KEYS = {"deleted_at", "merged_into", "unidentified"}
            tgt_metadata: dict[str, Any] = _parse_metadata(tgt_row["metadata"])
            src_metadata_clean = {
                k: v for k, v in src_metadata.items() if k not in _SYSTEM_METADATA_KEYS
            }
            merged_metadata = {**src_metadata_clean, **tgt_metadata}

            await conn.execute(
                "UPDATE shared.entities SET aliases = $1, metadata = $2::jsonb, roles = $3, "
                "updated_at = now() WHERE id = $4",
                new_aliases,
                json.dumps(merged_metadata),
                merged_roles,
                tgt_uuid,
            )

            # Tombstone source
            src_metadata_tombstoned = {**src_metadata, "merged_into": target_entity_id}
            await conn.execute(
                "UPDATE shared.entities SET metadata = $1::jsonb, updated_at = now() WHERE id = $2",
                json.dumps(src_metadata_tombstoned),
                src_uuid,
            )

    # ---------------------------------------------------------------
    # 2. Re-point facts across ALL pools (primary + extra)
    # ---------------------------------------------------------------
    all_pools = [pool] + (extra_pools or [])
    facts_repointed = 0
    facts_superseded = 0
    edge_facts_repointed = 0
    edge_facts_superseded = 0

    for p in all_pools:
        try:
            counts = await _repoint_facts_on_pool(p, src_uuid, tgt_uuid)
        except Exception:
            # Pool may lack facts table (not a memory schema) — skip
            continue
        facts_repointed += counts["facts_repointed"]
        facts_superseded += counts["facts_superseded"]
        edge_facts_repointed += counts["edge_facts_repointed"]
        edge_facts_superseded += counts["edge_facts_superseded"]

    # ---------------------------------------------------------------
    # 3. Emit audit event
    # ---------------------------------------------------------------
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO memory_events (event_type, tenant_id, payload)
                VALUES ('entity_merge', $1, $2::jsonb)
                """,
                tenant_id,
                json.dumps(
                    {
                        "source_entity_id": source_entity_id,
                        "target_entity_id": target_entity_id,
                        "facts_repointed": facts_repointed,
                        "facts_superseded": facts_superseded,
                        "edge_facts_repointed": edge_facts_repointed,
                        "edge_facts_superseded": edge_facts_superseded,
                        "aliases_added": aliases_added,
                    }
                ),
            )

    return {
        "target_entity_id": target_entity_id,
        "source_entity_id": source_entity_id,
        "facts_repointed": facts_repointed,
        "facts_superseded": facts_superseded,
        "edge_facts_repointed": edge_facts_repointed,
        "edge_facts_superseded": edge_facts_superseded,
        "aliases_added": aliases_added,
    }
