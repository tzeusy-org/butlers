"""Search functions for the Memory Butler.

Provides semantic, keyword, and hybrid search across memory types.
All functions accept an asyncpg connection pool and return ranked results.
"""

from __future__ import annotations

import importlib.util
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from asyncpg import Pool

# ---------------------------------------------------------------------------
# Load sibling modules from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent


def _load_module(name: str):
    path = _MODULE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_search_vector_mod = _load_module("search_vector")
preprocess_search_query = _search_vector_mod.preprocess_search_query
tsquery_sql = _search_vector_mod.tsquery_sql

_VALID_TABLES = frozenset({"episodes", "facts", "rules"})
# Tables that support scope filtering (facts/rules use `scope` column).
_SCOPED_TABLES = frozenset({"facts", "rules"})
# Tables that support butler-based filtering (episodes use `butler` column).
_BUTLER_TABLES = frozenset({"episodes"})

# PostgreSQL text-search configuration
_TS_CONFIG = "english"

# RRF fusion constant (standard value from the original RRF paper).
_RRF_K = 60


# ---------------------------------------------------------------------------
# Semantic search via pgvector
# ---------------------------------------------------------------------------


async def semantic_search(
    pool: Pool,
    query_embedding: list[float],
    table: str,
    *,
    limit: int = 10,
    scope: str | None = None,
) -> list[dict]:
    """Search by cosine similarity using pgvector.

    Args:
        pool: asyncpg connection pool.
        query_embedding: 384-d float vector for the query.
        table: Table name (``'episodes'``, ``'facts'``, or ``'rules'``).
        limit: Max results (default 10).
        scope: Optional scope filter (only applied for facts/rules tables).

    Returns:
        List of dicts with all table columns plus a ``similarity`` key
        (float, 0-1).  Ordered by similarity descending (most similar first).

    Raises:
        ValueError: If *table* is not one of the valid table names.
    """
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table: {table!r}. Must be one of {sorted(_VALID_TABLES)}")

    embedding_str = str(query_embedding)

    # Build WHERE clause -------------------------------------------------
    conditions: list[str] = []
    params: list = [embedding_str]
    param_idx = 2  # $1 is the embedding

    # Scope filtering: facts/rules use IN ('global', scope), episodes use butler = scope.
    if scope is not None and table in _SCOPED_TABLES:
        conditions.append(f"scope IN ('global', ${param_idx})")
        params.append(scope)
        param_idx += 1
    elif scope is not None and table in _BUTLER_TABLES:
        conditions.append(f"butler = ${param_idx}")
        params.append(scope)
        param_idx += 1

    # Facts: only return active rows.
    if table == "facts":
        conditions.append("validity = 'active'")

    # Rules: exclude forgotten (metadata->>'forgotten' IS NOT TRUE).
    if table == "rules":
        conditions.append("(metadata->>'forgotten')::boolean IS NOT TRUE")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"""
        SELECT *, 1 - (embedding <=> $1) AS similarity
        FROM {table}
        {where}
        ORDER BY embedding <=> $1
        LIMIT ${param_idx}
    """
    params.append(limit)

    rows = await pool.fetch(sql, *params)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Keyword search via tsvector
# ---------------------------------------------------------------------------


async def keyword_search(
    pool: Pool,
    query_text: str,
    table: str,
    *,
    limit: int = 10,
    scope: str | None = None,
) -> list[dict]:
    """Search by keyword using PostgreSQL full-text search.

    Uses plainto_tsquery for safe handling of user input (no special
    operator syntax required). Results are ranked by ts_rank.

    Args:
        pool: asyncpg connection pool.
        query_text: User search text (will be preprocessed).
        table: Table name ('episodes', 'facts', 'rules').
        limit: Max results (default 10).
        scope: Optional scope filter (only for facts/rules).

    Returns:
        List of dicts with keys: all table columns plus 'rank'.
        Ordered by rank descending (best match first).

    Raises:
        ValueError: If table is invalid.
    """
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table: {table!r}. Must be one of {sorted(_VALID_TABLES)}")

    cleaned_query = preprocess_search_query(query_text)
    if not cleaned_query:
        return []

    # Build WHERE clause
    conditions = [f"search_vector @@ plainto_tsquery('{_TS_CONFIG}', $1)"]
    params: list = [cleaned_query]
    param_idx = 2

    # Scope filtering: facts/rules use IN ('global', scope), episodes use butler = scope.
    if scope is not None and table in _SCOPED_TABLES:
        conditions.append(f"scope IN ('global', ${param_idx})")
        params.append(scope)
        param_idx += 1
    elif scope is not None and table in _BUTLER_TABLES:
        conditions.append(f"butler = ${param_idx}")
        params.append(scope)
        param_idx += 1

    if table == "facts":
        conditions.append("validity = 'active'")

    if table == "rules":
        conditions.append("(metadata->>'forgotten')::boolean IS NOT TRUE")

    where = " AND ".join(conditions)

    sql = f"""
        SELECT *, ts_rank(search_vector, plainto_tsquery('{_TS_CONFIG}', $1)) AS rank
        FROM {table}
        WHERE {where}
        ORDER BY rank DESC
        LIMIT ${param_idx}
    """
    params.append(limit)

    rows = await pool.fetch(sql, *params)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Hybrid search (Reciprocal Rank Fusion)
