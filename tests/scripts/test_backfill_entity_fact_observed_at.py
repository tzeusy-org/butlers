"""Tests for scripts/backfill_entity_fact_observed_at.py.

Covers:
1. count_remaining returns the NULL-observed_at row count.
2. Dry run reports the found count and performs no UPDATEs.
3. Apply processes rows in bounded batches (batch_size respected per SELECT).
4. The UPDATE SQL uses COALESCE(last_seen, created_at) and re-checks
   observed_at IS NULL (concurrency-safe).
5. Idempotency: a run against a fully backfilled table updates nothing.
6. Safety valve: a batch that selects rows but updates 0 stops the loop.

The DB layer is mocked (asyncpg pool) so these are fast unit tests; the
COALESCE semantics and SQL shape are asserted against the module's SQL
constants. End-to-end COALESCE behavior over a real table is exercised by the
migration integration tests' observed_at column.

Issue: bu-mxxjy
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "backfill_entity_fact_observed_at.py"
)
_MODULE_NAME = "backfill_entity_fact_observed_at"


def _load_script():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()


# ---------------------------------------------------------------------------
# Behavior tests with a mocked pool
#
# The NULL-only stamping, bounded batching, and idempotency that the SQL
# constants encode are asserted behaviorally below (bounded-batches checks the
# LIMIT, idempotent-second-run / safety-valve check the IS NULL re-stamp).
# ---------------------------------------------------------------------------


def _row(rid):
    return {"id": rid}


def _make_pool(*, null_count: int, fetch_batches: list[list[dict]], execute_results: list[str]):
    """Build a mock asyncpg pool.

    - fetchrow → the COUNT(*) result (count_remaining).
    - fetch → successive batches of id rows (then []).
    - execute → successive "UPDATE n" command tags.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": null_count})
    pool.fetch = AsyncMock(side_effect=[*fetch_batches, []])
    pool.execute = AsyncMock(side_effect=execute_results)
    return pool


@pytest.mark.asyncio
async def test_count_remaining_returns_count() -> None:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": 7})
    assert await _mod.count_remaining(pool) == 7


@pytest.mark.asyncio
async def test_dry_run_performs_no_updates() -> None:
    pool = _make_pool(null_count=42, fetch_batches=[], execute_results=[])

    summary = await _mod.backfill_observed_at(pool, batch_size=500, dry_run=True)

    assert summary == {"found": 42, "updated": 0, "batches": 0}
    pool.fetch.assert_not_called()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_op_when_nothing_to_backfill() -> None:
    pool = _make_pool(null_count=0, fetch_batches=[], execute_results=[])

    summary = await _mod.backfill_observed_at(pool, batch_size=500, dry_run=False)

    assert summary == {"found": 0, "updated": 0, "batches": 0}
    pool.fetch.assert_not_called()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_apply_processes_rows_in_bounded_batches() -> None:
    # Two batches of 2 rows each, then the loop sees an empty fetch and stops.
    b1 = [_row(uuid4()), _row(uuid4())]
    b2 = [_row(uuid4()), _row(uuid4())]
    pool = _make_pool(
        null_count=4,
        fetch_batches=[b1, b2],
        execute_results=["UPDATE 2", "UPDATE 2"],
    )

    summary = await _mod.backfill_observed_at(pool, batch_size=2, dry_run=False)

    assert summary == {"found": 4, "updated": 4, "batches": 2}
    # SELECT was called with the batch-size limit each iteration.
    # Assert the call count first so the loop below cannot pass vacuously.
    assert len(pool.fetch.call_args_list) == 3
    for call in pool.fetch.call_args_list:
        assert call.args[1] == 2, "SELECT must be bounded by batch_size"


@pytest.mark.asyncio
async def test_apply_passes_only_selected_ids_to_update() -> None:
    ids = [uuid4(), uuid4(), uuid4()]
    batch = [_row(i) for i in ids]
    pool = _make_pool(
        null_count=3,
        fetch_batches=[batch],
        execute_results=["UPDATE 3"],
    )

    await _mod.backfill_observed_at(pool, batch_size=10, dry_run=False)

    update_call = pool.execute.call_args_list[0]
    assert update_call.args[1] == ids, "UPDATE must target exactly the selected ids"


def test_update_batch_sql_rechecks_null_and_derives_value() -> None:
    """The batch UPDATE must re-check observed_at IS NULL (concurrency-safe) and
    derive the value from COALESCE(last_seen, created_at) per the spec."""
    sql = _mod._UPDATE_BATCH_SQL.lower()
    # Re-check guard: without it a concurrent stamp would be clobbered.
    assert "observed_at is null" in sql
    # Value derivation per specs/relationship-facts/spec.md.
    assert "coalesce(last_seen, created_at)" in sql


@pytest.mark.asyncio
async def test_idempotent_second_run_is_noop() -> None:
    """Second run sees count 0 → no fetch/execute (idempotency contract)."""
    # First run: backfills 2 rows.
    pool = _make_pool(
        null_count=2,
        fetch_batches=[[_row(uuid4()), _row(uuid4())]],
        execute_results=["UPDATE 2"],
    )
    first = await _mod.backfill_observed_at(pool, batch_size=500, dry_run=False)
    assert first["updated"] == 2

    # Second run: nothing left.
    pool2 = _make_pool(null_count=0, fetch_batches=[], execute_results=[])
    second = await _mod.backfill_observed_at(pool2, batch_size=500, dry_run=False)
    assert second == {"found": 0, "updated": 0, "batches": 0}
    pool2.fetch.assert_not_called()
    pool2.execute.assert_not_called()


@pytest.mark.asyncio
async def test_safety_valve_stops_when_batch_updates_zero() -> None:
    """A batch that selects rows but updates 0 (all concurrently stamped) stops the loop."""
    batch = [_row(uuid4()), _row(uuid4())]
    # fetch returns one non-empty batch, then would return another — but the
    # 0-update result should break the loop first.
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": 2})
    pool.fetch = AsyncMock(side_effect=[batch, batch, []])
    pool.execute = AsyncMock(side_effect=["UPDATE 0"])

    summary = await _mod.backfill_observed_at(pool, batch_size=500, dry_run=False)

    assert summary["batches"] == 1
    assert summary["updated"] == 0
    assert pool.execute.call_count == 1, "loop must stop after the 0-update batch"


@pytest.mark.asyncio
async def test_main_rejects_nonpositive_batch_size(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://x/y")
    rc = await _mod.main(["--batch-size", "0"])
    assert rc == 1


@pytest.mark.asyncio
async def test_main_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("BUTLERS_DATABASE_URL", raising=False)
    rc = await _mod.main([])
    assert rc == 1
