"""Tests for the memory re-embedding migration tool.

Covers:
  - count_pending: SQL counting logic via mocked pool
  - dry_run=True: produces no DB writes, returns stale counts
  - full run (dry_run=False): writes new embedding + embedding_model_version
  - batch_size: respected — stops after one batch
  - unknown tier: raises ValueError
  - ReembedResult.to_dict: shape contract
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.reembedding import (
    ALL_TIERS,
    ReembedResult,
    _resolve_tiers,
    _resolve_tiers_list,
    count_pending,
    run,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool(fetch_results: list | None = None, fetchrow_result=None) -> AsyncMock:
    """Build a minimal asyncpg pool mock.

    - pool.acquire() is an async context manager returning a connection mock.
    - conn.fetchrow() returns fetchrow_result (a dict-like).
    - conn.fetch() returns fetch_results (a list of dict-like rows).
    - conn.execute() is an AsyncMock (returns nothing; used for count queries).
    - conn.executemany() is an AsyncMock (returns nothing; used for batch UPDATEs).
    - conn.transaction() is an async context manager (no-op).
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetch = AsyncMock(return_value=fetch_results or [])
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()

    # transaction() is an async context manager.
    txn_ctx = AsyncMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_ctx)

    # pool.acquire() is an async context manager returning the connection.
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _make_engine(model_name: str = "new-model", dim: int = 384) -> MagicMock:
    engine = MagicMock()
    engine.model_name = model_name
    engine.embed_batch = MagicMock(return_value=[[0.1] * dim, [0.2] * dim])
    return engine


# ---------------------------------------------------------------------------
# _resolve_tiers / _resolve_tiers_list
# ---------------------------------------------------------------------------


class TestResolveTiers:
    def test_none_returns_all_tiers(self) -> None:
        assert _resolve_tiers(None) == list(ALL_TIERS)

    def test_valid_single_tier(self) -> None:
        assert _resolve_tiers("facts") == ["facts"]

    def test_list_validates_all(self) -> None:
        result = _resolve_tiers_list(["episodes", "rules"])
        assert result == ["episodes", "rules"]

    # NOTE: unknown-tier rejection is guarded at the public-API level by
    # TestCountPending.test_invalid_tier_raises and
    # TestRunFull.test_invalid_tier_raises_before_db_access.


# ---------------------------------------------------------------------------
# count_pending
# ---------------------------------------------------------------------------


class TestCountPending:
    async def test_counts_stale_rows_all_tiers(self) -> None:
        """count_pending queries each tier and returns counts."""
        pool, conn = _make_pool(fetchrow_result={"cnt": 7})

        result = await count_pending(pool, current_model="model-v2")

        # One fetchrow call per tier.
        assert conn.fetchrow.call_count == len(ALL_TIERS)
        assert all(v == 7 for v in result.values())
        assert set(result.keys()) == set(ALL_TIERS)

    async def test_counts_single_tier(self) -> None:
        pool, conn = _make_pool(fetchrow_result={"cnt": 3})

        result = await count_pending(pool, current_model="model-v2", tier="facts")

        assert conn.fetchrow.call_count == 1
        assert result == {"facts": 3}

    async def test_invalid_tier_raises(self) -> None:
        pool, _ = _make_pool()
        with pytest.raises(ValueError, match="Unknown tier"):
            await count_pending(pool, current_model="model-v2", tier="bogus")


# ---------------------------------------------------------------------------
# ReembedResult
# ---------------------------------------------------------------------------


class TestReembedResult:
    def test_total_sums_counts(self) -> None:
        r = ReembedResult(
            dry_run=True,
            current_model="m",
            tiers_processed=["facts", "rules"],
            counts={"facts": 5, "rules": 3},
        )
        assert r.total == 8

    def test_to_dict_shape(self) -> None:
        r = ReembedResult(
            dry_run=False,
            current_model="m",
            tiers_processed=["facts"],
            counts={"facts": 2},
            errors=["oops"],
        )
        d = r.to_dict()
        assert d["dry_run"] is False
        assert d["current_model"] == "m"
        assert d["tiers_processed"] == ["facts"]
        assert d["counts"] == {"facts": 2}
        assert d["total"] == 2
        assert d["errors"] == ["oops"]


# ---------------------------------------------------------------------------
# run — dry_run=True
# ---------------------------------------------------------------------------