# ---------------------------------------------------------------------------


async def hybrid_search(
    pool: Pool,
    query_text: str,
    query_embedding: list[float],
    table: str,
    *,
    limit: int = 10,
    scope: str | None = None,
) -> list[dict]:
    """Hybrid search combining semantic and keyword search via RRF.

    Runs both search methods, then fuses results using Reciprocal Rank
    Fusion.  Each result gets::

        rrf_score = 1/(k + semantic_rank) + 1/(k + keyword_rank)

    where ``k=60``.  Results appearing in only one list use
    ``rank = limit + 1`` for the missing dimension.

    Args:
        pool: asyncpg connection pool.
        query_text: User search text for keyword matching.
        query_embedding: 384-d vector for semantic matching.
        table: Table name (``'episodes'``, ``'facts'``, ``'rules'``).
        limit: Max results per search method and for final output.
        scope: Optional scope filter.

    Returns:
        List of dicts with ``rrf_score``, ``semantic_rank``, and
        ``keyword_rank`` added.  Ordered by ``rrf_score`` descending.

    Raises:
        ValueError: If *table* is not one of the valid table names.
    """
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table: {table!r}. Must be one of {sorted(_VALID_TABLES)}")

    # Run both searches
    semantic_results = await semantic_search(
        pool,
        query_embedding,
        table,
        limit=limit,
        scope=scope,
    )
    keyword_results = await keyword_search(
        pool,
        query_text,
        table,
        limit=limit,
        scope=scope,
    )

    # Build rank maps keyed by id
    default_rank = limit + 1

    semantic_ranks: dict[uuid.UUID, int] = {}
    semantic_data: dict[uuid.UUID, dict] = {}
    for rank, row in enumerate(semantic_results, start=1):
        rid = row["id"]
        semantic_ranks[rid] = rank
        semantic_data[rid] = row

    keyword_ranks: dict[uuid.UUID, int] = {}
    keyword_data: dict[uuid.UUID, dict] = {}
    for rank, row in enumerate(keyword_results, start=1):
        rid = row["id"]
        keyword_ranks[rid] = rank
        keyword_data[rid] = row

    # Union all IDs
    all_ids = set(semantic_ranks.keys()) | set(keyword_ranks.keys())

    # Compute RRF scores
    fused: list[dict] = []
    for rid in all_ids:
        s_rank = semantic_ranks.get(rid, default_rank)
        k_rank = keyword_ranks.get(rid, default_rank)
        rrf_score = 1.0 / (_RRF_K + s_rank) + 1.0 / (_RRF_K + k_rank)

        # Use data from whichever search found it (prefer semantic)
        row = semantic_data.get(rid) or keyword_data[rid]
        result = dict(row)
        result["rrf_score"] = rrf_score
        result["semantic_rank"] = s_rank
        result["keyword_rank"] = k_rank
        fused.append(result)

    # Sort by RRF score descending, then by semantic rank ascending as tiebreaker
    fused.sort(key=lambda r: (-r["rrf_score"], r["semantic_rank"]))

    return fused[:limit]


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


class CompositeWeights(NamedTuple):
    """Weight parameters for composite scoring.

    Default weights: relevance=0.4, importance=0.3, recency=0.2, confidence=0.1.
    """

    relevance: float = 0.4
    importance: float = 0.3
    recency: float = 0.2
    confidence: float = 0.1


_DEFAULT_WEIGHTS = CompositeWeights()


