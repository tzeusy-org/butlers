"""Tests for RuntimeConfigAccessor — TTL-cached accessor for runtime_config table.

Covers:
- Cache hit within TTL
- Cache miss after TTL
- Seed on empty table
- No-op seed on existing row
- Re-seed after row deletion
- DB failure with stale cache (returns stale)
- DB failure with no cache (raises)
- Concurrent seed race (ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.config import RuntimeSeedConfig
from butlers.core.runtime_config import RuntimeConfigAccessor, _row_to_config

pytestmark = pytest.mark.unit


def _make_row(
    butler_name: str = "test",
    core_groups: list[str] | None = None,
    catalog_read_sensitivity: str = "normal",
    max_concurrent: int = 3,
    max_queued: int = 10,
    tool_exposure_policy: str = "eager_filtered",
    seeded_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    """Create a mock DB row dict."""
    return {
        "butler_name": butler_name,
        "core_groups": core_groups,
        "catalog_read_sensitivity": catalog_read_sensitivity,
        "max_concurrent": max_concurrent,
        "max_queued": max_queued,
        "tool_exposure_policy": tool_exposure_policy,
        "seeded_at": seeded_at,
        "updated_at": updated_at,
    }


def _mock_record(row_dict: dict) -> MagicMock:
    """Create a mock asyncpg.Record from a dict."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: row_dict[key]
    record.keys = lambda: row_dict.keys()
    return record


def _make_seed(
    core_groups: tuple[str, ...] | None = None,
    tool_exposure_policy: str = "eager_filtered",
) -> RuntimeSeedConfig:
    return RuntimeSeedConfig(
        core_groups=core_groups,
        max_concurrent_sessions=3,
        max_queued_sessions=10,
        tool_exposure_policy=tool_exposure_policy,
    )


async def test_cache_hit_within_ttl():
    """Second get() within TTL returns cached result without DB query."""
    pool = AsyncMock()
    row = _mock_record(_make_row())
    pool.fetchrow = AsyncMock(return_value=row)

    accessor = RuntimeConfigAccessor(pool, "test", ttl_s=30.0)
    result1 = await accessor.get()
    result2 = await accessor.get()

    assert result1.butler_name == "test"
    assert result1 is result2
    # Only one DB call despite two get() calls
    assert pool.fetchrow.call_count == 1


async def test_cache_miss_after_ttl():
    """get() after TTL expiry queries the DB again."""
    pool = AsyncMock()
    row1 = _mock_record(_make_row(max_concurrent=3))
    row2 = _mock_record(_make_row(max_concurrent=5))
    pool.fetchrow = AsyncMock(side_effect=[row1, row2])

    accessor = RuntimeConfigAccessor(pool, "test", ttl_s=0.01)
    result1 = await accessor.get()
    assert result1.max_concurrent == 3

    # Wait for TTL to expire
    await asyncio.sleep(0.02)
    result2 = await accessor.get()
    assert result2.max_concurrent == 5
    assert pool.fetchrow.call_count == 2


async def test_seed_on_empty_table():
    """seed_if_empty() inserts a row when the table is empty, including the hot policy."""
    pool = AsyncMock()
    seed = _make_seed(core_groups=("infra", "state"), tool_exposure_policy="auto")
    seeded_row = _mock_record(
        _make_row(core_groups=["infra", "state"], tool_exposure_policy="auto")
    )
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=seeded_row)

    accessor = RuntimeConfigAccessor(pool, "test")
    result = await accessor.seed_if_empty(seed, "test")

    assert result.core_groups == ("infra", "state")
    assert result.tool_exposure_policy == "auto"
    insert_sql, *insert_args = pool.execute.await_args_list[0].args
    assert "tool_exposure_policy" in insert_sql
    assert "auto" in insert_args


async def test_db_failure_with_stale_cache():
    """get() returns stale cache on DB failure when cache exists."""
    pool = AsyncMock()
    row = _mock_record(_make_row())
    pool.fetchrow = AsyncMock(side_effect=[row, Exception("DB unavailable")])

    accessor = RuntimeConfigAccessor(pool, "test", ttl_s=0.01)
    result1 = await accessor.get()
    assert result1.max_concurrent == 3

    # Wait for TTL to expire
    await asyncio.sleep(0.02)
    # DB fails, but stale cache is returned
    result2 = await accessor.get()
    assert result2.max_concurrent == 3
    assert result2 is result1  # same cached object


