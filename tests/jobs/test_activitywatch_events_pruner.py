"""Opt-in retention pruner for connectors.activitywatch_events. [bu-60pwv6.14]"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs.retention import prune_activitywatch_events

pytestmark = pytest.mark.unit


def _pool(count: int = 0, execute_result: str = "DELETE 0") -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"n": count})
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


async def test_disabled_by_default_touches_nothing() -> None:
    pool = _pool(count=5)
    result = await prune_activitywatch_events(pool)
    assert result == {"candidates": 0, "deleted": 0, "dry_run": True, "enabled": False}
    pool.fetchrow.assert_not_called()
    pool.execute.assert_not_called()


async def test_dry_run_counts_without_deleting() -> None:
    pool = _pool(count=12)
    result = await prune_activitywatch_events(pool, enabled=True, dry_run=True)
    assert result == {"candidates": 12, "deleted": 0, "dry_run": True, "enabled": True}
    pool.execute.assert_not_called()


async def test_ttl_boundary_cutoff_is_ts_older_than_ttl_days() -> None:
    pool = _pool(count=3, execute_result="DELETE 3")
    before = datetime.now(UTC)
    result = await prune_activitywatch_events(pool, enabled=True, dry_run=False, ttl_days=90)
    after = datetime.now(UTC)

    assert result["deleted"] == 3
    count_sql, count_cutoff = pool.fetchrow.call_args[0]
    delete_sql, delete_cutoff, _ = pool.execute.call_args[0]
    assert "connectors.activitywatch_events" in count_sql and "ts < $1" in count_sql
    assert "ts < $1" in delete_sql and "ORDER BY ts" in delete_sql
    assert count_cutoff == delete_cutoff
    assert before - timedelta(days=90) <= delete_cutoff <= after - timedelta(days=90)


@pytest.mark.parametrize("ttl_days", [89, 30, 0, -1])
async def test_ttl_below_the_90_day_floor_raises_before_any_query(ttl_days: int) -> None:
    pool = _pool(count=1)
    with pytest.raises(ValueError, match=">= 90"):
        await prune_activitywatch_events(pool, enabled=True, dry_run=False, ttl_days=ttl_days)
    pool.fetchrow.assert_not_called()
    pool.execute.assert_not_called()


async def test_batch_limit_bounds_the_delete() -> None:
    pool = _pool(count=1000, execute_result="DELETE 25")
    result = await prune_activitywatch_events(
        pool, enabled=True, dry_run=False, ttl_days=120, batch_limit=25
    )
    assert pool.execute.call_args[0][2] == 25
    assert result == {"candidates": 1000, "deleted": 25, "dry_run": False, "enabled": True}


async def test_registered_for_general_only_and_disabled_without_job_args() -> None:
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry: dict[str, dict[str, Any]] = get_deterministic_schedule_job_registry()
    assert "activitywatch_events_prune" in registry["general"]
    assert all(
        "activitywatch_events_prune" not in jobs
        for name, jobs in registry.items()
        if name != "general"
    )

    pool = _pool(count=9)
    result = await registry["general"]["activitywatch_events_prune"](pool, None)
    assert result["enabled"] is False
    pool.fetchrow.assert_not_called()
    pool.execute.assert_not_called()
