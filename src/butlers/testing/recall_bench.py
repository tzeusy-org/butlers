"""Recall benchmark harness for pgvector ANN (HNSW) embedding indexes.

Seeds a memory-module table (``episodes``/``facts``/``rules``) with
deterministic synthetic embeddings, then compares the planner's natural
query plan (which prefers the ANN index once the table is large enough --
see ``search.py``'s ``ORDER BY embedding <=> $1 LIMIT n`` shape) against an
*exact* brute-force scan of the same table using the same distance operator.
The fraction of true nearest neighbors the ANN path actually returns is
``recall@k``.

Forcing the exact scan is done by disabling index/bitmap scans for that one
query (``SET LOCAL enable_indexscan/enable_bitmapscan = off``), which leaves
everything else (operator, filters, LIMIT) identical -- so the only source of
divergence between the two result sets is ANN approximation error, not a
different similarity metric.

Used by:
- ``tests/modules/memory/test_memory_hnsw_indexes.py`` (small-N smoke check,
  every CI run -- verifies the harness itself works, not gated on a strict
  recall threshold).
- ``tests/migrations/test_memory_hnsw_recall_nightly.py`` (larger-N, gated on
  a recall threshold, nightly only -- see ``nightly.yml``).

Purely random high-dimensional vectors are all nearly equidistant (curse of
dimensionality), which would make recall@k trivially ~1.0 regardless of index
quality. ``synthetic_corpus`` instead draws points from a handful of random
cluster centers plus Gaussian noise, giving ANN search real nearest-neighbor
structure to approximate.

Caveat -- seed once, do not churn the table
--------------------------------------------
Call :func:`seed_embeddings` exactly once against an empty table (a freshly
migrated database, or a table that was just ``TRUNCATE``d + ``VACUUM``ed).
Repeatedly
``DELETE``-ing and re-inserting a *different* synthetic corpus into the same
table across several benchmark configurations in one process, without a
``VACUUM`` in between, leaves stale/tombstoned entries in the HNSW graph that
were never reclaimed -- this was measured to collapse recall from ~1.0 to
~0.0 on an otherwise-identical corpus. That collapse is an artifact of the
churned test table, not a real HNSW quality regression, and does not reflect
how the memory module uses these tables in practice (rows accumulate; they
are not bulk-replaced). If a future caller needs multiple configurations in
one test session, ``TRUNCATE`` + ``VACUUM`` the table between them.
"""

from __future__ import annotations

import random

import asyncpg

#: Tables in the memory module chain that carry a 384-d ``embedding`` column
#: and the minimal row shape used here (subject/predicate/content/scope/
#: tenant_id/embedding/validity). All three share this shape; ``facts`` is
#: the default because it has no additional NOT NULL columns beyond that shape.
DEFAULT_TABLE = "facts"


def _unit_vector(rng: random.Random, dim: int) -> list[float]:
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def cluster_centers(seed: int, n_clusters: int, *, dim: int = 384) -> list[list[float]]:
    """Return *n_clusters* deterministic random unit-vector cluster centers."""
    rng = random.Random(seed)
    return [_unit_vector(rng, dim) for _ in range(n_clusters)]


def sample_around_centers(
    centers: list[list[float]],
    n: int,
    *,
    seed: int,
    cluster_noise: float = 0.15,
) -> list[list[float]]:
    """Return *n* deterministic unit vectors sampled around *centers*.

    Each point is ``center + Gaussian noise`` re-normalized to unit length,
    cycling through *centers* round-robin. Deterministic for a given *seed*.
    """
    rng = random.Random(seed)
    vectors: list[list[float]] = []
    for i in range(n):
        center = centers[i % len(centers)]
        noisy = [c + rng.gauss(0.0, cluster_noise) for c in center]
        norm = sum(x * x for x in noisy) ** 0.5 or 1.0
        vectors.append([x / norm for x in noisy])
    return vectors


