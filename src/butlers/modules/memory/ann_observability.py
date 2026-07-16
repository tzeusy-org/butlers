"""Bounded, read-only health checks for local memory HNSW indexes.

This is deliberately narrower than a benchmark.  A true recall calculation
needs an exact nearest-neighbour query, which is a sequential scan.  The probe
therefore performs that comparison only when PostgreSQL's catalogue estimates
that the *entire searchable corpus* is small enough to fit under a hard cap.
Larger tables report an explicit degraded result and retain their churn and
statistics-freshness observations; they are never scanned just to produce a
number.

The job returns aggregate counts and ratios only.  Query embeddings, IDs,
tenant IDs, and memory text remain inside the database transaction and are not
logged or returned to the scheduler's ``last_result`` surface.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

logger = logging.getLogger(__name__)

HNSW_INDEXES = {
    "episodes": "idx_episodes_embedding",
    "facts": "idx_facts_embedding",
    "rules": "idx_rules_embedding",
}

# Production safety budget.  Recall requires one exact scan for every sampled
# query, so strict caps matter more than metric coverage.  This cap is checked
# from pg_class before any sample or vector query is issued.
EXACT_CORPUS_MAX_ROWS = 2_000
# PostgreSQL statistics can be stale.  A physical heap-page cap prevents a
# stale low ``reltuples`` estimate from admitting a wide exact scan anyway.
EXACT_CORPUS_MAX_PAGES = 1_024
DEFAULT_SAMPLE_QUERIES = 2
DEFAULT_K = 10
SAMPLE_BLOCKS = 8
STATEMENT_TIMEOUT_MS = 250
LOCK_TIMEOUT_MS = 100

RECALL_WARNING_THRESHOLD = 0.90
RECALL_CRITICAL_THRESHOLD = 0.85
CHURN_WARNING_RATIO = 0.10
CHURN_CRITICAL_RATIO = 0.25


def _search_predicate(table: str) -> str:
    """Return the local semantic-search liveness predicate for *table*."""
    if table == "facts":
        return "validity IN ('active', 'fading')"
    if table == "rules":
        return "(metadata->>'forgotten')::boolean IS NOT TRUE"
    return "TRUE"


def _as_int(value: Any) -> int:
    return int(value or 0)


def _as_timestamp(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _timestamp_value(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _newest_timestamp(*values: Any) -> datetime | None:
    timestamps = [timestamp for value in values if (timestamp := _as_timestamp(value)) is not None]
    return max(timestamps) if timestamps else None


def _sample_percent(relpages: int) -> float:
    """Return a bounded expected page sample percentage for TABLESAMPLE SYSTEM."""
    pages = max(relpages, 1)
    return min(100.0, max(0.01, (SAMPLE_BLOCKS / pages) * 100.0))


def _walk_plan(node: Any, expected_index: str) -> bool:
    """Return whether a JSON EXPLAIN plan uses *expected_index*."""
    if isinstance(node, dict):
        if node.get("Index Name") == expected_index:
            return True
        return any(_walk_plan(value, expected_index) for value in node.values())
    if isinstance(node, list):
        return any(_walk_plan(value, expected_index) for value in node)
    return False


def _plan_uses_index(plan: Any, expected_index: str) -> bool:
    """Handle asyncpg JSON codecs that return either decoded JSON or text."""
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            return False
    return _walk_plan(plan, expected_index)


def _churn_observation(row: Any, *, now: datetime) -> tuple[dict[str, Any], str]:
    """Build a non-sensitive stats/churn summary and its health classification."""
    estimated_rows = max(_as_int(row["estimated_rows"]), _as_int(row["n_live_tup"]))
    dead_tuples = _as_int(row["n_dead_tup"])
    modified_since_analyze = _as_int(row["n_mod_since_analyze"])
    denominator = max(estimated_rows, 1)
    dead_ratio = dead_tuples / denominator
    modified_ratio = modified_since_analyze / denominator
    analyzed_at = _newest_timestamp(row["last_analyze"], row["last_autoanalyze"])
    vacuumed_at = _newest_timestamp(row["last_vacuum"], row["last_autovacuum"])
    statistics_age_hours = (
        round(max(0.0, (now - analyzed_at).total_seconds()) / 3600, 2)
        if analyzed_at is not None
        else None
    )

    status = "healthy"
    if analyzed_at is None and estimated_rows > 0:
        status = "degraded"
    if dead_ratio >= CHURN_CRITICAL_RATIO or modified_ratio >= CHURN_CRITICAL_RATIO:
        status = "critical"
    elif dead_ratio >= CHURN_WARNING_RATIO or modified_ratio >= CHURN_WARNING_RATIO:
        status = "warning"

    return (
        {
            "estimated_rows": estimated_rows,
            "dead_tuple_ratio": round(dead_ratio, 4),
            "modified_since_analyze_ratio": round(modified_ratio, 4),
            # PostgreSQL resets these counters when its statistics reset.  They
            # are included as a directional churn signal, not as a time-window
            # rate, and contain no memory data.
            "writes_since_stats_reset": {
                "inserts": _as_int(row["n_tup_ins"]),
                "updates": _as_int(row["n_tup_upd"]),
                "deletes": _as_int(row["n_tup_del"]),
            },
            "last_analyzed_at": _timestamp_value(analyzed_at),
            "statistics_age_hours": statistics_age_hours,
            "last_vacuumed_at": _timestamp_value(vacuumed_at),
        },
        status,
    )


def _recall_health(recall: float) -> str:
    if recall < RECALL_CRITICAL_THRESHOLD:
        return "critical"
    if recall < RECALL_WARNING_THRESHOLD:
        return "warning"
    return "healthy"


def _combine_health(*statuses: str) -> str:
    """Combine health states, preserving explicit no-data/degraded honesty."""
    precedence = {
        "healthy": 0,
        "no_data": 1,
        "degraded": 2,
        "warning": 3,
        "critical": 4,
    }
    return max(statuses, key=lambda status: precedence[status])


def _recommended_action(*, health: str, recall: dict[str, Any], churn: dict[str, Any]) -> str:
    """Return an operator action without prescribing an automatic maintenance write."""
    if recall.get("reason") == "hnsw_index_missing":
        return "apply_or_repair_memory_hnsw_migration"
    if recall.get("reason") in {
        "corpus_exceeds_exact_row_cap",
        "corpus_exceeds_exact_page_cap",
    }:
        return "use_nightly_synthetic_recall_or_plan_offline_rebenchmark"
    if recall.get("reason") == "hnsw_plan_not_selected":
        return "inspect_hnsw_query_plan_and_runtime_configuration"
    if recall.get("status") == "measured" and health in {"warning", "critical"}:
        return "inspect_hnsw_ef_search_and_plan_offline_maintenance"
    if churn.get("last_analyzed_at") is None:
        return "run_analyze_during_planned_maintenance"
    if (
        churn.get("dead_tuple_ratio", 0) >= CHURN_WARNING_RATIO
        or churn.get("modified_since_analyze_ratio", 0) >= CHURN_WARNING_RATIO
    ):
        return "review_memory_churn_and_plan_maintenance"
    return "none"


async def _load_table_stats(conn: Connection, *, table: str, index_name: str) -> Any:
    """Read catalog and stats views only; never inspect memory content."""
    return await conn.fetchrow(
        """
        SELECT
            c.reltuples::bigint AS estimated_rows,
            c.relpages,
            COALESCE(s.n_live_tup, 0)::bigint AS n_live_tup,
            COALESCE(s.n_dead_tup, 0)::bigint AS n_dead_tup,
            COALESCE(s.n_tup_ins, 0)::bigint AS n_tup_ins,
            COALESCE(s.n_tup_upd, 0)::bigint AS n_tup_upd,
            COALESCE(s.n_tup_del, 0)::bigint AS n_tup_del,
            COALESCE(s.n_mod_since_analyze, 0)::bigint AS n_mod_since_analyze,
            s.last_analyze,
            s.last_autoanalyze,
            s.last_vacuum,
            s.last_autovacuum,
            EXISTS (
                SELECT 1
                FROM pg_index AS i
                JOIN pg_class AS idx ON idx.oid = i.indexrelid
                JOIN pg_am AS am ON am.oid = idx.relam
                WHERE i.indrelid = c.oid
                  AND idx.relname = $2
                  AND am.amname = 'hnsw'
                  AND EXISTS (
                      SELECT 1
                      FROM unnest(i.indclass) AS indclass(opclass_oid)
                      JOIN pg_opclass AS opclass ON opclass.oid = indclass.opclass_oid
                      WHERE opclass.opcname = 'vector_cosine_ops'
                  )
            ) AS has_hnsw
        FROM pg_class AS c
        LEFT JOIN pg_stat_user_tables AS s ON s.relid = c.oid
        WHERE c.oid = to_regclass($1)
        """,
        table,
        index_name,
    )


async def _sample_query(
    conn: Connection,
    *,
    table: str,
    sample_percent: float,
    sample_seed: int,
) -> Any:
    """Read one query vector from a bounded physical-page sample."""
    async with conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        return await conn.fetchrow(
            f"""
            SELECT tenant_id, embedding
            FROM {table} TABLESAMPLE SYSTEM ($1) REPEATABLE ($2)
            WHERE embedding IS NOT NULL
              AND {_search_predicate(table)}
            LIMIT 1
            """,
            sample_percent,
            sample_seed,
        )


async def _topk_ids(
    conn: Connection,
    *,
    table: str,
    query_embedding: Any,
    tenant_id: Any,
    k: int,
    exact: bool,
) -> list[str]:
    """Return transient top-k IDs under either HNSW or exact planner settings."""
    async with conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        if exact:
            await conn.execute("SET LOCAL enable_indexscan = off")
            await conn.execute("SET LOCAL enable_indexonlyscan = off")
            await conn.execute("SET LOCAL enable_bitmapscan = off")
        else:
            await conn.execute("SET LOCAL enable_seqscan = off")

        rows = await conn.fetch(
            f"""
            SELECT id
            FROM {table}
            WHERE tenant_id = $1
              AND embedding IS NOT NULL
              AND {_search_predicate(table)}
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            tenant_id,
            str(query_embedding),
            k,
        )
    return [str(row["id"]) for row in rows]