class TestRunDryRun:
    async def test_dry_run_makes_no_db_writes(self) -> None:
        """In dry_run mode, no UPDATE or INSERT statements are executed."""
        stale_rows = [
            {"id": uuid.uuid4(), "content": "hello"},
            {"id": uuid.uuid4(), "content": "world"},
        ]
        pool, conn = _make_pool(fetch_results=stale_rows)
        engine = _make_engine(model_name="new-model")

        result = await run(pool, engine, dry_run=True)

        conn.executemany.assert_not_called()
        assert result.dry_run is True
        assert result.current_model == "new-model"
        assert set(result.tiers_processed) == set(ALL_TIERS)

    async def test_dry_run_counts_batch(self) -> None:
        """dry_run counts the first batch found per tier."""
        stale_rows = [{"id": uuid.uuid4(), "content": "x"}]
        pool, conn = _make_pool(fetch_results=stale_rows)
        engine = _make_engine()

        result = await run(pool, engine, dry_run=True, tiers=["facts"])

        # 1 row found in first fetch — should be reflected in counts.
        assert result.counts["facts"] == 1
        # No writes.
        conn.executemany.assert_not_called()

    async def test_dry_run_no_rows_returns_zero(self) -> None:
        pool, _ = _make_pool(fetch_results=[])
        engine = _make_engine()

        result = await run(pool, engine, dry_run=True, tiers=["rules"])

        assert result.counts["rules"] == 0


# ---------------------------------------------------------------------------
# run — dry_run=False (actual writes)
# ---------------------------------------------------------------------------


class TestRunFull:
    async def test_updates_embedding_and_model_version(self) -> None:
        """Full run calls executemany UPDATE for the batch."""
        row_id = uuid.uuid4()
        stale_rows = [{"id": row_id, "content": "test content"}]

        # First call returns stale rows; second call (next iteration) returns
        # empty list to terminate the loop.
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(side_effect=[stale_rows, []])
        engine = _make_engine(model_name="new-model")
        engine.embed_batch = MagicMock(return_value=[[0.5] * 384])

        result = await run(pool, engine, dry_run=False, tiers=["facts"])

        assert result.dry_run is False
        assert result.counts["facts"] == 1
        # A full run writes the batch: new model name + row id bound per row.
        conn.executemany.assert_called_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 1
        assert rows[0][1] == "new-model"  # model name
        assert rows[0][2] == row_id  # row id

    async def test_batch_size_respected(self) -> None:
        """With batch_size=2, a batch of 2 triggers a second fetch."""
        batch = [{"id": uuid.uuid4(), "content": f"c{i}"} for i in range(2)]

        pool, conn = _make_pool()
        # First fetch: full batch; second fetch: empty (done).
        conn.fetch = AsyncMock(side_effect=[batch, []])
        engine = _make_engine()
        engine.embed_batch = MagicMock(return_value=[[0.1] * 384, [0.2] * 384])

        result = await run(pool, engine, dry_run=False, tiers=["episodes"], batch_size=2)

        # A full batch triggers a second fetch (batch-boundary contract).
        assert conn.fetch.call_count == 2
        assert result.counts["episodes"] == 2

    async def test_no_rows_returns_zero_count(self) -> None:
        pool, conn = _make_pool(fetch_results=[])
        engine = _make_engine()

        result = await run(pool, engine, dry_run=False, tiers=["rules"])

        assert result.counts["rules"] == 0
        conn.executemany.assert_not_called()

    async def test_invalid_tier_raises_before_db_access(self) -> None:
        pool, conn = _make_pool()
        engine = _make_engine()

        with pytest.raises(ValueError, match="Unknown tiers"):
            await run(pool, engine, dry_run=False, tiers=["bogus"])

        conn.fetch.assert_not_called()

    async def test_multi_tier_processes_each(self) -> None:
        """All three tiers are processed independently.

        Each tier fetch returns 1 row (< batch_size=50), so the loop exits
        after a single fetch per tier — 3 total fetches across all tiers.
        """
        pool, conn = _make_pool()
        # One row per tier; batch_size=50 so 1 < 50 means "last batch" after first fetch.
        single_row = [{"id": uuid.uuid4(), "content": "text"}]
        conn.fetch = AsyncMock(side_effect=[single_row, single_row, single_row])
        engine = _make_engine()
        engine.embed_batch = MagicMock(return_value=[[0.1] * 384])

        result = await run(pool, engine, dry_run=False)

        assert set(result.tiers_processed) == set(ALL_TIERS)
        assert result.counts == {"episodes": 1, "facts": 1, "rules": 1}
        assert result.total == 3