def synthetic_corpus(
    seed: int,
    n: int,
    *,
    dim: int = 384,
    n_clusters: int = 20,
    cluster_noise: float = 0.15,
) -> tuple[list[list[float]], list[list[float]]]:
    """Return (*n* deterministic corpus vectors, the cluster centers used).

    Points are drawn from *n_clusters* random unit-vector cluster centers plus
    Gaussian noise, then re-normalized to unit length -- giving ANN search
    real nearest-neighbor structure to approximate (pure uniform-random high-
    dimensional vectors are all nearly equidistant, which would make recall@k
    trivially ~1.0 regardless of index quality).

    The returned centers are meant to be reused by :func:`sample_around_centers`
    to draw *in-distribution* query vectors. Queries drawn from unrelated
    random centers would be out-of-distribution relative to the corpus (no
    query would have a well-defined "true" cluster of near neighbors to
    recall), so always pass these same centers when generating query vectors
    for this corpus.
    """
    centers = cluster_centers(seed, n_clusters, dim=dim)
    vectors = sample_around_centers(centers, n, seed=seed + 1, cluster_noise=cluster_noise)
    return vectors, centers


async def seed_embeddings(
    pool: asyncpg.Pool,
    *,
    table: str,
    tenant_id: str,
    vectors: list[list[float]],
) -> None:
    """Bulk-insert *vectors* into *table* as minimal synthetic rows.

    Uses a single ``INSERT ... SELECT FROM UNNEST`` round trip rather than
    one INSERT per row, so seeding thousands of rows stays fast.

    The synthetic ``subject`` embeds *tenant_id* so two calls with different
    *tenant_id* values against the same (module-scoped-fixture) table never
    collide on the ``facts`` table's partial unique index over
    ``(scope, subject, predicate)`` for entity-less active rows.
    """
    await pool.execute(
        f"""
        INSERT INTO {table}
            (subject, predicate, content, scope, tenant_id, embedding, validity)
        SELECT
            'synthetic_subj_' || $1 || '_' || s.ord::text,
            'synthetic_pred',
            'synthetic content ' || s.ord::text,
            'global',
            $1,
            s.embedding_text::vector,
            'active'
        FROM UNNEST($2::text[]) WITH ORDINALITY AS s(embedding_text, ord)
        """,
        tenant_id,
        [str(v) for v in vectors],
    )


async def _topk_ids(
    conn: asyncpg.Connection,
    *,
    table: str,
    tenant_id: str,
    query_embedding: list[float],
    k: int,
    force_exact: bool,
) -> list[str]:
    async with conn.transaction():
        if force_exact:
            # Forces a Seq Scan + in-memory sort so the result is the true
            # top-k by the same `<=>` operator the ANN index approximates.
            await conn.execute("SET LOCAL enable_indexscan = off")
            await conn.execute("SET LOCAL enable_bitmapscan = off")
        rows = await conn.fetch(
            f"""
            SELECT id FROM {table}
            WHERE tenant_id = $1
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            tenant_id,
            str(query_embedding),
            k,
        )
    return [str(r["id"]) for r in rows]


async def recall_at_k(
    pool: asyncpg.Pool,
    *,
    table: str,
    tenant_id: str,
    queries: list[list[float]],
    k: int,
) -> float:
    """Average recall@k of the planner's natural query plan vs. exact brute force.

    For each query vector, runs the same ``ORDER BY embedding <=> $1 LIMIT k``
    query twice against *table*: once under normal planner settings (which
    prefers the ANN index once the table is large enough) and once with
    index/bitmap scans disabled (forcing an exact scan). Returns the mean
    fraction of the exact top-k ids that also appear in the approximate top-k,
    averaged across all *queries*.

    Returns 0.0 if *queries* is empty.
    """
    if not queries:
        return 0.0

    total = 0.0
    async with pool.acquire() as conn:
        for query_embedding in queries:
            exact = await _topk_ids(
                conn,
                table=table,
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                k=k,
                force_exact=True,
            )
            approx = await _topk_ids(
                conn,
                table=table,
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                k=k,
                force_exact=False,
            )
            if not exact:
                continue
            overlap = len(set(exact) & set(approx))
            total += overlap / len(exact)

    return total / len(queries)
