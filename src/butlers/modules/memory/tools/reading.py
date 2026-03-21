"""Memory reading tools — search, recall, and retrieve."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search, _serialize_row, _storage

logger = logging.getLogger(__name__)


async def memory_search(
    pool: Pool,
    embedding_engine,
    query: str,
    *,
    types: list[str] | None = None,
    scope: str | None = None,
    mode: str = "hybrid",
    limit: int = 10,
    min_confidence: float = 0.2,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search across memory types using hybrid, semantic, or keyword mode.

    Delegates to _search.search() and serializes the results for JSON output.

    Args:
        filters: Optional dict of AND-conditions applied at the search layer.
            Supported keys: scope, entity_id, predicate, source_butler,
            time_from, time_to, retention_class, sensitivity.
            Unrecognized keys are silently ignored.
    """
    results = await _search.search(
        pool,
        query,
        embedding_engine,
        types=types,
        scope=scope,
        mode=mode,
        limit=limit,
        min_confidence=min_confidence,
        filters=filters,
    )
    return [_serialize_row(r) for r in results]


async def memory_recall(
    pool: Pool,
    embedding_engine,
    topic: str,
    *,
    scope: str | None = None,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """High-level composite-scored retrieval of relevant facts and rules.

    Delegates to _search.recall() and serializes the results for JSON output.

    Args:
        filters: Optional dict of AND-conditions. Supported keys: scope,
            entity_id, predicate, source_butler, time_from, time_to,
            retention_class, sensitivity. Unrecognized keys are silently ignored.
        request_context: Optional dict with 'tenant_id' and 'request_id'.
    """
    tenant_id = "shared"
    if isinstance(request_context, dict):
        rc_tenant = request_context.get("tenant_id")
        if isinstance(rc_tenant, str) and rc_tenant.strip():
            tenant_id = rc_tenant.strip()

    results = await _search.recall(
        pool,
        topic,
        embedding_engine,
        scope=scope,
        limit=limit,
        filters=filters,
        tenant_id=tenant_id,
    )
    return [_serialize_row(r) for r in results]


async def memory_get(
    pool: Pool,
    memory_type: str,
    memory_id: str,
) -> dict[str, Any] | None:
    """Retrieve a specific memory by type and ID.

    Converts the string memory_id to a UUID, delegates to _storage.get_memory(),
    and serializes the result for JSON output.
    """
    result = await _storage.get_memory(pool, memory_type, uuid.UUID(memory_id))
    if result is None:
        return None
    return _serialize_row(result)


async def memory_catalog_search(
    pool: Pool,
    embedding_engine,
    query: str,
    *,
    memory_type: str | None = None,
    limit: int = 10,
    mode: str = "hybrid",
) -> list[dict[str, Any]]:
    """Search the shared memory catalog for cross-butler memory discovery.

    Delegates to _search.search_catalog() and serializes results for JSON output.
    """
    results = await _search.search_catalog(
        pool,
        query,
        embedding_engine,
        memory_type=memory_type,
        limit=limit,
        mode=mode,
    )
    return [_serialize_row(r) for r in results]


_PREDICATE_RESULT_COLUMNS = (
    "name, scope, expected_subject_type, expected_object_type,"
    " is_edge, is_temporal, description, example_json"
)

# RRF constant K — standard value that balances precision/recall.
_RRF_K = 60


def _rrf_fuse(ranked_lists: list[list[str]]) -> list[tuple[str, float]]:
    """Fuse multiple ranked result lists using Reciprocal Rank Fusion.

    Args:
        ranked_lists: Each inner list is a sequence of predicate names ordered
            by relevance (most relevant first) for one retrieval signal.

    Returns:
        List of ``(name, score)`` tuples sorted by fused score descending.
        Score = SUM(1 / (K + rank_i)) across all signals where the name
        appears (rank is 1-based).
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, name in enumerate(ranked, start=1):
            scores[name] = scores.get(name, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(scores.items(), key=lambda t: t[1], reverse=True)


async def predicate_search(
    pool: Pool,
    query: str,
    *,
    scope: str | None = None,
    embedding_engine: Any | None = None,
) -> list[dict[str, Any]]:
    """Search predicate_registry using hybrid retrieval with RRF fusion.

    When ``query`` is empty all registered predicates are returned ordered by
    name (equivalent to predicate_list).

    When ``query`` is non-empty, three complementary signals are combined via
    Reciprocal Rank Fusion (RRF):

    1. **Trigram** — fuzzy name matching via ``pg_trgm`` similarity.
       Catches typos and partial matches (e.g. ``"parnet"`` → ``parent_of``).
    2. **Full-text** — weighted tsvector search on name (weight A) and
       description (weight B). Handles stemming and multi-word queries.
    3. **Semantic** — cosine similarity between the query embedding and each
       predicate's ``description_embedding``.  Catches conceptual matches
       (e.g. ``"dad"`` → ``parent_of``).  Requires *embedding_engine* and
       the ``description_embedding`` column to be populated.

    If ``pg_trgm`` or semantic embeddings are not available (e.g. in test
    environments), the function falls back gracefully to the signals that
    are available.

    The optional ``scope`` parameter pre-filters by the ``scope`` column
    (domain namespace added in mem_023: health, relationship, finance, home,
    global).

    Args:
        pool: asyncpg connection pool.
        query: Search string. Empty string returns all predicates ordered by name.
        scope: Optional filter on the ``scope`` column (exact match).
        embedding_engine: Optional EmbeddingEngine instance for semantic search.
            When None the semantic signal is skipped.

    Returns:
        List of dicts with keys: name, scope, expected_subject_type,
        expected_object_type, is_edge, is_temporal, description, example_json, score.
        ``example_json`` is a JSONB object with keys ``content`` and optionally
        ``metadata`` showing a concrete usage example, or None if not set.
        When query is empty, score is always 0.0 and results are ordered by
        name ASC.
    """
    # -------------------------------------------------------------------------
    # Empty query → return all predicates ordered by name (no scoring needed).
    # -------------------------------------------------------------------------
    if not query:
        base_select = f"SELECT {_PREDICATE_RESULT_COLUMNS} FROM predicate_registry"
        conditions: list[str] = []
        params: list[Any] = []
        if scope is not None:
            params.append(scope)
            conditions.append(f"scope = ${len(params)}")
        if conditions:
            base_select += " WHERE " + " AND ".join(conditions)
        base_select += " ORDER BY name ASC"
        rows = await pool.fetch(base_select, *params)
        result = []
        for row in rows:
            d = dict(row)
            d["score"] = 0.0
            result.append(d)
        return result

    # -------------------------------------------------------------------------
    # Build scope filter fragment (shared across all three signal queries).
    # Each signal query places its primary param at $1, so the scope value
    # is always at $2.  We build the fragment with the correct offset.
    # -------------------------------------------------------------------------
    scope_params: list[Any] = []
    if scope is not None:
        scope_params.append(scope)

    # Helper: build " AND scope = $<offset>" when scope is set.
    # offset = 1-based position of scope_params[0] in the full params list.
    def _scope_extra(offset: int) -> str:
        if not scope_params:
            return ""
        return f" AND scope = ${offset}"

    # -------------------------------------------------------------------------
    # Signal 1: Trigram fuzzy matching on predicate name.
    # similarity(name, query) > 0.3 — candidates ordered by score DESC.
    # Falls back to empty list if pg_trgm is not installed.
    # -------------------------------------------------------------------------
    trigram_ranked: list[str] = []
    try:
        trgm_params: list[Any] = [query] + scope_params
        trgm_sql = (
            f"SELECT name FROM predicate_registry"
            f" WHERE similarity(name, $1) > 0.3"
            f"{_scope_extra(2)}"
            f" ORDER BY similarity(name, $1) DESC"
        )
        trgm_rows = await pool.fetch(trgm_sql, *trgm_params)
        trigram_ranked = [r["name"] for r in trgm_rows]
    except Exception:
        # pg_trgm not available or query error — skip this signal.
        logger.debug("Predicate search: trigram signal failed, skipping.", exc_info=True)

    # -------------------------------------------------------------------------
    # Signal 2: Full-text search on search_vector (name A + description B).
    # Falls back to empty list if search_vector column does not exist yet.
    # -------------------------------------------------------------------------
    fts_ranked: list[str] = []
    try:
        fts_params: list[Any] = [query] + scope_params
        fts_sql = (
            f"SELECT name FROM predicate_registry"
            f" WHERE search_vector @@ plainto_tsquery('english', $1)"
            f"{_scope_extra(2)}"
            f" ORDER BY ts_rank(search_vector, plainto_tsquery('english', $1)) DESC"
        )
        fts_rows = await pool.fetch(fts_sql, *fts_params)
        fts_ranked = [r["name"] for r in fts_rows]
    except Exception:
        # search_vector column not populated or extension unavailable — skip.
        logger.debug("Predicate search: full-text signal failed, skipping.", exc_info=True)

    # -------------------------------------------------------------------------
    # Signal 3: Semantic similarity on description_embedding.
    # Requires both an embedding_engine and populated description_embedding column.
    # Falls back to empty list when unavailable.
    # -------------------------------------------------------------------------
    semantic_ranked: list[str] = []
    if embedding_engine is not None:
        try:
            query_embedding = embedding_engine.embed(query)
            embedding_str = str(query_embedding)
            sem_params: list[Any] = [embedding_str] + scope_params
            sem_sql = (
                f"SELECT name FROM predicate_registry"
                f" WHERE description_embedding IS NOT NULL"
                f"{_scope_extra(2)}"
                f" ORDER BY description_embedding <=> $1::vector ASC"
            )
            sem_rows = await pool.fetch(sem_sql, *sem_params)
            semantic_ranked = [r["name"] for r in sem_rows]
        except Exception:
            # vector extension not available or embeddings not populated — skip.
            logger.debug("Predicate search: semantic signal failed, skipping.", exc_info=True)

    # -------------------------------------------------------------------------
    # RRF fusion of all available signals.
    # If no signal returned any results, fall back to simple prefix search.
    # -------------------------------------------------------------------------
    all_signals = [s for s in [trigram_ranked, fts_ranked, semantic_ranked] if s]

    if not all_signals:
        # Fallback: ILIKE prefix + description substring (original behaviour).
        # $1 = name prefix, $2 = description substring, $3 = scope (if set).
        fb_params: list[Any] = [query.lower(), f"%{query.lower()}%"] + scope_params
        fb_sql = (
            f"SELECT {_PREDICATE_RESULT_COLUMNS}"
            f" FROM predicate_registry"
            f" WHERE (lower(name) LIKE $1 || '%' OR lower(COALESCE(description, '')) LIKE $2)"
            f"{_scope_extra(3)}"
            f" ORDER BY name ASC"
        )
        fb_rows = await pool.fetch(fb_sql, *fb_params)
        return [dict(row) | {"score": 0.0} for row in fb_rows]

    fused = _rrf_fuse(all_signals)  # [(name, score), …]
    fused_names = [name for name, _ in fused]

    # -------------------------------------------------------------------------
    # Fetch full metadata for all fused candidates in a single query.
    # $1 = fused_names array, $2 = scope (if set).
    # -------------------------------------------------------------------------
    if not fused_names:
        return []

    meta_params: list[Any] = [fused_names] + scope_params
    meta_sql = (
        f"SELECT {_PREDICATE_RESULT_COLUMNS}"
        f" FROM predicate_registry"
        f" WHERE name = ANY($1)"
        f"{_scope_extra(2)}"
    )
    meta_rows = await pool.fetch(meta_sql, *meta_params)
    meta_map: dict[str, dict[str, Any]] = {r["name"]: dict(r) for r in meta_rows}

    # Reconstruct results in RRF order, attaching score.
    results: list[dict[str, Any]] = []
    for name, score in fused:
        if name in meta_map:
            row = meta_map[name]
            row["score"] = score
            results.append(row)

    return results
