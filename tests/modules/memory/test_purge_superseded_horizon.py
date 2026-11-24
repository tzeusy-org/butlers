"""The superseded-fact purge keeps belief history and fails closed (bu-uhbhzq.3)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.scheduled_jobs import _read_fact_horizon, _run_memory_purge_superseded_job

pytestmark = pytest.mark.unit


def _row(ttl_days):
    return {"ttl_days": ttl_days}


@pytest.fixture
def wired(monkeypatch):
    """The job with its storage seams replaced; returns (pool, superseded, ha_state, log)."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row(None))
    superseded = AsyncMock(return_value={"deleted": 0})
    ha_state = AsyncMock(return_value={"deleted_ha_state": 0})
    log = AsyncMock()
    monkeypatch.setattr(
        "butlers.core.memory_hooks.resolve_memory_runtime_pool", MagicMock(return_value=pool)
    )
    monkeypatch.setattr("butlers.modules.memory.storage.purge_superseded_facts", superseded)
    monkeypatch.setattr("butlers.modules.memory.storage.purge_ha_state_facts", ha_state)
    monkeypatch.setattr("butlers.scheduled_jobs._table_size_bytes", AsyncMock(return_value=None))
    monkeypatch.setattr("butlers.scheduled_jobs._log_compaction", log)
    return pool, superseded, ha_state, log


async def test_a_null_horizon_deletes_no_superseded_fact_but_still_purges_ha_state(wired):
    pool, superseded, ha_state, log = wired
    superseded.return_value = {"deleted": 0, "skipped": "no_horizon"}
    ha_state.return_value = {"deleted_ha_state": 3}

    result = await _run_memory_purge_superseded_job(pool, None)

    superseded.assert_awaited_once_with(pool, older_than_days=None)
    ha_state.assert_awaited_once_with(pool)
    assert result["superseded_deleted"] == 0
    assert result["deleted_ha_state"] == 3
    assert result["superseded_skipped"] == "no_horizon"
    assert log.await_args.args[1:3] == ("fact", 3)


async def test_an_unreadable_policy_keeps_everything_instead_of_defaulting_to_seven_days(
    wired, caplog
):
    pool, superseded, ha_state, _log = wired
    pool.fetchrow.side_effect = RuntimeError("connection reset")
    caplog.set_level(logging.WARNING)

    await _run_memory_purge_superseded_job(pool, None)

    superseded.assert_awaited_once_with(pool, older_than_days=None)
    ha_state.assert_awaited_once()
    assert "unreadable" in caplog.text


async def test_a_missing_fact_policy_row_keeps_everything(wired, caplog):
    pool, superseded, _ha, _log = wired
    pool.fetchrow.return_value = None
    caplog.set_level(logging.WARNING)

    await _run_memory_purge_superseded_job(pool, None)

    superseded.assert_awaited_once_with(pool, older_than_days=None)
    assert "no kind='fact'" in caplog.text


async def test_a_finite_horizon_is_passed_through(wired):
    pool, superseded, _ha, _log = wired
    pool.fetchrow.return_value = _row(30)
    superseded.return_value = {"deleted": 4}

    result = await _run_memory_purge_superseded_job(pool, None)

    superseded.assert_awaited_once_with(pool, older_than_days=30)
    assert result["superseded_deleted"] == 4


@pytest.mark.parametrize(
    ("override", "expected"), [(1, 30), (29, 30), (30, 30), (90, 90), (True, 30), (-5, 30)]
)
async def test_job_args_may_only_lengthen_a_finite_horizon(wired, override, expected):
    pool, superseded, _ha, _log = wired
    pool.fetchrow.return_value = _row(30)

    await _run_memory_purge_superseded_job(pool, {"older_than_days": override})

    superseded.assert_awaited_once_with(pool, older_than_days=expected)


async def test_job_args_never_enable_deletion_when_there_is_no_horizon(wired):
    pool, superseded, _ha, _log = wired
    pool.fetchrow.return_value = _row(None)

    await _run_memory_purge_superseded_job(pool, {"older_than_days": 1})

    superseded.assert_awaited_once_with(pool, older_than_days=None)


async def test_a_failing_superseded_purge_does_not_stop_the_ha_state_purge(wired):
    pool, superseded, ha_state, _log = wired
    superseded.return_value = {"deleted": 0, "error": "purge_failed"}
    ha_state.return_value = {"deleted_ha_state": 2}

    result = await _run_memory_purge_superseded_job(pool, None)

    assert result["deleted_ha_state"] == 2
    assert result["errors"] == ["superseded"]


async def test_nothing_removed_logs_no_compaction(wired):
    pool, _superseded, _ha, log = wired
    await _run_memory_purge_superseded_job(pool, None)
    log.assert_not_awaited()


async def test_read_fact_horizon_reads_null_as_keep_forever():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row(None))
    assert await _read_fact_horizon(pool) is None
    pool.fetchrow.return_value = _row(7)
    assert await _read_fact_horizon(pool) == 7


async def test_no_horizon_deletes_nothing_without_touching_the_pool():
    from butlers.modules.memory.storage import purge_superseded_facts

    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not open a connection"))
    assert await purge_superseded_facts(pool, older_than_days=None) == {
        "deleted": 0,
        "skipped": "no_horizon",
    }
