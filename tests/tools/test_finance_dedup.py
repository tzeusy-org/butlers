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


async def test_p1_dedup_match_and_skip():
    """P1 matches on external_id+account_id; skipped when external_id=None; no P2/P3 on match."""
    # Match
    pool = _mock_pool(fetchval_return=1, fetchrow_return=_make_row(_TXN_ID))
    assert (
        await _deduplicate(pool, {"external_id": "ext-123", "account_id": _ACCOUNT_ID}) == _TXN_ID
    )

    # No match
    pool2 = _mock_pool(fetchval_return=1, fetchrow_return=None)
    assert await _deduplicate(pool2, {"external_id": "new", "account_id": _ACCOUNT_ID}) is None

    # external_id=None → P1 skipped (fetchval not called); P2/P3 also not run (no required fields)
    pool3 = _mock_pool(fetchval_return=1, fetchrow_return=None)
    await _deduplicate(pool3, {"external_id": None, "account_id": _ACCOUNT_ID})
    pool3.fetchval.assert_not_called()

    # P1 match → no P2/P3 queries
    pool4 = _mock_pool(fetchval_return=1, fetchrow_return=_make_row(_TXN_ID))
    await _deduplicate(
        pool4,
        {
            "external_id": "ext-123",
            "account_id": _ACCOUNT_ID,
            "source_message_id": "msg-111",
            "posted_at": _NOW,
            "amount": Decimal("42.00"),
            "merchant": "Acme",
        },
    )
    assert pool4.fetchrow.call_count == 1


async def test_p2_and_p3_dedup():
    """P2 matches source_message_id; P3 matches composite key and normalizes negative amounts."""
    # P2 match
    pool = _mock_pool(fetchrow_return=_make_row(_TXN_ID))
    result = await _deduplicate(
        pool, {"external_id": None, "account_id": None, "source_message_id": "email-123@ex.com"}
    )
    assert result == _TXN_ID

    # P2 skipped when source_message_id=None
    pool2 = _mock_pool(fetchrow_return=None)
    await _deduplicate(pool2, {"external_id": None, "account_id": None, "source_message_id": None})
    pool2.fetchrow.assert_not_called()

    # P3 matches with positive and negative amounts
    base_txn = {
        "external_id": None,
        "account_id": _ACCOUNT_ID,
        "source_message_id": None,
        "posted_at": _NOW,
        "merchant": "Acme",
    }
    for amount in (Decimal("42.00"), Decimal("-42.00")):
        pool3 = _mock_pool(fetchrow_return=_make_row(_TXN_ID))
        assert await _deduplicate(pool3, {**base_txn, "amount": amount}) == _TXN_ID
