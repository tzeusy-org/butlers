"""Bounded, read-only IVFFlat recall measurement for ``memory_catalog``.

This module is intentionally separate from :mod:`butlers.testing.recall_bench`:
that benchmark prepares isolated data and adjusts planner settings for HNSW
experiments, neither of which is appropriate for a live catalog observation.

The comparator only returns identifiers inside the current Python process to
calculate overlap.  Every public result contains aggregate counts and timing,
never catalog content, provenance, result IDs, or query vectors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from butlers.db import db_params_from_env
from butlers.modules.memory.search import resolve_allowed_sensitivities

CATALOG_EMBEDDING_DIMENSIONS = 384
MAX_QUERY_VECTORS = 25
MAX_LIMIT = 50
DEFAULT_EXACT_CANDIDATE_CAP = 50_000
MAX_EXACT_CANDIDATE_CAP = DEFAULT_EXACT_CANDIDATE_CAP
DEFAULT_QUERY_TIMEOUT_SECONDS = 10.0
MAX_QUERY_TIMEOUT_SECONDS = DEFAULT_QUERY_TIMEOUT_SECONDS
MIN_EVIDENCE_SAMPLES = 20
RECALL_FOLLOW_UP_THRESHOLD = 0.98
SHORTFALL_OBSERVATION_RATE = 0.10
IVFFLAT_INDEX_NAME = "idx_memory_catalog_embedding"


@dataclass(frozen=True)
class CatalogMeasurementRequest:
    """A bounded catalog filter and precomputed query vectors.

    ``query_vectors`` are intentionally excluded from ``repr`` and never
    returned in a report.  Operators supply precomputed vectors so a probe
    cannot log or persist the natural-language query used to create them.
    """

    tenant_id: str
    memory_type: str | None
    allowed_sensitivities: tuple[str, ...]
    limit: int
    query_vectors: tuple[Sequence[float], ...] = field(repr=False)
    exact_candidate_cap: int = DEFAULT_EXACT_CANDIDATE_CAP
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id must not be empty")
        if self.memory_type not in {None, "fact", "rule"}:
            raise ValueError("memory_type must be 'fact', 'rule', or None")
        if not self.allowed_sensitivities:
            raise ValueError("allowed_sensitivities must not be empty")
        if not 1 <= self.limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        if not 1 <= len(self.query_vectors) <= MAX_QUERY_VECTORS:
            raise ValueError(f"query_vectors must contain at most {MAX_QUERY_VECTORS} vectors")
        if not 1 <= self.exact_candidate_cap <= MAX_EXACT_CANDIDATE_CAP:
            raise ValueError(f"exact_candidate_cap must be between 1 and {MAX_EXACT_CANDIDATE_CAP}")
        if not 0 < self.query_timeout_seconds <= MAX_QUERY_TIMEOUT_SECONDS:
            raise ValueError(
                "query_timeout_seconds must be greater than zero and no more than "
                f"{MAX_QUERY_TIMEOUT_SECONDS:g}"
            )
        for vector in self.query_vectors:
            if len(vector) != CATALOG_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"each query vector must have {CATALOG_EMBEDDING_DIMENSIONS} dimensions"
                )
            if not all(math.isfinite(float(value)) for value in vector):
                raise ValueError("each query vector value must be finite")


@dataclass(frozen=True)
class QueryObservation:
    """Aggregate result of one approximate-versus-exact comparison."""

    filtered_candidate_count: int
    approximate_result_count: int
    exact_result_count: int | None
    overlap_count: int | None
    recall_at_limit: float | None
    candidate_shortfall: int | None
    approximate_latency_ms: float
    exact_latency_ms: float | None
    ivfflat_plan_used: bool
    exact_status: str


@dataclass(frozen=True)
class EvidenceAssessment:
    """Whether the deliberately conservative evidence threshold is met."""

    eligible_observation_count: int
    mean_recall_at_limit: float | None
    p95_candidate_shortfall: int | None
    shortfall_observation_rate: float | None
    follow_up_recommended: bool


@dataclass(frozen=True)
class CatalogMeasurementReport:
    """Aggregate-only output for a bounded measurement run."""

    filter_memory_type: str | None
    filter_sensitivities: tuple[str, ...]
    limit: int
    exact_candidate_cap: int
    observations: tuple[QueryObservation, ...]
    evidence: EvidenceAssessment
    safety: Mapping[str, Any]


@dataclass(frozen=True)
class CatalogMaintenanceObservability:
    """Safe catalog/index statistics useful when interpreting a probe."""

    lifecycle_counts: tuple[Mapping[str, Any], ...]
    table_stats: Mapping[str, Any] | None
    ivfflat_index_stats: Mapping[str, Any] | None


def resolve_measurement_sensitivities(max_sensitivity: str) -> tuple[str, ...]:
    """Use the catalog retrieval ceiling resolver instead of a probe-only policy."""

    return tuple(resolve_allowed_sensitivities(max_sensitivity))


def _catalog_filter(
    *,
    tenant_id: str,
    memory_type: str | None,
    allowed_sensitivities: Sequence[str],
    embedding: Sequence[float] | None = None,
) -> tuple[list[Any], list[str]]:
    """Build the same live-row predicates as ``_catalog_semantic_search``.

    When an embedding is present it remains ``$1``, matching the production
    retrieval query exactly; count and maintenance queries start their filter
    arguments at ``$1`` instead.
    """

    params: list[Any] = []
    next_parameter = 1
    if embedding is not None:
        params.append(str(list(embedding)))
        next_parameter = 2

    params.append(tenant_id)
    conditions = [f"tenant_id = ${next_parameter}", "invalid_at IS NULL"]
    next_parameter += 1
    if memory_type is not None:
        params.append(memory_type)
        conditions.append(f"memory_type = ${next_parameter}")
        next_parameter += 1
    params.append(list(allowed_sensitivities))
    conditions.append(f"COALESCE(sensitivity, 'normal') = ANY(${next_parameter})")
    return params, conditions


def _where(conditions: Sequence[str]) -> str:
    return "WHERE " + " AND ".join(conditions)


def _plan_uses_ivfflat(plan: Any) -> bool:
    """Find the named IVFFlat index in ``EXPLAIN (FORMAT JSON)`` output."""

    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            # Nested plan fields such as ``Node Type`` are ordinary strings,
            # not a second JSON document.
            return False
    if isinstance(plan, Mapping):
        if plan.get("Index Name") == IVFFLAT_INDEX_NAME:
            return True
        return any(_plan_uses_ivfflat(value) for value in plan.values())
    if isinstance(plan, Sequence) and not isinstance(plan, (str, bytes, bytearray)):
        return any(_plan_uses_ivfflat(value) for value in plan)
    return False


def _nearest_rank_p95(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[position]


def assess_follow_up_evidence(observations: Sequence[QueryObservation]) -> EvidenceAssessment:
    """Apply the documented proposal-only threshold to comparable observations."""

    eligible = [
        observation
        for observation in observations
        if observation.ivfflat_plan_used
        and observation.exact_status == "completed"
        and observation.recall_at_limit is not None
        and observation.candidate_shortfall is not None
    ]
    recalls = [observation.recall_at_limit for observation in eligible]
    shortfalls = [observation.candidate_shortfall for observation in eligible]
    count = len(eligible)
    mean_recall = sum(recalls) / count if count else None
    p95_shortfall = _nearest_rank_p95(shortfalls)
    shortfall_rate = sum(value > 0 for value in shortfalls) / count if count else None
    enough_evidence = count >= MIN_EVIDENCE_SAMPLES
    material_shortfall = (
        shortfall_rate is not None
        and p95_shortfall is not None
        and shortfall_rate >= SHORTFALL_OBSERVATION_RATE
        and p95_shortfall >= 1
    )
    low_recall = mean_recall is not None and mean_recall < RECALL_FOLLOW_UP_THRESHOLD
    return EvidenceAssessment(
        eligible_observation_count=count,
        mean_recall_at_limit=mean_recall,
        p95_candidate_shortfall=p95_shortfall,
        shortfall_observation_rate=shortfall_rate,
        follow_up_recommended=enough_evidence and (material_shortfall or low_recall),
    )


async def _measure_one(
    connection: Any,
    request: CatalogMeasurementRequest,
    vector: Sequence[float],
    *,
    candidate_count: int,
) -> QueryObservation:
    """Measure one vector under a pre-counted, immutable live-row filter."""

    query_params, query_conditions = _catalog_filter(
        tenant_id=request.tenant_id,
        memory_type=request.memory_type,
        allowed_sensitivities=request.allowed_sensitivities,
        embedding=vector,
    )
    approximate_sql = f"""
        SELECT id
        FROM public.memory_catalog
        {_where(query_conditions)}
        ORDER BY embedding <=> $1
        LIMIT ${len(query_params) + 1}
    """
    approximate_params = [*query_params, request.limit]
    approximate_started = time.perf_counter()
    approximate_rows = await connection.fetch(
        approximate_sql,
        *approximate_params,
        timeout=request.query_timeout_seconds,
    )
    approximate_latency_ms = (time.perf_counter() - approximate_started) * 1000

    plan = await connection.fetchval(
        "EXPLAIN (FORMAT JSON) " + approximate_sql,
        *approximate_params,
        timeout=request.query_timeout_seconds,
    )
    ivfflat_plan_used = _plan_uses_ivfflat(plan)
    if candidate_count > request.exact_candidate_cap:
        return QueryObservation(
            filtered_candidate_count=candidate_count,
            approximate_result_count=len(approximate_rows),
            exact_result_count=None,
            overlap_count=None,
            recall_at_limit=None,
            candidate_shortfall=None,
            approximate_latency_ms=approximate_latency_ms,
            exact_latency_ms=None,
            ivfflat_plan_used=ivfflat_plan_used,
            exact_status="skipped_candidate_cap",
        )

    exact_sql = f"""
        WITH candidates AS MATERIALIZED (
            SELECT id, embedding <=> $1 AS distance
            FROM public.memory_catalog
            {_where(query_conditions)}
        )
        SELECT id
        FROM candidates
        ORDER BY distance
        LIMIT ${len(query_params) + 1}
    """
    exact_started = time.perf_counter()
    exact_rows = await connection.fetch(
        exact_sql,
        *approximate_params,
        timeout=request.query_timeout_seconds,
    )
    exact_latency_ms = (time.perf_counter() - exact_started) * 1000
    approximate_ids = {str(row["id"]) for row in approximate_rows}
    exact_ids = {str(row["id"]) for row in exact_rows}
    overlap_count = len(approximate_ids & exact_ids)
    exact_result_count = len(exact_rows)
    return QueryObservation(
        filtered_candidate_count=candidate_count,
        approximate_result_count=len(approximate_rows),
        exact_result_count=exact_result_count,
        overlap_count=overlap_count,
        recall_at_limit=overlap_count / exact_result_count if exact_result_count else None,
        candidate_shortfall=max(0, exact_result_count - len(approximate_rows)),
        approximate_latency_ms=approximate_latency_ms,
        exact_latency_ms=exact_latency_ms,
        ivfflat_plan_used=ivfflat_plan_used,
        exact_status="completed",
    )


async def _filtered_candidate_count(connection: Any, request: CatalogMeasurementRequest) -> int:
    """Count the fixed filter once, before deciding whether exact work is safe."""

    params, conditions = _catalog_filter(
        tenant_id=request.tenant_id,
        memory_type=request.memory_type,
        allowed_sensitivities=request.allowed_sensitivities,
    )
    return int(
        await connection.fetchval(
            f"SELECT COUNT(*) FROM public.memory_catalog {_where(conditions)}",
            *params,
            timeout=request.query_timeout_seconds,
        )
    )


async def measure_catalog_ivfflat(
    pool: Any,
    request: CatalogMeasurementRequest,
) -> CatalogMeasurementReport:
    """Compare bounded IVFFlat results with exact filtered results.

    This function executes only ``SELECT``/plain ``EXPLAIN`` statements under
    a PostgreSQL read-only transaction.  It never changes planner settings,
    index settings, table contents, statistics, or database maintenance state.
    """

    observations: list[QueryObservation] = []
    async with pool.acquire() as connection:
        async with connection.transaction(readonly=True):
            candidate_count = await _filtered_candidate_count(connection, request)
            for vector in request.query_vectors:
                observations.append(
                    await _measure_one(
                        connection,
                        request,
                        vector,
                        candidate_count=candidate_count,
                    )
                )
    evidence = assess_follow_up_evidence(observations)
    return CatalogMeasurementReport(
        filter_memory_type=request.memory_type,
        filter_sensitivities=request.allowed_sensitivities,
        limit=request.limit,
        exact_candidate_cap=request.exact_candidate_cap,
        observations=tuple(observations),
        evidence=evidence,
        safety={
            "read_only_transaction": True,
            "max_query_vectors": MAX_QUERY_VECTORS,
            "query_timeout_seconds": request.query_timeout_seconds,
            "exact_candidate_cap": request.exact_candidate_cap,
            "mutations_attempted": 0,
            "planner_or_index_settings_changed": False,
        },
    )


async def collect_catalog_maintenance_observability(
    pool: Any,
    *,
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> CatalogMaintenanceObservability:
    """Return aggregate lifecycle and maintenance statistics without maintenance work."""

    if not 0 < query_timeout_seconds <= MAX_QUERY_TIMEOUT_SECONDS:
        raise ValueError(
            "query_timeout_seconds must be greater than zero and no more than "
            f"{MAX_QUERY_TIMEOUT_SECONDS:g}"
        )

    lifecycle_sql = """
        SELECT
            COALESCE(memory_type, 'unknown') AS memory_type,
            COUNT(*) FILTER (WHERE invalid_at IS NULL) AS active_count,
            COUNT(*) FILTER (WHERE invalid_at IS NOT NULL) AS stale_count,
            COUNT(*) FILTER (WHERE embedding IS NULL) AS missing_embedding_count
        FROM public.memory_catalog
        GROUP BY COALESCE(memory_type, 'unknown')
        ORDER BY COALESCE(memory_type, 'unknown')
    """
    table_stats_sql = """
        SELECT n_live_tup, n_dead_tup, last_analyze, last_autoanalyze
        FROM pg_stat_user_tables
        WHERE relid = 'public.memory_catalog'::regclass
    """
    index_stats_sql = """
        SELECT
            c.relname AS index_name,
            am.amname AS index_method,
            pg_relation_size(c.oid) AS index_bytes,
            COALESCE(s.idx_scan, 0) AS idx_scan,
            COALESCE(s.idx_tup_read, 0) AS idx_tup_read,
            COALESCE(s.idx_tup_fetch, 0) AS idx_tup_fetch
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_am am ON am.oid = c.relam
        LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = c.oid
        WHERE n.nspname = 'public' AND c.relname = $1
    """
    async with pool.acquire() as connection:
        async with connection.transaction(readonly=True):
            lifecycle_rows = await connection.fetch(
                lifecycle_sql,
                timeout=query_timeout_seconds,
            )
            table_stats = await connection.fetchrow(
                table_stats_sql,
                timeout=query_timeout_seconds,
            )
            index_stats = await connection.fetchrow(
                index_stats_sql,
                IVFFLAT_INDEX_NAME,
                timeout=query_timeout_seconds,
            )
    return CatalogMaintenanceObservability(
        lifecycle_counts=tuple(dict(row) for row in lifecycle_rows),
        table_stats=dict(table_stats) if table_stats is not None else None,
        ivfflat_index_stats=dict(index_stats) if index_stats is not None else None,
    )


def _read_vectors(path: Path) -> tuple[Sequence[float], ...]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("vectors JSON must contain a top-level list")
    if not all(isinstance(vector, list) for vector in payload):
        raise ValueError("vectors JSON must contain only numeric vector lists")
    return tuple(payload)


def _serialize(value: Any) -> str:
    """Serialize aggregate output without custom record or datetime handling."""

    return json.dumps(value, default=str, indent=2, sort_keys=True)


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    request = CatalogMeasurementRequest(
        tenant_id=args.tenant_id,
        memory_type=args.memory_type,
        allowed_sensitivities=resolve_measurement_sensitivities(args.max_sensitivity),
        limit=args.limit,
        query_vectors=_read_vectors(args.vectors_json),
        exact_candidate_cap=args.exact_candidate_cap,
        query_timeout_seconds=args.query_timeout_seconds,
    )
    pool = await asyncpg.create_pool(
        database=os.environ.get("POSTGRES_DB", "butlers"),
        min_size=1,
        max_size=1,
        **db_params_from_env(),
    )
    try:
        report = await measure_catalog_ivfflat(pool, request)
        maintenance = await collect_catalog_maintenance_observability(
            pool,
            query_timeout_seconds=request.query_timeout_seconds,
        )
    finally:
        await pool.close()
    return {"measurement": asdict(report), "maintenance_observability": asdict(maintenance)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded, aggregate-only IVFFlat-versus-exact measurement for "
            "public.memory_catalog. Uses only read-only transactions."
        )
    )
    parser.add_argument(
        "--vectors-json",
        type=Path,
        required=True,
        help="Path to a JSON list of precomputed 384-dimensional vectors; never echoed.",
    )
    parser.add_argument("--tenant-id", default="shared")
    parser.add_argument("--memory-type", choices=("fact", "rule"))
    parser.add_argument(
        "--max-sensitivity",
        dest="max_sensitivity",
        choices=("normal", "pii", "confidential"),
        default="normal",
        help="Highest visibility level, resolved inclusively as in live catalog retrieval.",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--exact-candidate-cap",
        type=int,
        default=DEFAULT_EXACT_CANDIDATE_CAP,
        help=f"Exact comparator cap (hard maximum: {MAX_EXACT_CANDIDATE_CAP}).",
    )
    parser.add_argument(
        "--query-timeout-seconds",
        type=float,
        default=DEFAULT_QUERY_TIMEOUT_SECONDS,
        help=f"Client-side per-query timeout (hard maximum: {MAX_QUERY_TIMEOUT_SECONDS:g}s).",
    )
    return parser


def main() -> None:
    """Run the opt-in operational measurement command."""

    args = _parser().parse_args()
    try:
        result = asyncio.run(_run_cli(args))
    except Exception as exc:
        # Do not include vectors, catalog values, database DSNs, or SQL in errors.
        raise SystemExit(f"catalog measurement failed: {type(exc).__name__}") from exc
    print(_serialize(result))


if __name__ == "__main__":
    main()
