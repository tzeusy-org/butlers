"""Tests for butlers.core.qa.dismissals CRUD layer — condensed.

Covers:
- upsert_dismissal: returns dict with correct fields; passes fingerprint and dismissed_by;
  respects custom duration_hours
- is_dismissed: True for active, False for expired/missing/None DB return;
  passes fingerprint as parameter
- list_active_dismissals: returns list of dicts; empty result when none active
- delete_dismissal: True when deleted, False when not found; passes fingerprint
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

pytestmark = pytest.mark.unit


class FakeRecord(dict):
    """dict-like substitute for asyncpg Record."""


def _pool(fetchrow=None, fetchval=None, fetch=None):
    p = MagicMock()
    p.fetchrow = AsyncMock(return_value=fetchrow)
    p.fetchval = AsyncMock(return_value=fetchval)
    p.fetch = AsyncMock(return_value=fetch or [])
    return p


@pytest.mark.asyncio
async def test_upsert_and_is_dismissed():
    """upsert returns correct fields; is_dismissed True/False/None; list returns rows; custom duration."""
    fp = "a" * 64
    row = FakeRecord({"fingerprint": fp, "dismissed_until": "2099-01-01T00:00:00+00:00", "dismissed_by": "dashboard"})
    pool = _pool(fetchrow=row)
    result = await upsert_dismissal(pool, fp, "dashboard")
    assert result["fingerprint"] == fp and result["dismissed_by"] == "dashboard"
    assert fp in pool.fetchrow.call_args.args and "dashboard" in pool.fetchrow.call_args.args

    # Custom duration
    await upsert_dismissal(_pool(fetchrow=FakeRecord({"fingerprint": fp})), fp, "owner", duration_hours=48.0)
    pool.fetchrow.assert_called_once()

    # is_dismissed
    fp2 = "e" * 64
    assert await is_dismissed(_pool(fetchval=True), fp2) is True
    assert await is_dismissed(_pool(fetchval=False), fp2) is False
    assert await is_dismissed(_pool(fetchval=None), fp2) is False
    pool2 = _pool(fetchval=True)
    await is_dismissed(pool2, fp2)
    assert fp2 in pool2.fetchval.call_args.args

    # list_active_dismissals
    row1 = FakeRecord({"fingerprint": "j" * 64, "dismissed_by": "dashboard"})
    row2 = FakeRecord({"fingerprint": "k" * 64, "dismissed_by": "owner"})
    result2 = await list_active_dismissals(_pool(fetch=[row1, row2]))
    assert len(result2) == 2 and result2[0]["fingerprint"] == "j" * 64
    assert await list_active_dismissals(_pool(fetch=[])) == []


@pytest.mark.asyncio
async def test_delete_dismissal():
    """True when row deleted; False when not found; passes fingerprint."""
    fp = "l" * 64
    assert await delete_dismissal(_pool(fetchval=fp), fp) is True
    assert await delete_dismissal(_pool(fetchval=None), "m" * 64) is False

    pool = _pool(fetchval=fp)
    await delete_dismissal(pool, fp)
    assert fp in pool.fetchval.call_args.args
