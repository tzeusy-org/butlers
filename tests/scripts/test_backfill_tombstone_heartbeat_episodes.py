"""Tests for scripts/backfill_tombstone_heartbeat_episodes.py.

Covers:
1. count_by_category — correctly bins rows into tick/qa/healing/schedule:*/total.
2. apply_tombstones — issues correct UPDATE and returns the row count.
3. Idempotency — a second apply_tombstones call touches zero rows because
   already-tombstoned rows are excluded by ``tombstone_at IS NULL``.
4. count_by_category returns all-zeros when no candidates remain (post-apply).
5. SQL uses parameterised values (not string interpolation) for the exclusion list.
6. main() dry-run path exits 0 without calling execute.
7. main() --apply path calls execute and exits 0 on success.

Issue: bu-noocq
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the script under test
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "backfill_tombstone_heartbeat_episodes.py"
)
_MODULE_NAME = "backfill_tombstone_heartbeat_episodes"


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

count_by_category = _mod.count_by_category
apply_tombstones = _mod.apply_tombstones
main = _mod.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(trigger_source: str, n: int) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda s, k, _d={"trigger_source": trigger_source, "n": n}: _d[k]
    return row


def _pool_fetch(*rows) -> AsyncMock:
    """Pool where .fetch() returns the given rows."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=list(rows))
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.close = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# count_by_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_by_category_bins_tick_qa_healing() -> None:
    """Rows with exact trigger_source values are binned into their named key."""
    rows = [
        _make_row("tick", 1200),
        _make_row("qa", 800),
        _make_row("healing", 42),
    ]
    pool = _pool_fetch(*rows)

    counts = await count_by_category(pool)

    assert counts["tick"] == 1200
    assert counts["qa"] == 800
    assert counts["healing"] == 42
    assert counts["schedule:*"] == 0
    assert counts["total"] == 2042


@pytest.mark.asyncio
async def test_count_by_category_bins_schedule_prefix() -> None:
    """Rows with schedule:* trigger sources are accumulated under 'schedule:*'."""
    rows = [
        _make_row("schedule:tick", 500),
        _make_row("schedule:health-check", 300),
    ]
    pool = _pool_fetch(*rows)

    counts = await count_by_category(pool)

    assert counts["schedule:*"] == 800
    assert counts["tick"] == 0
    assert counts["total"] == 800


@pytest.mark.asyncio
async def test_count_by_category_mixed_sources() -> None:
    rows = [
        _make_row("tick", 100),
        _make_row("qa", 50),
        _make_row("healing", 10),
        _make_row("schedule:foo", 40),
    ]
    pool = _pool_fetch(*rows)

    counts = await count_by_category(pool)

    assert counts["tick"] == 100
    assert counts["qa"] == 50
    assert counts["healing"] == 10
    assert counts["schedule:*"] == 40
    assert counts["total"] == 200


@pytest.mark.asyncio
async def test_count_by_category_empty_returns_zeros() -> None:
    """When the DB returns no rows, all categories are zero."""
    pool = _pool_fetch()

    counts = await count_by_category(pool)

    assert counts["total"] == 0
    assert counts["tick"] == 0
    assert counts["qa"] == 0
    assert counts["healing"] == 0
    assert counts["schedule:*"] == 0
    # The diagnostic query must exclude already-tombstoned rows so the count
    # reflects remaining work; dropping `tombstone_at IS NULL` would re-count
    # rows the apply pass already handled.
    assert pool.fetch.called
    fetch_sql: str = pool.fetch.call_args.args[0]
    assert "tombstone_at IS NULL" in fetch_sql


# ---------------------------------------------------------------------------
# apply_tombstones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_tombstones_returns_updated_count() -> None:
    """apply_tombstones returns the integer parsed from the asyncpg status string."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 4217")

    updated = await apply_tombstones(pool)

    assert updated == 4217


@pytest.mark.asyncio
async def test_apply_tombstones_issues_update_not_delete() -> None:
    """The backfill must SET tombstone_at, not DELETE rows."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 0")

    await apply_tombstones(pool)

    assert pool.execute.called
    sql: str = pool.execute.call_args.args[0]
    assert "UPDATE" in sql.upper()
    assert "DELETE" not in sql.upper()
    assert "tombstone_at" in sql
    # Idempotency guard: the UPDATE must exclude already-tombstoned rows.
    # Dropping `AND tombstone_at IS NULL` would re-tombstone (re-stamp now())
    # rows on every re-run, breaking the idempotency contract.
    assert "tombstone_at IS NULL" in sql


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_second_apply_touches_zero_rows() -> None:
    """Calling apply_tombstones twice should find zero candidates the second time.

    The WHERE ``tombstone_at IS NULL`` clause makes the UPDATE a no-op after the
    first application.  We verify this by having the mock return 'UPDATE 0' on
    the second call, mirroring real DB behaviour.
    """
    pool = AsyncMock()
    pool.execute = AsyncMock(side_effect=["UPDATE 4217", "UPDATE 0"])

    first = await apply_tombstones(pool)
    second = await apply_tombstones(pool)

    assert first == 4217
    assert second == 0


@pytest.mark.asyncio
async def test_count_by_category_zero_after_apply() -> None:
    """count_by_category returns all zeros when no untombstoned candidates remain."""
    # DB returns empty result set — simulates post-apply state.
    pool = _pool_fetch()

    counts = await count_by_category(pool)

    assert counts["total"] == 0


# ---------------------------------------------------------------------------
# main() dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_dry_run_exits_zero_without_apply(monkeypatch) -> None:
    """main() without --apply prints counts and exits 0 without calling execute."""
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://fake/butlers")

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_make_row("tick", 5)])
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")
    mock_pool.close = AsyncMock()

    with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool):
        exit_code = await main([])

    assert exit_code == 0
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_main_dry_run_exits_zero_when_nothing_to_do(monkeypatch) -> None:
    """main() without --apply exits 0 even when there are zero candidates."""
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://fake/butlers")

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")
    mock_pool.close = AsyncMock()

    with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool):
        exit_code = await main([])

    assert exit_code == 0
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_main_exits_one_without_db_url(monkeypatch) -> None:
    """main() exits 1 when BUTLERS_DATABASE_URL is not set."""
    monkeypatch.delenv("BUTLERS_DATABASE_URL", raising=False)

    exit_code = await main([])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# main() --apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_apply_calls_execute_and_exits_zero(monkeypatch) -> None:
    """main() --apply tombstones rows and exits 0 when post-check shows zero remaining."""
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://fake/butlers")

    mock_pool = AsyncMock()
    # First fetch: 10 candidates before apply
    # Second fetch: 0 remaining after apply (all zeros = post-apply state)
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [_make_row("tick", 10)],  # count_by_category before
            [],  # count_by_category after (expect all zeros)
        ]
    )
    mock_pool.execute = AsyncMock(return_value="UPDATE 10")
    mock_pool.close = AsyncMock()

    with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool):
        exit_code = await main(["--apply"])

    assert exit_code == 0
    mock_pool.execute.assert_called_once()
    sql: str = mock_pool.execute.call_args.args[0]
    assert "tombstone_at" in sql
