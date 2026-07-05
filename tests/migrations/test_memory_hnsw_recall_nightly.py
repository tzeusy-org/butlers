"""Nightly synthetic-scale recall benchmark for the memory module's HNSW indexes.

Origin: bu-4ftb2 (pursuit slice 4 deferred from bu-qvnce.3 / PR #2903).
migrations/007_hnsw_embedding_indexes.py swapped the ivfflat embedding indexes
(idx_episodes_embedding, idx_facts_embedding, idx_rules_embedding) for HNSW
(m=16, ef_construction=64 -- pgvector's own defaults). This test makes future
regressions to that index's *quality* (not just its existence -- see
tests/modules/memory/test_memory_hnsw_indexes.py for that) visible: a change
that silently degrades recall (e.g. a future migration that lowers ``m`` or
``ef_construction``, or a runtime change that overrides ``hnsw.ef_search``
down) fails this test instead of shipping unnoticed.

Uses ``butlers.testing.recall_bench``: seeds a synthetic, deterministically-
clustered corpus of SEED_ROWS embeddings into ``facts`` (representative of
all three embedding indexes, which share identical build parameters), runs
N_QUERIES nearest-neighbor queries under the planner's natural (ANN-
preferring) plan, and compares each against an exact brute-force scan of the
same table using the same ``<=>`` operator. recall@K is the fraction of true
nearest neighbors the ANN path actually returned.

Threshold rationale
--------------------
Calibrated empirically against a freshly-migrated table (see bu-4ftb2 worker
report for the full sweep): at SEED_ROWS=4000, CLUSTER_NOISE=0.15, K=10,
N_QUERIES=100, the shipped default configuration (m=16, ef_construction=64,
query-time hnsw.ef_search=40) consistently measures recall@10 in the
0.94-0.96 range across multiple corpus seeds (<1pp spread). RECALL_THRESHOLD
= 0.85 leaves a ~10pp margin below the observed baseline -- enough to absorb
minor pgvector-version/hardware variance while still catching a real
regression (e.g. disabling the index entirely does not trip this test, since
that makes the "approximate" path identical to the exact path and recall
would read 1.0 -- see test_memory_hnsw_indexes.py for that guard instead; but
a genuine quality regression, such as ef_search dropping to single digits,
craters recall well below this threshold, as verified during calibration).

Opt-in only
-----------
Marked ``nightly`` -- excluded from ``ci.yml``'s per-PR integration job
(``-m "integration and not nightly"``) and picked up by ``nightly.yml``,
which already collects ``tests/migrations/``. Run manually with::

    uv run pytest tests/migrations/test_memory_hnsw_recall_nightly.py -m "nightly or integration" -v

Requires Docker (testcontainers, ``pgvector/pgvector:pg17``); automatically
skipped when Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.testing.recall_bench import (
    recall_at_k,
    sample_around_centers,
    seed_embeddings,
    synthetic_corpus,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Deterministic seed for the synthetic corpus and query set.
SEED: int = 42

#: Row count -- representative of "thousands of rows per butler schema"
#: (current production scale; see bu-qvnce.3 evidence), with headroom.
SEED_ROWS: int = 4000

#: Cluster spread. Calibrated so the corpus has real (not degenerate) nearest-
#: neighbor structure -- see butlers.testing.recall_bench module docstring
#: for why pure uniform-random high-dim vectors would make this trivial.
CLUSTER_NOISE: float = 0.15

#: Neighbors requested per query -- matches the LIMIT search.py's
#: semantic_search typically uses.
K: int = 10

#: Query count. 100 keeps sampling variance low without materially slowing
#: the nightly job (each query issues two SELECTs against a 4000-row table).
N_QUERIES: int = 100

#: See "Threshold rationale" in the module docstring.
RECALL_THRESHOLD: float = 0.85

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.nightly,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def memory_migrated_db(postgres_container: Any) -> str:
    """A fresh DB with the full core + memory migration chain applied."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


async def _seed_and_measure_recall(db_url: str) -> float:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    try:
        corpus, centers = synthetic_corpus(
            seed=SEED,
            n=SEED_ROWS,
            cluster_noise=CLUSTER_NOISE,
        )
        # Seeded once against a table that has never held any other rows in
        # this test session -- see the recall_bench module docstring's
        # "seed once, do not churn the table" caveat for why repeated
        # DELETE+reinsert cycles without an intervening VACUUM would
        # invalidate the measurement.
        await seed_embeddings(pool, table="facts", tenant_id="recall-nightly", vectors=corpus)
        await pool.execute("ANALYZE facts")

        queries = sample_around_centers(
            centers,
            N_QUERIES,
            seed=SEED + 9999,
            cluster_noise=CLUSTER_NOISE,
        )
        return await recall_at_k(
            pool,
            table="facts",
            tenant_id="recall-nightly",
            queries=queries,
            k=K,
        )
    finally:
        await pool.close()


def test_hnsw_recall_at_synthetic_scale(memory_migrated_db: str) -> None:
    """recall@K stays at or above RECALL_THRESHOLD at SEED_ROWS synthetic scale.

    See the module docstring's "Threshold rationale" for how RECALL_THRESHOLD
    was calibrated. A failure here means the HNSW index's *approximation
    quality* regressed (build parameters, query-time ef_search, or the
    operator class), not that the index went missing entirely -- a missing
    index would make the "approximate" path identical to the exact brute-
    force path and this test would read recall=1.0, not a failure. That
    "index exists and is used" guard lives separately in
    tests/modules/memory/test_memory_hnsw_indexes.py.
    """
    recall = asyncio.run(_seed_and_measure_recall(memory_migrated_db))

    print(
        f"\n[nightly] HNSW recall@{K} on {SEED_ROWS:,} synthetic rows "
        f"(cluster_noise={CLUSTER_NOISE}, {N_QUERIES} queries): {recall:.4f} "
        f"(threshold: {RECALL_THRESHOLD})"
    )

    assert recall >= RECALL_THRESHOLD, (
        f"HNSW recall@{K} regressed: measured {recall:.4f}, "
        f"expected >= {RECALL_THRESHOLD} at {SEED_ROWS:,} synthetic rows. "
        "This means the ANN index's approximation quality got worse -- check "
        "recent changes to index build parameters (m/ef_construction in "
        "007_hnsw_embedding_indexes.py) or the query-time hnsw.ef_search GUC."
    )
