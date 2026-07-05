"""Real-DB integration tests for the mem_007 ivfflat -> HNSW index swap.

Origin: bu-4ftb2 (pursuit slice 4 deferred from bu-qvnce.3 / PR #2903).
migrations/007_hnsw_embedding_indexes.py replaces the ``idx_episodes_embedding``,
``idx_facts_embedding``, and ``idx_rules_embedding`` ivfflat indexes (built
with ``lists = 20`` on then-empty tables in mem_001) with HNSW equivalents.

This file verifies:
  1. All three embedding indexes are actually HNSW after the full memory
     migration chain runs (not just that *an* index with that name exists).
  2. A realistic ``ORDER BY embedding <=> $1 LIMIT n`` query -- the shape used
     by ``search.py``'s ``semantic_search`` -- plans onto the new HNSW index
     rather than falling back to a sequential scan.
  3. The recall benchmark harness (``butlers.testing.recall_bench``) runs
     end-to-end at a small scale. This is a *mechanics* smoke test, not a
     quality gate -- the harness's actual recall threshold is only enforced
     at synthetic-scale in the nightly-only
     ``tests/migrations/test_memory_hnsw_recall_nightly.py``.

Local-dev requirements
----------------------
Docker must be available (testcontainers, ``pgvector/pgvector:pg17``). Tests
are automatically skipped when Docker is unavailable.

Run with::

    uv run pytest tests/modules/memory/test_memory_hnsw_indexes.py -q --tb=short
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.testing.recall_bench import (
    recall_at_k,
    sample_around_centers,
    seed_embeddings,
    synthetic_corpus,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_EMBEDDING_INDEXES = {
    "episodes": "idx_episodes_embedding",
    "facts": "idx_facts_embedding",
    "rules": "idx_rules_embedding",
}


@pytest.fixture(scope="module")
def memory_migrated_db(postgres_container) -> str:
    """A fresh DB with the full core + memory migration chain applied."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


def _indexdef(db_url: str, index_name: str) -> str | None:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :i"),
                {"i": index_name},
            ).fetchone()
        return str(row[0]) if row else None
    finally:
        engine.dispose()


def test_all_embedding_indexes_are_hnsw(memory_migrated_db: str) -> None:
    """After mem_007, episodes/facts/rules embedding indexes use the hnsw am.

    Regression guard for the ivfflat -> HNSW swap: asserts the *access method*
    actually changed, not just that an index with the expected name exists
    (an index named idx_facts_embedding could still be the old ivfflat one if
    mem_007 failed to apply).
    """
    for table, index_name in _EMBEDDING_INDEXES.items():
        indexdef = _indexdef(memory_migrated_db, index_name)
        assert indexdef is not None, f"Expected {index_name} to exist on {table}"
        assert "USING hnsw" in indexdef, (
            f"Expected {index_name} to use the hnsw access method, got: {indexdef!r}"
        )
        assert "ivfflat" not in indexdef, (
            f"Expected {index_name} to no longer be ivfflat, got: {indexdef!r}"
        )


async def _seed_and_explain(db_url: str) -> str:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    try:
        # A realistic row count for the planner to prefer the ANN index over
        # a sequential scan (verified empirically: below ~2000 rows the
        # planner favors a Seq Scan + sort for this query shape regardless of
        # the index's existence, since a full sort over a small table is
        # cheaper than the ANN index's estimated startup cost).
        corpus, _centers = synthetic_corpus(seed=1, n=2500)
        await seed_embeddings(pool, table="facts", tenant_id="plan-check", vectors=corpus)
        await pool.execute("ANALYZE facts")

        query_vec = corpus[0]
        plan_rows = await pool.fetch(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT id FROM facts
            WHERE tenant_id = 'plan-check'
            ORDER BY embedding <=> $1::vector
            LIMIT 10
            """,
            str(query_vec),
        )
        return "\n".join(r[0] for r in plan_rows)
    finally:
        await pool.close()


def test_semantic_search_query_plans_onto_hnsw_index(memory_migrated_db: str) -> None:
    """The ORDER BY embedding <=> $1 LIMIT n query shape (search.py's
    semantic_search) plans onto idx_facts_embedding rather than a Seq Scan.
    """
    plan_text = asyncio.run(_seed_and_explain(memory_migrated_db))

    assert "idx_facts_embedding" in plan_text, (
        f"Expected idx_facts_embedding in the query plan. Full plan:\n{plan_text}"
    )
    assert "Seq Scan" not in plan_text, (
        f"Expected an Index Scan, got a Seq Scan. Full plan:\n{plan_text}"
    )


async def _run_smoke_recall(db_url: str) -> float:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    try:
        corpus, centers = synthetic_corpus(seed=1, n=200, n_clusters=10)
        await seed_embeddings(pool, table="facts", tenant_id="recall-smoke", vectors=corpus)
        await pool.execute("ANALYZE facts")
        queries = sample_around_centers(centers, 20, seed=1 + 9999)
        return await recall_at_k(
            pool, table="facts", tenant_id="recall-smoke", queries=queries, k=5
        )
    finally:
        await pool.close()


def test_recall_harness_runs_end_to_end(memory_migrated_db: str) -> None:
    """The recall benchmark harness itself runs cleanly at a small scale.

    This is a mechanics smoke test, not a quality gate: it exists so that a
    broken harness (import error, SQL typo, wrong table shape) fails on every
    PR, rather than only in the nightly job. The recall *threshold* that
    actually gates on index quality lives in the nightly-only
    tests/migrations/test_memory_hnsw_recall_nightly.py, at a synthetic scale
    (thousands of rows) large enough for the measurement to be meaningful.
    """
    recall = asyncio.run(_run_smoke_recall(memory_migrated_db))

    assert 0.0 <= recall <= 1.0, f"recall@k must be a fraction, got {recall!r}"
    # Loose sanity floor only -- catches total breakage (e.g. the ANN index
    # silently returning nothing, or a broken query), not quality regressions.
    assert recall >= 0.5, f"recall harness smoke check unexpectedly low: {recall!r}"
