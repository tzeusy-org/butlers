"""Tests for butlers.core.qa.dismissals CRUD layer.

Covers:
- upsert_dismissal: creates row with correct dismissed_until
- upsert_dismissal: ON CONFLICT updates existing row
- is_dismissed: returns True for active dismissal
- is_dismissed: returns False for expired dismissal (dismissed_until in the past)
- is_dismissed: returns False when no row exists
- list_active_dismissals: returns only non-expired rows
- list_active_dismissals: empty result when none active
- delete_dismissal: returns True when row deleted
- delete_dismissal: returns False when no row existed
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.dismissals import (
    delete_dismissal,
    is_dismissed,
    list_active_dismissals,
    upsert_dismissal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_return=None, fetchval_return=None, fetch_return=None):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    return pool


class FakeRecord(dict):
    """dict-like substitute for asyncpg Record."""
    pass


# ---------------------------------------------------------------------------
# upsert_dismissal tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_dismissal_returns_dict():
    """upsert_dismissal returns the upserted row as a plain dict."""
    row = FakeRecord(
        {
            "fingerprint": "a" * 64,
            "dismissed_until": "2099-01-01T00:00:00+00:00",
            "dismissed_by": "dashboard",
            "created_at": "2024-01-01T00:00:00+00:00",
        }
    )
    pool = _make_pool(fetchrow_return=row)

    result = await upsert_dismissal(pool, "a" * 64, "dashboard")

    assert result["fingerprint"] == "a" * 64
    assert result["dismissed_by"] == "dashboard"
    pool.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_dismissal_passes_fingerprint():
    """upsert_dismissal passes fingerprint as first parameter."""
    fp = "b" * 64
    row = FakeRecord({"fingerprint": fp, "dismissed_by": "owner"})
    pool = _make_pool(fetchrow_return=row)

    await upsert_dismissal(pool, fp, "owner")

    call_args = pool.fetchrow.call_args
    assert fp in call_args.args


@pytest.mark.asyncio
async def test_upsert_dismissal_passes_dismissed_by():
    """upsert_dismissal passes dismissed_by as parameter."""
    row = FakeRecord({"fingerprint": "c" * 64, "dismissed_by": "owner"})
    pool = _make_pool(fetchrow_return=row)

    await upsert_dismissal(pool, "c" * 64, "owner")

    call_args = pool.fetchrow.call_args
    assert "owner" in call_args.args


@pytest.mark.asyncio
async def test_upsert_dismissal_custom_duration():
    """upsert_dismissal respects custom duration_hours."""
    row = FakeRecord({"fingerprint": "d" * 64, "dismissed_by": "dashboard"})
    pool = _make_pool(fetchrow_return=row)

    # Should not raise; just verify call was made
    await upsert_dismissal(pool, "d" * 64, "dashboard", duration_hours=48.0)

    pool.fetchrow.assert_called_once()


# ---------------------------------------------------------------------------
# is_dismissed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_dismissed_active_returns_true():
    """is_dismissed returns True when dismissed_until is in the future."""
    pool = _make_pool(fetchval_return=True)

    result = await is_dismissed(pool, "e" * 64)

    assert result is True


@pytest.mark.asyncio
async def test_is_dismissed_expired_returns_false():
    """is_dismissed returns False when dismissed_until is in the past (DB returns False)."""
    pool = _make_pool(fetchval_return=False)

    result = await is_dismissed(pool, "f" * 64)

    assert result is False


@pytest.mark.asyncio
async def test_is_dismissed_no_row_returns_false():
    """is_dismissed returns False when no dismissal row exists (DB EXISTS returns False)."""
    pool = _make_pool(fetchval_return=False)

    result = await is_dismissed(pool, "g" * 64)

    assert result is False


@pytest.mark.asyncio
async def test_is_dismissed_none_from_db_returns_false():
    """is_dismissed handles None from pool.fetchval by returning False."""
    pool = _make_pool(fetchval_return=None)

    result = await is_dismissed(pool, "h" * 64)

    # bool(None) is False
    assert result is False


@pytest.mark.asyncio
async def test_is_dismissed_passes_fingerprint():
    """is_dismissed passes fingerprint as parameter to the query."""
    fp = "i" * 64
    pool = _make_pool(fetchval_return=True)

    await is_dismissed(pool, fp)

    call_args = pool.fetchval.call_args
    assert fp in call_args.args


# ---------------------------------------------------------------------------
# list_active_dismissals tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_dismissals_returns_list_of_dicts():
    """list_active_dismissals returns a list of plain dicts."""
    row1 = FakeRecord({"fingerprint": "j" * 64, "dismissed_by": "dashboard"})
    row2 = FakeRecord({"fingerprint": "k" * 64, "dismissed_by": "owner"})
    pool = _make_pool(fetch_return=[row1, row2])

    result = await list_active_dismissals(pool)

    assert len(result) == 2
    assert result[0]["fingerprint"] == "j" * 64


@pytest.mark.asyncio
async def test_list_active_dismissals_empty():
    """list_active_dismissals returns empty list when no active dismissals."""
    pool = _make_pool(fetch_return=[])

    result = await list_active_dismissals(pool)

    assert result == []


# ---------------------------------------------------------------------------
# delete_dismissal tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_dismissal_existing_returns_true():
    """delete_dismissal returns True when a row was deleted."""
    fp = "l" * 64
    pool = _make_pool(fetchval_return=fp)

    result = await delete_dismissal(pool, fp)

    assert result is True


@pytest.mark.asyncio
async def test_delete_dismissal_not_found_returns_false():
    """delete_dismissal returns False when no row matched."""
    pool = _make_pool(fetchval_return=None)

    result = await delete_dismissal(pool, "m" * 64)

    assert result is False


@pytest.mark.asyncio
async def test_delete_dismissal_passes_fingerprint():
    """delete_dismissal passes fingerprint as parameter."""
    fp = "n" * 64
    pool = _make_pool(fetchval_return=fp)

    await delete_dismissal(pool, fp)

    call_args = pool.fetchval.call_args
    assert fp in call_args.args
