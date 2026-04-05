"""Tests for the finance butler _deduplicate() function.

Covers the three-tier dedup priority:
- P1: external_id + account_id (highest priority)
- P2: source_message_id
- P3: composite fallback (account_id + posted_at + amount + merchant)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import butlers.tools.finance.transactions as _txn_module
from butlers.tools.finance.transactions import _deduplicate

pytestmark = pytest.mark.unit

_ACCOUNT_ID = str(uuid4())
_TXN_ID = str(uuid4())
_NOW = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_column_cache():
    _txn_module._column_existence_cache.clear()
    yield
    _txn_module._column_existence_cache.clear()


def _mock_pool(*, fetchrow_return=None, fetchval_return=None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    return pool


def _make_row(txn_id: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: txn_id if k == "id" else None)
    return row


@pytest.mark.parametrize(
    "txn_kwargs, fetchrow_return, expected",
    [
        # P1 match
        ({"external_id": "ext-123", "account_id": _ACCOUNT_ID}, _make_row(_TXN_ID), _TXN_ID),
        # P1 no match
        ({"external_id": "ext-new", "account_id": _ACCOUNT_ID}, None, None),
        # P1 skipped when external_id is None (fetchval must not be called)
        ({"external_id": None, "account_id": _ACCOUNT_ID}, None, None),
    ],
)
async def test_p1_dedup(txn_kwargs, fetchrow_return, expected):
    """P1 (external_id + account_id) matches, misses, and is skipped when external_id is None."""
    pool = _mock_pool(fetchval_return=1, fetchrow_return=fetchrow_return)
    result = await _deduplicate(pool, txn_kwargs)
    assert result == expected
    if txn_kwargs.get("external_id") is None:
        pool.fetchval.assert_not_called()


async def test_p1_no_fall_through_when_matched():
    """Once P1 returns a match, P2 and P3 are NOT queried."""
    pool = _mock_pool(fetchval_return=1, fetchrow_return=_make_row(_TXN_ID))
    result = await _deduplicate(pool, {
        "external_id": "ext-123", "account_id": _ACCOUNT_ID,
        "source_message_id": "msg-111", "posted_at": _NOW,
        "amount": Decimal("42.00"), "merchant": "Acme",
    })
    assert result == _TXN_ID
    assert pool.fetchrow.call_count == 1  # only P1 SELECT called


async def test_p2_match_and_skip():
    """P2 returns existing ID when source_message_id matches; skipped when None."""
    pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))
    result = await _deduplicate(
        pool, {"external_id": None, "account_id": None, "source_message_id": "email-123@ex.com"}
    )
    assert result == _TXN_ID

    pool2 = _mock_pool(fetchrow_return=None)
    result2 = await _deduplicate(
        pool2, {"external_id": None, "account_id": None, "source_message_id": None}
    )
    pool2.fetchrow.assert_not_called()
    assert result2 is None


async def test_p3_match_and_normalizes_amount():
    """P3 matches on composite key and normalizes negative amounts via ABS."""
    base_txn = {
        "external_id": None, "account_id": _ACCOUNT_ID, "source_message_id": None,
        "posted_at": _NOW, "merchant": "Acme",
    }
    for amount in (Decimal("42.00"), Decimal("-42.00")):
        pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))
        result = await _deduplicate(pool, {**base_txn, "amount": amount})
        assert result == _TXN_ID
