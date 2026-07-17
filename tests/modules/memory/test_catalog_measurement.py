"""Unit coverage for bounded, read-only catalog IVFFlat measurement.

The live harness deliberately works through a pool connection in a read-only
transaction.  These tests use only fake records and never need pgvector or a
running database.
"""

from __future__ import annotations

import argparse
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from butlers.modules.memory import catalog_measurement
from butlers.modules.memory.catalog_measurement import (
    DEFAULT_EXACT_CANDIDATE_CAP,
    MIN_EVIDENCE_SAMPLES,
    CatalogMeasurementRequest,
    QueryObservation,
    _plan_uses_ivfflat,
    assess_follow_up_evidence,
    collect_catalog_maintenance_observability,
    measure_catalog_ivfflat,
    resolve_measurement_sensitivities,
)

pytestmark = pytest.mark.unit


def _vector() -> list[float]:
    return [1.0] + [0.0] * 383


class _Transaction:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events

    async def __aenter__(self) -> None:
        if self.events is not None:
            self.events.append("transaction:start")
        return None

    async def __aexit__(self, *_: object) -> None:
        if self.events is not None:
            self.events.append("transaction:end")
        return None


class _Connection:
    def __init__(
        self,
        *,
        candidate_count: int,
        approximate_ids: list[str],
        exact_ids: list[str],
        approximate_distances: list[float | None] | None = None,
        exact_distances: list[float | None] | None = None,
    ) -> None:
        self.candidate_count = candidate_count
        self.approximate_ids = approximate_ids
        self.exact_ids = exact_ids
        default_distances = {
            item: float(index)
            for index, item in enumerate(dict.fromkeys([*exact_ids, *approximate_ids]))
        }
        self.approximate_distances = approximate_distances or [
            default_distances[item] for item in approximate_ids
        ]
        self.exact_distances = exact_distances or [default_distances[item] for item in exact_ids]
        self.calls: list[tuple[str, tuple[object, ...], float | None]] = []
        self.events: list[str] = []
        self.readonly: bool | None = None
        self.transaction_kwargs: list[dict[str, object]] = []

    def transaction(self, *, isolation: str | None = None, readonly: bool = False) -> _Transaction:
        self.readonly = readonly
        self.transaction_kwargs.append({"isolation": isolation, "readonly": readonly})
        return _Transaction(self.events)

    async def fetchval(self, sql: str, *args: object, timeout: float | None = None) -> Any:
        self.calls.append((sql, args, timeout))
        if "COUNT(*)" in sql:
            self.events.append("candidate_count")
            return self.candidate_count
        if "EXPLAIN" in sql:
            self.events.append("approximate_plan")
            return [
                [
                    {
                        "Plan": {
                            "Node Type": "Index Scan",
                            "Index Name": "idx_memory_catalog_embedding",
                        }
                    }
                ]
            ]
        raise AssertionError(f"unexpected fetchval SQL: {sql}")

    async def fetch(
        self, sql: str, *args: object, timeout: float | None = None
    ) -> list[dict[str, object]]:
        self.calls.append((sql, args, timeout))
        if "WITH candidates AS MATERIALIZED" in sql:
            self.events.append("exact")
            return [
                {"id": item, "distance": distance}
                for item, distance in zip(self.exact_ids, self.exact_distances, strict=True)
            ]
        if "ORDER BY embedding <=> $1" in sql:
            self.events.append("approximate")
            return [
                {"id": item, "distance": distance}
                for item, distance in zip(
                    self.approximate_ids, self.approximate_distances, strict=True
                )
            ]
        raise AssertionError(f"unexpected fetch SQL: {sql}")


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class _ObservabilityConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], float | None]] = []
        self.readonly: bool | None = None

    def transaction(self, *, readonly: bool = False) -> _Transaction:
        self.readonly = readonly
        return _Transaction()

    async def fetch(
        self, sql: str, *args: object, timeout: float | None = None
    ) -> list[dict[str, object]]:
        self.calls.append((sql, args, timeout))
        if "GROUP BY" in sql:
            return [
                {
                    "memory_type": "fact",
                    "active_count": 4,
                    "stale_count": 1,
                    "missing_embedding_count": 0,
                }
            ]
        raise AssertionError(f"unexpected fetch SQL: {sql}")

    async def fetchrow(
        self, sql: str, *args: object, timeout: float | None = None
    ) -> dict[str, object]:
        self.calls.append((sql, args, timeout))
        if "pg_stat_user_tables" in sql:
            return {
                "n_live_tup": 4,
                "n_dead_tup": 1,
                "last_analyze": None,
                "last_autoanalyze": None,
            }
        if "pg_stat_user_indexes" in sql:
            return {
                "index_name": "idx_memory_catalog_embedding",
                "index_method": "ivfflat",
                "index_bytes": 1024,
                "idx_scan": 2,
                "idx_tup_read": 20,
                "idx_tup_fetch": 20,
            }
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")


