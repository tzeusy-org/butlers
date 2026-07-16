"""Unit tests for the live-safe memory ANN observability probe."""

from __future__ import annotations

from datetime import UTC, datetime

from butlers.modules.memory.ann_observability import (
    EXACT_CORPUS_MAX_PAGES,
    EXACT_CORPUS_MAX_ROWS,
    run_ann_observability,
)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, *, estimated_rows: int, relpages: int = 1) -> None:
        self.estimated_rows = estimated_rows
        self.relpages = relpages
        self.executed: list[str] = []
        self.mode: str | None = None
        self.samples_returned = 0

    def transaction(self, *, readonly: bool = False):
        assert readonly is True
        return _Transaction()

    async def fetchrow(self, query: str, *args):
        if "FROM pg_class AS c" in query:
            return {
                "estimated_rows": self.estimated_rows,
                "relpages": self.relpages,
                "n_live_tup": self.estimated_rows,
                "n_dead_tup": 2,
                "n_tup_ins": 10,
                "n_tup_upd": 1,
                "n_tup_del": 1,
                "n_mod_since_analyze": 0,
                "last_analyze": datetime(2026, 7, 17, tzinfo=UTC),
                "last_autoanalyze": None,
                "last_vacuum": None,
                "last_autovacuum": None,
                "has_hnsw": True,
            }
        if "TABLESAMPLE SYSTEM" in query:
            self.samples_returned += 1
            return {"tenant_id": "shared", "embedding": "[0.1,0.2]"}
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetchval(self, query: str, *args):
        if query.lstrip().startswith("EXPLAIN"):
            return '[{"Plan":{"Node Type":"Index Scan","Index Name":"idx_facts_embedding"}}]'
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def fetch(self, query: str, *args):
        if "ORDER BY embedding <=>" not in query:
            raise AssertionError(f"unexpected fetch query: {query}")
        if self.mode == "exact":
            return [{"id": "exact-1"}, {"id": "exact-2"}]
        if self.mode == "approx":
            return [{"id": "exact-1"}, {"id": "exact-2"}]
        raise AssertionError("query executed without an ANN/exact mode")

    async def execute(self, query: str, *args):
        self.executed.append(query)
        if "enable_indexscan = off" in query:
            self.mode = "exact"
        elif "enable_seqscan = off" in query:
            self.mode = "approx"


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


async def test_small_hnsw_table_reports_exact_recall_without_sensitive_payloads() -> None:
    """The monitor compares bounded live data but returns only aggregate health."""
    connection = _Connection(estimated_rows=100)

    result = await run_ann_observability(
        _Pool(connection),
        tables=("facts",),
        sample_queries=2,
        k=2,
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    facts = result["tables"]["facts"]
    assert result["health"] == "healthy"
    assert facts["recall"] == {
        "status": "measured",
        "queries_compared": 2,
        "recall_at_k": 1.0,
    }
    assert facts["recommended_action"] == "none"
    assert "embedding" not in str(result)
    assert "exact-1" not in str(result)
    assert connection.samples_returned == 2
    assert all(
        forbidden not in "\n".join(connection.executed).upper()
        for forbidden in ("DELETE", "INSERT", "UPDATE", "VACUUM", "REINDEX")
    )


async def test_large_table_is_honestly_degraded_without_sampling_or_exact_scan() -> None:
    """The hard exact-corpus cap prevents a production-wide brute-force probe."""
    connection = _Connection(estimated_rows=EXACT_CORPUS_MAX_ROWS + 1)

    result = await run_ann_observability(
        _Pool(connection),
        tables=("facts",),
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    facts = result["tables"]["facts"]
    assert result["health"] == "degraded"
    assert facts["recall"] == {
        "status": "degraded",
        "reason": "corpus_exceeds_exact_row_cap",
    }
    assert facts["recommended_action"] == "use_nightly_synthetic_recall_or_plan_offline_rebenchmark"
    assert connection.samples_returned == 0
    assert not any("ORDER BY embedding <=>" in query for query in connection.executed)


async def test_large_physical_relation_never_uses_stale_row_estimates_for_exact_scan() -> None:
    """The heap-page cap protects the probe when pg_class.reltuples is stale."""
    connection = _Connection(estimated_rows=1, relpages=EXACT_CORPUS_MAX_PAGES + 1)

    result = await run_ann_observability(
        _Pool(connection),
        tables=("facts",),
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    facts = result["tables"]["facts"]
    assert facts["recall"] == {
        "status": "degraded",
        "reason": "corpus_exceeds_exact_page_cap",
    }
    assert connection.samples_returned == 0