async def _hnsw_plan_selected(
    conn: Connection,
    *,
    table: str,
    index_name: str,
    query_embedding: Any,
    tenant_id: Any,
    k: int,
) -> bool:
    """Confirm the forced approximate branch actually plans onto the HNSW index."""
    async with conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = await conn.fetchval(
            f"""
            EXPLAIN (FORMAT JSON, COSTS FALSE)
            SELECT id
            FROM {table}
            WHERE tenant_id = $1
              AND embedding IS NOT NULL
              AND {_search_predicate(table)}
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            tenant_id,
            str(query_embedding),
            k,
        )
    return _plan_uses_index(plan, index_name)


async def _measure_recall(
    conn: Connection,
    *,
    table: str,
    index_name: str,
    relpages: int,
    sample_queries: int,
    k: int,
    sample_seed: int,
) -> tuple[dict[str, Any], str]:
    """Compare HNSW results with exact results for a tiny sampled query set."""
    sample_percent = _sample_percent(relpages)
    recalls: list[float] = []

    for offset in range(sample_queries):
        try:
            query = await _sample_query(
                conn,
                table=table,
                sample_percent=sample_percent,
                sample_seed=sample_seed + offset,
            )
            if query is None:
                continue
            if not await _hnsw_plan_selected(
                conn,
                table=table,
                index_name=index_name,
                query_embedding=query["embedding"],
                tenant_id=query["tenant_id"],
                k=k,
            ):
                return {"status": "degraded", "reason": "hnsw_plan_not_selected"}, "degraded"

            exact = await _topk_ids(
                conn,
                table=table,
                query_embedding=query["embedding"],
                tenant_id=query["tenant_id"],
                k=k,
                exact=True,
            )
            if len(exact) < k:
                continue
            approximate = await _topk_ids(
                conn,
                table=table,
                query_embedding=query["embedding"],
                tenant_id=query["tenant_id"],
                k=k,
                exact=False,
            )
            recalls.append(len(set(exact) & set(approximate)) / len(exact))
        except Exception:
            logger.warning(
                "memory ANN recall probe failed for table=%s; preserving scheduler availability",
                table,
            )
            return {"status": "degraded", "reason": "probe_query_failed"}, "degraded"

    if not recalls:
        return {"status": "no_data", "reason": "insufficient_live_rows"}, "no_data"

    recall = round(sum(recalls) / len(recalls), 4)
    return (
        {
            "status": "measured",
            "queries_compared": len(recalls),
            "recall_at_k": recall,
        },
        _recall_health(recall),
    )


async def run_ann_observability(
    pool: Pool,
    *,
    tables: tuple[str, ...] = tuple(HNSW_INDEXES),
    sample_queries: int = DEFAULT_SAMPLE_QUERIES,
    k: int = DEFAULT_K,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return live-safe aggregate ANN recall, churn, and freshness health.

    ``tables`` is intentionally restricted to the local HNSW-backed memory
    tables.  ``public.memory_catalog`` is IVFFlat-backed and belongs to the
    separate catalog observability work; it is never queried here.
    """
    if not tables or any(table not in HNSW_INDEXES for table in tables):
        raise ValueError(f"tables must be a non-empty subset of {sorted(HNSW_INDEXES)}")
    if sample_queries <= 0:
        raise ValueError("sample_queries must be positive")
    if k <= 0:
        raise ValueError("k must be positive")

    current_time = now or datetime.now(UTC)
    result_tables: dict[str, Any] = {}

    async with pool.acquire() as conn:
        for table in tables:
            index_name = HNSW_INDEXES[table]
            try:
                row = await _load_table_stats(conn, table=table, index_name=index_name)
            except Exception:
                logger.warning(
                    "memory ANN observability could not inspect table=%s", table, exc_info=True
                )
                result_tables[table] = {
                    "health": "degraded",
                    "reason": "catalog_query_failed",
                }
                continue

            if row is None:
                result_tables[table] = {
                    "health": "degraded",
                    "reason": "memory_table_missing",
                }
                continue

            churn, churn_health = _churn_observation(row, now=current_time)
            if not bool(row["has_hnsw"]):
                recall = {"status": "degraded", "reason": "hnsw_index_missing"}
                health = _combine_health(churn_health, "degraded")
                result_tables[table] = {
                    "health": health,
                    "reason": "hnsw_index_missing",
                    "churn": churn,
                    "recall": recall,
                    "recommended_action": _recommended_action(
                        health=health, recall=recall, churn=churn
                    ),
                }
                continue

            estimated_rows = churn["estimated_rows"]
            relpages = _as_int(row["relpages"])
            if estimated_rows > EXACT_CORPUS_MAX_ROWS or relpages > EXACT_CORPUS_MAX_PAGES:
                reason = (
                    "corpus_exceeds_exact_row_cap"
                    if estimated_rows > EXACT_CORPUS_MAX_ROWS
                    else "corpus_exceeds_exact_page_cap"
                )
                recall = {"status": "degraded", "reason": reason}
                health = _combine_health(churn_health, "degraded")
                result_tables[table] = {
                    "health": health,
                    "churn": churn,
                    "recall": recall,
                    "recommended_action": _recommended_action(
                        health=health, recall=recall, churn=churn
                    ),
                }
                continue

            recall, recall_health = await _measure_recall(
                conn,
                table=table,
                index_name=index_name,
                relpages=relpages,
                sample_queries=sample_queries,
                k=k,
                sample_seed=current_time.date().toordinal() + len(table),
            )
            health = _combine_health(churn_health, recall_health)
            result_tables[table] = {
                "health": health,
                "churn": churn,
                "recall": recall,
                "recommended_action": _recommended_action(
                    health=health, recall=recall, churn=churn
                ),
            }

    overall_health = _combine_health(*(entry["health"] for entry in result_tables.values()))
    return {
        "health": overall_health,
        "safety": {
            "read_only": True,
            "exact_corpus_max_rows": EXACT_CORPUS_MAX_ROWS,
            "exact_corpus_max_pages": EXACT_CORPUS_MAX_PAGES,
            "sample_queries_per_table": sample_queries,
            "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        },
        "thresholds": {
            "recall_warning": RECALL_WARNING_THRESHOLD,
            "recall_critical": RECALL_CRITICAL_THRESHOLD,
            "churn_warning_ratio": CHURN_WARNING_RATIO,
            "churn_critical_ratio": CHURN_CRITICAL_RATIO,
        },
        "tables": result_tables,
    }