def compute_recency_score(
    last_referenced_at: datetime | None,
    *,
    half_life_days: float = 7.0,
) -> float:
    """Compute a recency score using exponential decay.

    Args:
        last_referenced_at: When the memory was last referenced. If ``None``,
            returns 0.0 (no recency signal).
        half_life_days: Number of days for the score to halve (default 7).

    Returns:
        Float in [0.0, 1.0].  1.0 means "just now", decaying towards 0.
    """
    if last_referenced_at is None:
        return 0.0

    now = datetime.now(UTC)
    elapsed = (now - last_referenced_at).total_seconds()
    days_elapsed = max(elapsed / 86400.0, 0.0)

    decay_lambda = math.log(2) / half_life_days
    score = math.exp(-decay_lambda * days_elapsed)

    return max(0.0, min(1.0, score))


def compute_composite_score(
    relevance: float,
    importance: float,
    recency: float,
    effective_confidence: float,
    weights: CompositeWeights | None = None,
) -> float:
    """Compute a weighted composite score for memory ranking.

    Args:
        relevance: Search relevance score (0-1).
        importance: Importance rating (0-10, will be normalized to 0-1).
        recency: Recency score (0-1), typically from :func:`compute_recency_score`.
        effective_confidence: Effective confidence after decay (0-1).
        weights: Optional custom weights. Defaults to
            relevance=0.4, importance=0.3, recency=0.2, confidence=0.1.

    Returns:
        Weighted composite score as a float.
    """
    if weights is None:
        weights = _DEFAULT_WEIGHTS

    return (
        weights.relevance * relevance
        + weights.importance * (importance / 10.0)
        + weights.recency * recency
        + weights.confidence * effective_confidence
    )


# ---------------------------------------------------------------------------
# Effective confidence with time-based decay
# ---------------------------------------------------------------------------


def effective_confidence(
    confidence: float,
    decay_rate: float,
    last_confirmed_at: datetime | None,
) -> float:
    """Compute effective confidence after time-based decay.

    The effective confidence decays exponentially from the base confidence
    level based on how long ago the memory was last confirmed::

        effective = confidence * exp(-decay_rate * days_since_confirmed)

    Special cases:

    - *decay_rate == 0.0* (permanent): returns confidence unchanged.
    - *last_confirmed_at is None*: returns 0.0.

    Args:
        confidence: Base confidence level (0.0 to 1.0).
        decay_rate: Rate of exponential decay per day.
        last_confirmed_at: When confidence was last confirmed/reset.

    Returns:
        Float in [0.0, confidence], the decayed confidence value.
    """
    if last_confirmed_at is None:
        return 0.0

    if decay_rate == 0.0:
        return confidence

    now = datetime.now(UTC)
    elapsed_seconds = (now - last_confirmed_at).total_seconds()
    days_elapsed = max(elapsed_seconds / 86400.0, 0.0)

    return confidence * math.exp(-decay_rate * days_elapsed)


# ---------------------------------------------------------------------------
# High-level recall (primary retrieval entry point)
# ---------------------------------------------------------------------------


async def recall(
    pool: Pool,
    topic: str,
    embedding_engine,  # EmbeddingEngine instance
    *,
    scope: str | None = None,
    limit: int = 10,
    min_confidence: float = 0.2,
    weights: CompositeWeights | None = None,
) -> list[dict]:
    """High-level composite-scored retrieval of relevant facts and rules.

    This is the primary retrieval entry point. It:
    1. Embeds the topic text
    2. Runs hybrid search on both facts and rules tables
    3. Computes composite scores (relevance, importance, recency, confidence)
    4. Filters by minimum effective confidence
    5. Bumps reference counts on returned results
    6. Returns results sorted by composite score descending

    Args:
        pool: asyncpg connection pool.
        topic: Natural language query/topic.
        embedding_engine: EmbeddingEngine instance for embedding the topic.
        scope: Optional scope filter.
        limit: Max results to return (default 10).
        min_confidence: Minimum effective confidence threshold (default 0.2).
        weights: Optional custom composite weights.

    Returns:
        List of dicts with ``composite_score`` and ``memory_type`` added.
        Sorted by composite_score descending.
    """
    query_embedding = embedding_engine.embed(topic)

    # Search both facts and rules
    facts_results = await hybrid_search(
        pool,
        topic,
        query_embedding,
        "facts",
        limit=limit,
        scope=scope,
    )
    rules_results = await hybrid_search(
        pool,
        topic,
        query_embedding,
        "rules",
        limit=limit,
        scope=scope,
    )

    # Tag each result with its memory type
    for r in facts_results:
        r["memory_type"] = "fact"
    for r in rules_results:
        r["memory_type"] = "rule"

    all_results = facts_results + rules_results

    # Compute composite scores and filter
    max_rrf = 2.0 / (_RRF_K + 1)
    scored: list[dict] = []
    for r in all_results:
        # Normalise rrf_score to [0, 1]
        relevance_raw = r.get("rrf_score", 0.0)
        relevance = min(relevance_raw / max_rrf, 1.0) if max_rrf > 0 else 0.0

        importance = r.get("importance", 5.0)
        recency = compute_recency_score(r.get("last_referenced_at"))
        confidence = r.get("confidence", 1.0)

        # Filter by effective confidence threshold
        if confidence < min_confidence:
            continue

        composite = compute_composite_score(relevance, importance, recency, confidence, weights)
        r["composite_score"] = composite
        scored.append(r)

    # Sort by composite score descending
    scored.sort(key=lambda x: -x["composite_score"])
    scored = scored[:limit]

    # Bump reference counts for returned results
    for r in scored:
        table = "facts" if r["memory_type"] == "fact" else "rules"
        await pool.execute(
            f"UPDATE {table} SET reference_count = reference_count + 1, "
            f"last_referenced_at = now() WHERE id = $1",
            r["id"],
        )

    return scored


