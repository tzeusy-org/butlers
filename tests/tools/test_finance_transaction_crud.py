"""Tests for finance transaction CRUD operations."""

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
    list_transactions,
    merge_duplicates,
    record_transaction,
    split_transaction,
    update_transaction,
)

pytestmark = pytest.mark.unit

_ACCOUNT_ID = str(uuid4())
_TXN_ID = str(uuid4())
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
        "id": id,
        "posted_at": posted_at,
        "merchant": merchant,
        "amount": amount,
        "currency": currency,
        "direction": direction,
        "category": category,
        "description": None,
        "payment_method": None,
        "account_id": _ACCOUNT_ID,
        "source_message_id": None,
        "external_ref": None,
        "external_id": None,
        "receipt_url": None,
        "metadata": json.dumps({}),
        "deleted_at": deleted_at,
        "version": version,
        "updated_at": _NOW,
        "category_source": "manual",
        "is_category_locked": False,
        "is_duplicate": is_duplicate,
        "duplicate_of": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    row.get = MagicMock(side_effect=lambda k, default=None: data.get(k, default))
    row.keys = MagicMock(return_value=data.keys())
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


class _CategoryTaxonomyPool:
    def __init__(self, categories: set[str] | None):
        self.categories = categories

    async def fetchval(self, _query, *_args):
        return int(self.categories is not None)

    async def fetchrow(self, query, *args):
        if self.categories is None:
            return None
        if "lower(name) = lower($1)" in query:
            candidate = str(args[0]).lower()
            match = next(
                (category for category in self.categories if category.lower() == candidate), None
            )
            return {"name": match} if match is not None else None
        if "name = 'uncategorized'" in query and "uncategorized" in self.categories:
            return {"name": "uncategorized"}
        return None


async def test_unknown_category_falls_back_to_uncategorized_when_taxonomy_exists():
    """Unknown categories are converted before the category FK can reject the insert."""
    metadata: dict = {}
    category, used_fallback = await _txn_module._resolve_category_for_insert(
        _CategoryTaxonomyPool({"groceries", "uncategorized"}),
        "misc",
        metadata,
    )

    assert category == "uncategorized"
    assert used_fallback is True
    assert metadata == {
        "original_category": "misc",
        "warnings": [
            {
                "code": "unknown_category",
                "field": "category",
                "stored_as": "uncategorized",
            }
        ],
    }


async def test_category_lookup_is_case_insensitive_when_taxonomy_exists():
    metadata: dict = {}
    category, used_fallback = await _txn_module._resolve_category_for_insert(
        _CategoryTaxonomyPool({"groceries", "uncategorized"}),
        "Groceries",
        metadata,
    )

    assert category == "groceries"
    assert used_fallback is False
    assert metadata == {}


async def test_freeform_category_is_preserved_when_taxonomy_is_absent():
    metadata: dict = {}
    category, used_fallback = await _txn_module._resolve_category_for_insert(
        _CategoryTaxonomyPool(None),
        "misc",
        metadata,
    )

    assert category == "misc"
    assert used_fallback is False
    assert metadata == {}


async def test_record_and_list_transactions():
    """Dedup returns existing without INSERT; list excludes soft-deleted rows."""
    existing_row = _make_txn_row(id=_TXN_ID, merchant="Netflix")
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0)
    dedup_id_row = MagicMock()
    dedup_id_row.__getitem__ = MagicMock(side_effect=lambda k: _TXN_ID if k == "id" else None)
    pool.fetchrow = AsyncMock(side_effect=[dedup_id_row, existing_row])

    with patch("butlers.tools.finance.transactions._mirror_to_spo"):
        result = await record_transaction(
            pool=pool,
            posted_at=_NOW,
            merchant="Netflix",
            amount=Decimal("15.99"),
            currency="USD",
            category="subscriptions",
            source_message_id="msg-abc-123",
        )
    pool.execute.assert_not_called()
    assert result["id"] == _TXN_ID

    count_row = MagicMock()
    count_row.__getitem__ = MagicMock(side_effect=lambda k: 1 if k == "total" else None)
    pool2 = _make_pool()
    pool2.fetchval = AsyncMock(return_value=1)
    pool2.fetchrow = AsyncMock(return_value=count_row)
    pool2.fetch = AsyncMock(return_value=[_make_txn_row()])
    result2 = await list_transactions(pool2)
    assert all(k in result2 for k in ("items", "total", "limit", "offset"))
    assert "deleted_at IS NULL" in pool2.fetchrow.call_args.args[0]


async def test_spending_summary_update_and_edge_cases():
    """spending_summary: debit filter, invalid group_by; update errors; no DELETE FROM; dry run."""

    def _mock_row(**kwargs):
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda k: kwargs.get(k))
        return r

    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(
        side_effect=[
            _mock_row(total=Decimal("100.00")),
            _mock_row(currency="USD", cnt=1),
            _mock_row(cnt=3),
        ]
    )
    pool.fetch = AsyncMock(return_value=[])

    summary = await spending_summary(pool, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    assert all(
        k in summary for k in ("start_date", "end_date", "currency", "total_spend", "groups")
    )
    assert "direction = 'debit'" in pool.fetchrow.call_args_list[0].args[0]

    with pytest.raises(ValueError, match="Unsupported group_by"):
        await spending_summary(pool, group_by="invalid_group")

    # update_transaction: not_found and version_conflict
    pool2 = _make_pool()
    pool2.fetchval = AsyncMock(return_value=0)
    pool2.fetchrow = AsyncMock(side_effect=[None])
    assert (await update_transaction(pool2, _TXN_ID, category="dining"))[
        "error"
    ] == "transaction_not_found"

    pool3 = _make_pool()
    pool3.fetchval = AsyncMock(return_value=1)
    pool3.fetchrow = AsyncMock(side_effect=[_make_txn_row(version=5), None])
    assert (await update_transaction(pool3, _TXN_ID, category="dining", expected_version=3))[
        "error"
    ] == "version_conflict"

    # No DELETE FROM in module
    import inspect

    assert "DELETE FROM" not in inspect.getsource(_txn_module).upper()

    # merge requires duplicate_ids; split rejects mismatch; dry_run
    pool4 = _make_pool()
    assert "error" in await merge_duplicates(pool4, keep_id=_TXN_ID, duplicate_ids=[])

    pool5 = _make_pool()
    pool5.fetchval = AsyncMock(return_value=0)
    pool5.fetchrow = AsyncMock(return_value=_make_txn_row(id=_TXN_ID, amount=Decimal("100.00")))
    assert "error" in await split_transaction(
        pool5,
        _TXN_ID,
        splits=[
            {"amount": Decimal("40.00"), "category": "dining"},
            {"amount": Decimal("50.00"), "category": "groceries"},
        ],
    )

    pool6 = _make_pool()
    pool6.fetchval = AsyncMock(return_value=3)
    result = await bulk_recategorize(
        pool6, merchant_pattern="netflix", new_category="subscriptions", dry_run=True
    )
    assert "matched" in result and result["dry_run"] is True
    pool6.execute.assert_not_called()