async def test_db_failure_with_no_cache():
    """get() raises on DB failure when no prior cache exists."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=Exception("DB unavailable"))

    accessor = RuntimeConfigAccessor(pool, "test")
    with pytest.raises(Exception, match="DB unavailable"):
        await accessor.get()


async def test_concurrent_seed_race():
    """Two concurrent seed_if_empty() calls don't fail (ON CONFLICT DO NOTHING)."""
    pool = AsyncMock()
    seed = _make_seed()
    seeded_row = _mock_record(_make_row())
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=seeded_row)

    accessor1 = RuntimeConfigAccessor(pool, "test")
    accessor2 = RuntimeConfigAccessor(pool, "test")

    results = await asyncio.gather(
        accessor1.seed_if_empty(seed, "test"),
        accessor2.seed_if_empty(seed, "test"),
    )
    assert all(r.butler_name == "test" for r in results)
    # Both should succeed without error
    assert pool.execute.call_count == 2
    # Seed INSERT must be race-safe / idempotent: ON CONFLICT DO NOTHING
    # (concurrent daemon starts must not raise a unique-violation).
    seed_sql = pool.execute.await_args_list[0].args[0]
    assert "ON CONFLICT" in seed_sql
    assert "DO NOTHING" in seed_sql


async def test_invalidate_cache():
    """invalidate_cache() forces next get() to query the DB."""
    pool = AsyncMock()
    row1 = _mock_record(_make_row(max_concurrent=3))
    row2 = _mock_record(_make_row(max_concurrent=7))
    pool.fetchrow = AsyncMock(side_effect=[row1, row2])

    accessor = RuntimeConfigAccessor(pool, "test", ttl_s=300.0)  # long TTL
    result1 = await accessor.get()
    assert result1.max_concurrent == 3

    accessor.invalidate_cache()
    result2 = await accessor.get()
    assert result2.max_concurrent == 7
    assert pool.fetchrow.call_count == 2


def test_row_to_config_with_null_core_groups():
    """NULL core_groups in DB maps to None in RuntimeConfig."""
    row = _mock_record(_make_row(core_groups=None))
    config = _row_to_config(row)
    assert config.core_groups is None
    assert not hasattr(config, "model")
    assert not hasattr(config, "runtime_type")
    assert not hasattr(config, "args")
    assert not hasattr(config, "session_timeout_s")


def test_row_to_config_with_core_groups():
    """Array core_groups in DB maps to tuple in RuntimeConfig."""
    row = _mock_record(_make_row(core_groups=["infra", "state"]))
    config = _row_to_config(row)
    assert config.core_groups == ("infra", "state")


def test_row_to_config_preserves_catalog_read_sensitivity():
    row = _mock_record(_make_row(catalog_read_sensitivity="internal"))

    config = _row_to_config(row)

    assert config.catalog_read_sensitivity == "internal"


def test_row_to_config_missing_catalog_authority_fails_closed_normal():
    row_data = _make_row()
    del row_data["catalog_read_sensitivity"]

    config = _row_to_config(_mock_record(row_data))

    assert config.catalog_read_sensitivity == "normal"


def test_row_to_config_preserves_tool_exposure_policy():
    row = _mock_record(_make_row(tool_exposure_policy="auto"))

    config = _row_to_config(row)

    assert config.tool_exposure_policy == "auto"


def test_row_to_config_missing_tool_exposure_policy_fails_closed_eager_filtered():
    """A rolling pre-core_224 row shape must never opt a butler into native discovery."""
    row_data = _make_row()
    del row_data["tool_exposure_policy"]

    config = _row_to_config(_mock_record(row_data))

    assert config.tool_exposure_policy == "eager_filtered"


async def test_get_tool_exposure_policy_bypasses_the_ttl_cache():
    """The per-attempt authoritative read always hits the DB, even within a warm TTL cache.

    This is the mechanism that makes the hot policy correct across process
    boundaries (bu-ondtw.2 design): a cached get() can be stale for up to
    ttl_s, but every session-planning caller must see a committed PATCH
    immediately regardless of which process wrote it.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            _mock_record(_make_row(tool_exposure_policy="eager_filtered")),
            {"tool_exposure_policy": "auto"},
        ]
    )

    accessor = RuntimeConfigAccessor(pool, "test", ttl_s=300.0)
    warm = await accessor.get()
    assert warm.tool_exposure_policy == "eager_filtered"

    live = await accessor.get_tool_exposure_policy()

    assert live == "auto"
    assert pool.fetchrow.call_count == 2


async def test_get_tool_exposure_policy_falls_back_to_stale_cache_on_db_failure():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            _mock_record(_make_row(tool_exposure_policy="auto")),
            Exception("DB unavailable"),
        ]
    )

    accessor = RuntimeConfigAccessor(pool, "test")
    await accessor.get()

    live = await accessor.get_tool_exposure_policy()

    assert live == "auto"


async def test_get_tool_exposure_policy_raises_with_no_cache_and_no_row():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    accessor = RuntimeConfigAccessor(pool, "test")

    with pytest.raises(RuntimeError, match="No runtime_config row found"):
        await accessor.get_tool_exposure_policy()