# ---------------------------------------------------------------------------
# General-purpose search
# ---------------------------------------------------------------------------

_VALID_SEARCH_MODES = frozenset({"semantic", "keyword", "hybrid"})
_VALID_SEARCH_TYPES = frozenset({"episode", "fact", "rule"})
_TYPE_TO_TABLE: dict[str, str] = {"episode": "episodes", "fact": "facts", "rule": "rules"}


async def search(
    pool: Pool,
    query: str,
    embedding_engine,
    *,
    types: list[str] | None = None,
    scope: str | None = None,
    mode: str = "hybrid",
    limit: int = 10,
    min_confidence: float = 0.0,
) -> list[dict]:
    """General-purpose search across memory types.

    Supports three search modes:
    - 'hybrid' (default): Combined semantic + keyword via RRF
    - 'semantic': Vector similarity only
    - 'keyword': Full-text search only

    Args:
        pool: asyncpg connection pool.
        query: Search query text.
        embedding_engine: EmbeddingEngine for embedding (used in semantic/hybrid).
        types: Memory types to search ('episode', 'fact', 'rule'). Defaults to all.
        scope: Optional scope filter.
        mode: Search mode ('semantic', 'keyword', 'hybrid').
        limit: Max results per type (default 10).
        min_confidence: Minimum confidence filter (default 0.0).

    Returns:
        List of dicts with 'memory_type' added, sorted by relevance.

    Raises:
        ValueError: If mode or types are invalid.
    """
    if mode not in _VALID_SEARCH_MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Must be one of {sorted(_VALID_SEARCH_MODES)}")

    if types is None:
        types = list(_VALID_SEARCH_TYPES)
    else:
        for t in types:
            if t not in _VALID_SEARCH_TYPES:
                raise ValueError(
                    f"Invalid type: {t!r}. Must be one of {sorted(_VALID_SEARCH_TYPES)}"
                )

    all_results: list[dict] = []

    # Embed once for semantic/hybrid modes
    query_embedding = None
    if mode in ("semantic", "hybrid"):
        query_embedding = embedding_engine.embed(query)

    for mem_type in types:
        table = _TYPE_TO_TABLE[mem_type]

        if mode == "semantic":
            results = await semantic_search(pool, query_embedding, table, limit=limit, scope=scope)
        elif mode == "keyword":
            results = await keyword_search(pool, query, table, limit=limit, scope=scope)
        else:  # hybrid
            results = await hybrid_search(
                pool, query, query_embedding, table, limit=limit, scope=scope
            )

        # Tag with memory type
        for r in results:
            r["memory_type"] = mem_type

        # Filter by confidence if applicable
        if min_confidence > 0:
            results = [r for r in results if r.get("confidence", 1.0) >= min_confidence]

        all_results.extend(results)

    # Sort by the relevant score field
    if mode == "semantic":
        all_results.sort(key=lambda r: -r.get("similarity", 0))
    elif mode == "keyword":
        all_results.sort(key=lambda r: -r.get("rank", 0))
    else:
        all_results.sort(key=lambda r: -r.get("rrf_score", 0))

    return all_results[:limit]
