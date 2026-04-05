"""Tests for finance transaction CRUD operations.

Covers key behavioral contracts for record_transaction, list_transactions,
spending_summary, update_transaction, delete_transaction, merge_duplicates,
split_transaction, and bulk_recategorize.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import butlers.tools.finance.transactions as _txn_module
from butlers.tools.finance.spending import spending_summary
from butlers.tools.finance.transactions import (
    bulk_recategorize,
    delete_transaction,
    list_transactions,
    merge_duplicates,
    record_transaction,
    split_transaction,
    update_transaction,
)

pytestmark = pytest.mark.unit

_ACCOUNT_ID = str(uuid4())
_TXN_ID = str(uuid4())
_TXN_ID2 = str(uuid4())
_NOW = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_column_cache():
    _txn_module._column_existence_cache.clear()
    yield
    _txn_module._column_existence_cache.clear()


def _make_txn_row(
    *,
    id: str = _TXN_ID,
    posted_at: datetime = _NOW,
    merchant: str = "ACME Corp",
    amount: Decimal = Decimal("42.00"),
    currency: str = "USD",
    direction: str = "debit",
    category: str = "shopping",
    version: int = 1,
    deleted_at=None,
    is_duplicate: bool = False,
) -> MagicMock:
    data = {
        "id": id, "posted_at": posted_at, "merchant": merchant,
        "amount": amount, "currency": currency, "direction": direction,
        "category": category, "description": None, "payment_method": None,
        "account_id": _ACCOUNT_ID, "source_message_id": None,
        "external_ref": None, "external_id": None, "receipt_url": None,
        "metadata": json.dumps({}), "deleted_at": deleted_at,
        "version": version, "updated_at": _NOW,
        "category_source": "manual", "is_category_locked": False,
        "is_duplicate": is_duplicate, "duplicate_of": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    row.get = MagicMock(side_effect=lambda k, default=None: data.get(k, default))
    row._data = data
    return row


def _make_pool(*, fetchrow_return=None, fetchval_return=None, fetch_return=None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock(return_value="UPDATE 0")
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    txn_ctx = AsyncMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_ctx)
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._conn = conn
    return pool


async def test_record_transaction_dedup_returns_existing():
    """When dedup finds an existing transaction, returns existing row without INSERT."""
    existing_row = _make_txn_row(id=_TXN_ID, merchant="Netflix")
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0)
    dedup_id_row = MagicMock()
    dedup_id_row.__getitem__ = MagicMock(side_effect=lambda k: _TXN_ID if k == "id" else None)
    pool.fetchrow = AsyncMock(side_effect=[dedup_id_row, existing_row])

    with patch("butlers.tools.finance.transactions._mirror_to_spo"):
        result = await record_transaction(
            pool=pool, posted_at=_NOW, merchant="Netflix",
            amount=Decimal("15.99"), currency="USD", category="subscriptions",
            source_message_id="msg-abc-123",
        )

    pool.execute.assert_not_called()
    assert result["id"] == _TXN_ID


async def test_list_transactions_shape_and_soft_delete_filter():
    """list_transactions returns paginated shape and excludes soft-deleted rows."""
    count_row = MagicMock()
    count_row.__getitem__ = MagicMock(side_effect=lambda k: 1 if k == "total" else None)
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=1)  # deleted_at column exists
    pool.fetchrow = AsyncMock(return_value=count_row)
    pool.fetch = AsyncMock(return_value=[_make_txn_row()])

    result = await list_transactions(pool)

    assert all(k in result for k in ("items", "total", "limit", "offset"))
    query = pool.fetchrow.call_args.args[0]
    assert "deleted_at IS NULL" in query


async def test_spending_summary_shape_debit_filter_and_invalid_group_by():
    """spending_summary returns required fields, filters debit-only, rejects invalid group_by."""
    def _mock_row(**kwargs):
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda k: kwargs.get(k))
        return r

    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(side_effect=[
        _mock_row(total=Decimal("100.00")),
        _mock_row(currency="USD", cnt=1),
        _mock_row(cnt=3),
    ])
    pool.fetch = AsyncMock(return_value=[])

    result = await spending_summary(pool, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    assert all(k in result for k in ("start_date", "end_date", "currency", "total_spend", "groups"))
    query = pool.fetchrow.call_args_list[0].args[0]
    assert "direction = 'debit'" in query

    with pytest.raises(ValueError, match="Unsupported group_by"):
        await spending_summary(pool, group_by="invalid_group")


@pytest.mark.parametrize(
    "fetchrow_side_effect, extra_kwargs, expected_error",
    [
        ([None], {}, "transaction_not_found"),
        ([_make_txn_row(version=5)], {"expected_version": 3}, "version_conflict"),
    ],
)
async def test_update_transaction_error_paths(fetchrow_side_effect, extra_kwargs, expected_error):
    """update_transaction returns structured errors for not_found and version_conflict."""
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0 if fetchrow_side_effect == [None] else 1)
    pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect if fetchrow_side_effect[0] is None
                              else fetchrow_side_effect + [None])

    result = await update_transaction(pool, _TXN_ID, category="dining", **extra_kwargs)
    assert result["error"] == expected_error


def test_no_delete_from_in_transactions_module():
    """The finance transactions module must not use DELETE FROM (soft-delete only)."""
    import inspect
    source = inspect.getsource(_txn_module)
    assert "DELETE FROM" not in source.upper()


async def test_merge_duplicates_empty_input_returns_error():
    """merge_duplicates requires duplicate_ids list, rejects empty input."""
    pool = _make_pool()
    result = await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[])
    assert "error" in result


async def test_split_transaction_amount_mismatch_returns_error():
    """split_transaction returns error when split amounts don't sum to original."""
    original = _make_txn_row(id=_TXN_ID, amount=Decimal("100.00"))
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(return_value=original)

    result = await split_transaction(
        pool, _TXN_ID,
        splits=[
            {"amount": Decimal("40.00"), "category": "dining"},
            {"amount": Decimal("50.00"), "category": "groceries"},
        ],
    )
    assert "error" in result


async def test_bulk_recategorize_dry_run_returns_matched_count_without_update():
    """Dry run returns matched count without executing updates."""
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=3)

    result = await bulk_recategorize(
        pool, merchant_pattern="netflix", new_category="subscriptions", dry_run=True
    )
    assert "matched_count" in result
    pool.execute.assert_not_called()