@pytest.mark.asyncio
async def test_measurement_reports_overlap_without_exposing_result_ids() -> None:
    """Filtered IVFFlat results are compared to exact results only in-process."""
    connection = _Connection(
        candidate_count=8,
        approximate_ids=["private-result-001", "private-result-003"],
        exact_ids=["private-result-001", "private-result-002", "private-result-003"],
    )
    request = CatalogMeasurementRequest(
        tenant_id="shared",
        memory_type="fact",
        allowed_sensitivities=("normal",),
        limit=3,
        query_vectors=(_vector(), _vector()),
    )

    report = await measure_catalog_ivfflat(_Pool(connection), request)

    observation = report.observations[0]
    assert connection.readonly is True
    assert connection.transaction_kwargs == [
        {"isolation": "repeatable_read", "readonly": True},
        {"isolation": "repeatable_read", "readonly": True},
    ]
    assert connection.events == [
        "transaction:start",
        "candidate_count",
        "approximate",
        "approximate_plan",
        "exact",
        "transaction:end",
        "transaction:start",
        "candidate_count",
        "approximate",
        "approximate_plan",
        "exact",
        "transaction:end",
    ]
    assert len(report.observations) == 2
    assert observation.filtered_candidate_count == 8
    assert observation.approximate_result_count == 2
    assert observation.exact_result_count == 3
    assert observation.overlap_count == 2
    assert observation.recall_at_limit == pytest.approx(2 / 3)
    assert observation.candidate_shortfall == 1
    assert observation.ivfflat_plan_used is True
    assert "private-result-001" not in str(report)
    assert "private-result-002" not in str(report)

    sql = "\n".join(call[0] for call in connection.calls)
    assert sql.count("tenant_id = $") >= 3
    assert sql.count("invalid_at IS NULL") >= 3
    assert sql.count("memory_type = $") >= 3
    assert sql.count("COALESCE(sensitivity, 'normal')") >= 3
    assert "WITH candidates AS MATERIALIZED" in sql
    assert "EXPLAIN (FORMAT JSON)" in sql
    assert "EXPLAIN ANALYZE" not in sql
    assert "SET " not in sql
    assert "ANALYZE " not in sql
    assert all(call[2] is not None and call[2] <= 10 for call in connection.calls)


@pytest.mark.asyncio
async def test_measurement_treats_boundary_distance_ties_as_equivalent() -> None:
    """Different equally-ranked IDs must not look like IVFFlat recall loss."""
    connection = _Connection(
        candidate_count=3,
        approximate_ids=["nearest", "equally-near-alternative"],
        approximate_distances=[0.1, 0.2],
        exact_ids=["nearest", "equally-near-reference"],
        exact_distances=[0.1, 0.2],
    )
    request = CatalogMeasurementRequest(
        tenant_id="shared",
        memory_type="fact",
        allowed_sensitivities=("normal",),
        limit=2,
        query_vectors=(_vector(),),
    )

    report = await measure_catalog_ivfflat(_Pool(connection), request)

    observation = report.observations[0]
    assert observation.ivfflat_plan_used is True
    assert observation.overlap_count == 2
    assert observation.recall_at_limit == 1.0
    assert observation.candidate_shortfall == 0


@pytest.mark.asyncio
async def test_measurement_skips_exact_comparator_when_filter_population_exceeds_cap() -> None:
    """The exact scan never runs beyond the fixed live-safe candidate cap."""
    connection = _Connection(
        candidate_count=DEFAULT_EXACT_CANDIDATE_CAP + 1,
        approximate_ids=["a"],
        exact_ids=["a"],
    )
    request = CatalogMeasurementRequest(
        tenant_id="shared",
        memory_type="rule",
        allowed_sensitivities=("normal",),
        limit=1,
        query_vectors=(_vector(),),
    )

    report = await measure_catalog_ivfflat(_Pool(connection), request)

    observation = report.observations[0]
    assert observation.exact_status == "skipped_candidate_cap"
    assert observation.exact_result_count is None
    assert observation.recall_at_limit is None
    assert not any("WITH candidates AS MATERIALIZED" in sql for sql, _, _ in connection.calls)


@pytest.mark.asyncio
async def test_maintenance_observability_is_aggregate_only_and_timeout_bounded() -> None:
    """Lifecycle/index stats are safe diagnostics, never maintenance commands."""
    connection = _ObservabilityConnection()

    result = await collect_catalog_maintenance_observability(_Pool(connection))

    assert connection.readonly is True
    assert result.lifecycle_counts == (
        {"memory_type": "fact", "active_count": 4, "stale_count": 1, "missing_embedding_count": 0},
    )
    assert result.ivfflat_index_stats is not None
    assert result.ivfflat_index_stats["index_method"] == "ivfflat"
    sql = "\n".join(call[0] for call in connection.calls)
    assert "pg_stat_user_tables" in sql
    assert "pg_stat_user_indexes" in sql
    assert all(call[2] is not None and call[2] <= 10 for call in connection.calls)
    assert not any(
        keyword in sql for keyword in ("INSERT", "UPDATE", "DELETE", "VACUUM", "REINDEX", "SET ")
    )


def _observation(*, recall: float, shortfall: int, ivfflat: bool = True) -> QueryObservation:
    return QueryObservation(
        filtered_candidate_count=10,
        approximate_result_count=10 - shortfall,
        exact_result_count=10,
        overlap_count=round(recall * 10),
        recall_at_limit=recall,
        candidate_shortfall=shortfall,
        approximate_latency_ms=1.0,
        exact_latency_ms=2.0,
        ivfflat_plan_used=ivfflat,
        exact_status="completed",
    )


def test_follow_up_threshold_requires_enough_ivfflat_planned_observations() -> None:
    """A single low-recall run informs an operator but cannot recommend tuning."""
    not_enough = assess_follow_up_evidence([_observation(recall=0.5, shortfall=2)])
    assert not_enough.eligible_observation_count == 1
    assert not_enough.follow_up_recommended is False

    enough = assess_follow_up_evidence(
        [_observation(recall=0.97, shortfall=1) for _ in range(MIN_EVIDENCE_SAMPLES)]
    )
    assert enough.eligible_observation_count == MIN_EVIDENCE_SAMPLES
    assert enough.follow_up_recommended is True


def test_plan_parser_ignores_non_json_plan_strings() -> None:
    """Plan traversal must not mistake node labels for a JSON payload."""
    plan = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "memory_catalog"}}]

    assert _plan_uses_ivfflat(plan) is False


def test_measurement_sensitivity_ceiling_matches_catalog_retrieval() -> None:
    """A PII-authorized probe includes normal rows just as live retrieval does."""
    assert resolve_measurement_sensitivities("pii") == ("normal", "pii")


def test_request_rejects_oversized_or_nonfinite_vectors_before_database_access() -> None:
    """Operator mistakes cannot turn the bounded probe into an unbounded run."""
    with pytest.raises(ValueError, match="at most"):
        CatalogMeasurementRequest(
            tenant_id="shared",
            memory_type=None,
            allowed_sensitivities=("normal",),
            limit=1,
            query_vectors=tuple(_vector() for _ in range(26)),
        )

    with pytest.raises(ValueError, match="finite"):
        CatalogMeasurementRequest(
            tenant_id="shared",
            memory_type=None,
            allowed_sensitivities=("normal",),
            limit=1,
            query_vectors=([float("nan")] + [0.0] * 383,),
        )


@pytest.mark.parametrize("invalid_value", ["1.0", True])
def test_request_rejects_non_numeric_vector_values_before_database_access(
    invalid_value: object,
) -> None:
    """JSON strings and booleans must not reach the pgvector bind parameter."""
    vector = _vector()
    vector[0] = invalid_value  # type: ignore[assignment]

    with pytest.raises(ValueError, match="numeric"):
        CatalogMeasurementRequest(
            tenant_id="shared",
            memory_type=None,
            allowed_sensitivities=("normal",),
            limit=1,
            query_vectors=(vector,),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "limit", "match"),
    [
        ("not-a-list", 1, "top-level list"),
        ([], 1, "at most"),
        ([[_vector()[0]] * 383], 1, "384 dimensions"),
        ([[True] + [0.0] * 383], 1, "numeric"),
        ([[float("nan")] + [0.0] * 383], 1, "finite"),
        ([_vector()], 0, "limit must be between"),
    ],
)
async def test_cli_rejects_invalid_inputs_before_creating_database_pool(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    limit: int,
    match: str,
) -> None:
    """Validation errors must be raised before the CLI opens a database pool."""
    vectors_json = tmp_path / "vectors.json"
    vectors_json.write_text(json.dumps(payload))
    pool_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def unexpected_create_pool(*args: object, **kwargs: object) -> None:
        pool_calls.append((args, kwargs))
        raise AssertionError("invalid catalog measurement input opened a database pool")

    monkeypatch.setattr(catalog_measurement.asyncpg, "create_pool", unexpected_create_pool)
    args = argparse.Namespace(
        tenant_id="shared",
        memory_type=None,
        max_sensitivity="normal",
        limit=limit,
        vectors_json=vectors_json,
        exact_candidate_cap=DEFAULT_EXACT_CANDIDATE_CAP,
        query_timeout_seconds=10.0,
    )

    with pytest.raises(ValueError, match=match):
        await catalog_measurement._run_cli(args)

    assert pool_calls == []
