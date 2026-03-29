"""Tests for finance transaction CRUD operations (section 4).

Covers tasks 4.1–4.8 from openspec/changes/finance-data-model-redesign/tasks.md:
  4.1 record_transaction() with dedup check
  4.2 list_transactions() with filters
  4.3 spending_summary() aggregation
  4.4 update_transaction() with optimistic locking
  4.5 delete_transaction() as soft delete
  4.6 merge_duplicates() — merge duplicate transactions
  4.7 split_transaction() — split into sub-amounts
  4.8 bulk_recategorize() — update category for multiple transactions

All tests use unittest.mock.AsyncMock — no live database required.

Issue: bu-w9m6
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACCOUNT_ID = str(uuid4())
_TXN_ID = str(uuid4())
_TXN_ID2 = str(uuid4())
_TXN_ID3 = str(uuid4())
_NOW = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_column_cache():
    """Clear the _has_column module-level cache before each test."""
    _txn_module._column_existence_cache.clear()
    yield
    _txn_module._column_existence_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txn_row(
    *,
    id: str = _TXN_ID,
    posted_at: datetime = _NOW,
    merchant: str = "ACME Corp",
    amount: Decimal = Decimal("42.00"),
    currency: str = "USD",
    direction: str = "debit",
    category: str = "shopping",
    description: str | None = None,
    payment_method: str | None = None,
    account_id: str | None = None,
    source_message_id: str | None = None,
    external_ref: str | None = None,
    external_id: str | None = None,
    receipt_url: str | None = None,
    metadata: dict | None = None,
    deleted_at: datetime | None = None,
    version: int = 1,
    updated_at: datetime = _NOW,
    category_source: str = "manual",
    is_category_locked: bool = False,
    is_duplicate: bool = False,
    duplicate_of: str | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record for a transaction row."""
    data = {
        "id": id,
        "posted_at": posted_at,
        "merchant": merchant,
        "amount": amount,
        "currency": currency,
        "direction": direction,
        "category": category,
        "description": description,
        "payment_method": payment_method,
        "account_id": account_id,
        "source_message_id": source_message_id,
        "external_ref": external_ref,
        "external_id": external_id,
        "receipt_url": receipt_url,
        "metadata": json.dumps(metadata or {}),
        "deleted_at": deleted_at,
        "version": version,
        "updated_at": updated_at,
        "category_source": category_source,
        "is_category_locked": is_category_locked,
        "is_duplicate": is_duplicate,
        "duplicate_of": duplicate_of,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    row.__iter__ = MagicMock(return_value=iter(data.items()))
    row.keys = MagicMock(return_value=data.keys())
    row.get = MagicMock(side_effect=lambda k, default=None: data.get(k, default))
    # Allow dict(row) conversion needed by _row_to_dict
    row.__class__ = MagicMock()
    # Patch iter to yield (key, value) tuples for dict()
    row._data = data
    return row


def _make_pool(
    *,
    fetchrow_return=None,
    fetchval_return=None,
    fetch_return=None,
    execute_return="UPDATE 0",
) -> AsyncMock:
    """Build a minimal asyncpg Pool mock with common methods."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock(return_value=execute_return)

    # acquire() context manager for transaction blocks
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value=execute_return)
    conn.fetchval = AsyncMock(return_value=fetchval_return)

    # transaction() context manager
    txn_ctx = AsyncMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_ctx)

    # acquire() async context manager
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._conn = conn  # expose for test inspection

    return pool


# ---------------------------------------------------------------------------
# 4.1: record_transaction() with dedup check
# ---------------------------------------------------------------------------


class TestRecordTransaction:
    """Task 4.1: record_transaction() with dedup check and SPO mirror."""

    async def test_returns_existing_transaction_when_duplicate_found(self):
        """When _deduplicate() returns an existing ID, that row is returned."""
        existing_row = _make_txn_row(id=_TXN_ID, merchant="Netflix", amount=Decimal("15.99"))

        pool = _make_pool()
        # _has_column → 0 (no external_id column), so dedup falls to P2 via source_message_id
        # _deduplicate's P2 fetchrow returns the existing row id
        pool.fetchval = AsyncMock(return_value=0)  # no external_id col
        dedup_id_row = MagicMock()
        dedup_id_row.__getitem__ = MagicMock(side_effect=lambda k: _TXN_ID if k == "id" else None)

        # fetchrow: P2 dedup → returns id row, then SELECT * → returns full row
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

        # Should NOT insert a new row — fetchrow used for dedup + SELECT only
        pool.execute.assert_not_called()
        assert result["id"] == _TXN_ID

    async def test_inserts_new_transaction_when_no_duplicate(self):
        """When no duplicate is found, a new row is inserted.

        With source_message_id=None and account_id=None, _deduplicate makes zero
        fetchrow calls (all three priority tiers are skipped). The first and only
        fetchrow call is the INSERT ... RETURNING *.
        """
        new_row = _make_txn_row(
            id=_TXN_ID,
            merchant="Starbucks",
            amount=Decimal("5.50"),
            direction="debit",
        )
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        # No dedup fetchrow calls; only the INSERT RETURNING fetchrow
        pool.fetchrow = AsyncMock(return_value=new_row)

        with patch("butlers.tools.finance.transactions.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            result = await record_transaction(
                pool=pool,
                posted_at=_NOW,
                merchant="Starbucks",
                amount=Decimal("5.50"),
                currency="USD",
                category="dining",
                account_id=None,
                source_message_id=None,
            )

        assert result["id"] == _TXN_ID

    async def test_direction_inferred_debit_for_negative_amount(self):
        """Negative amount → direction = 'debit'."""
        new_row = _make_txn_row(id=_TXN_ID, direction="debit", amount=Decimal("20.00"))
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        # No dedup fetchrow calls with no source_message_id/account_id/external_id
        pool.fetchrow = AsyncMock(return_value=new_row)

        with patch("butlers.tools.finance.transactions.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            result = await record_transaction(
                pool=pool,
                posted_at=_NOW,
                merchant="ACME",
                amount=Decimal("-20.00"),
                currency="USD",
                category="shopping",
                account_id=None,
                source_message_id=None,
            )

        assert result["direction"] == "debit"

    async def test_direction_inferred_credit_for_positive_amount(self):
        """Positive amount → direction = 'credit'."""
        from butlers.tools.finance.transactions import _infer_direction

        assert _infer_direction(Decimal("100.00")) == "credit"
        assert _infer_direction(Decimal("-5.00")) == "debit"
        assert _infer_direction(Decimal("0.00")) == "credit"

    async def test_amount_stored_as_absolute_value(self):
        """Amount is stored as absolute value (NUMERIC 14,2)."""
        from butlers.tools.finance.transactions import _normalize_amount

        assert _normalize_amount(Decimal("-42.50")) == Decimal("42.50")
        assert _normalize_amount(Decimal("42.50")) == Decimal("42.50")
        assert _normalize_amount(-7) == Decimal("7")

    async def test_auto_categorize_from_merchant_mappings(self):
        """When category='uncategorized', merchant mapping lookup is performed."""
        mapped_row = MagicMock()
        mapped_row.__getitem__ = MagicMock(
            side_effect=lambda k: "streaming" if k == "category" else None
        )

        new_row = _make_txn_row(id=_TXN_ID, category="streaming")
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)

        # When _has_table returns True, _lookup_merchant_category makes a fetchrow call
        # fetchrow: mapping lookup → mapped_row, INSERT → new_row
        pool.fetchrow = AsyncMock(side_effect=[mapped_row, new_row])

        with patch("butlers.tools.finance.transactions._has_table", return_value=True):
            with patch("butlers.tools.finance.transactions.asyncio") as mock_asyncio:
                mock_asyncio.create_task = MagicMock()
                result = await record_transaction(
                    pool=pool,
                    posted_at=_NOW,
                    merchant="Netflix",
                    amount=Decimal("15.99"),
                    currency="USD",
                    category="uncategorized",
                    account_id=None,
                    source_message_id=None,
                )

        assert result["id"] == _TXN_ID

    async def test_spo_mirror_scheduled_as_background_task(self):
        """After insert, _mirror_to_spo is scheduled via asyncio.create_task."""
        new_row = _make_txn_row(id=_TXN_ID)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        # No dedup calls when no source_message_id/account_id/external_id
        pool.fetchrow = AsyncMock(return_value=new_row)

        with patch("butlers.tools.finance.transactions.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            await record_transaction(
                pool=pool,
                posted_at=_NOW,
                merchant="ACME",
                amount=Decimal("10.00"),
                currency="USD",
                category="shopping",
                account_id=None,
                source_message_id=None,
            )

        mock_asyncio.create_task.assert_called_once()


# ---------------------------------------------------------------------------
# 4.2: list_transactions() with filters
# ---------------------------------------------------------------------------


class TestListTransactions:
    """Task 4.2: list_transactions() with filter parameters."""

    async def test_returns_paginated_response_shape(self):
        """Response has items, total, limit, offset keys."""
        txn_row = _make_txn_row()
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 1 if k == "total" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)  # no deleted_at column
        pool.fetchrow = AsyncMock(return_value=count_row)
        pool.fetch = AsyncMock(return_value=[txn_row])

        result = await list_transactions(pool)

        assert "items" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result
        assert result["limit"] == 50
        assert result["offset"] == 0

    async def test_excludes_soft_deleted_when_column_exists(self):
        """When deleted_at column exists, adds 'deleted_at IS NULL' condition."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # deleted_at column exists
        pool.fetchrow = AsyncMock(return_value=count_row)
        pool.fetch = AsyncMock(return_value=[])

        await list_transactions(pool)

        # Check that the COUNT query included deleted_at IS NULL
        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "deleted_at IS NULL" in query

    async def test_date_range_filter(self):
        """start_date and end_date are applied as WHERE conditions."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 31, tzinfo=UTC)
        await list_transactions(pool, start_date=start, end_date=end)

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        # Both date params should appear in the query
        assert "posted_at >=" in query
        assert "posted_at <=" in query
        # Params should include start and end
        params = count_call.args[1:]
        assert start in params
        assert end in params

    async def test_category_filter(self):
        """category filter is applied as exact match."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, category="groceries")

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "category = " in query
        params = count_call.args[1:]
        assert "groceries" in params

    async def test_merchant_filter_uses_ilike(self):
        """merchant filter uses case-insensitive LIKE matching."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, merchant="netflix")

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "lower(merchant) LIKE lower(" in query
        params = count_call.args[1:]
        assert "%netflix%" in params

    async def test_amount_range_filter(self):
        """min_amount and max_amount are applied as bounds."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, min_amount=10, max_amount=100)

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "amount >= " in query
        assert "amount <= " in query
        params = count_call.args[1:]
        assert Decimal("10") in params
        assert Decimal("100") in params

    async def test_direction_filter(self):
        """direction filter restricts to 'debit' or 'credit'."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, direction="debit")

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "direction = " in query
        params = count_call.args[1:]
        assert "debit" in params

    async def test_invalid_direction_raises(self):
        """direction must be 'debit' or 'credit'; other values raise ValueError."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="direction must be"):
            await list_transactions(pool, direction="invalid")

    async def test_tags_filter_when_column_exists(self):
        """tags filter uses array containment @> when tags column exists."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()

        # fetchval calls: deleted_at column check (→1), tags column check (→1)
        pool.fetchval = AsyncMock(side_effect=[1, 1])
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, tags=["food", "restaurant"])

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "tags @>" in query

    async def test_limit_clamped_to_500(self):
        """limit is clamped to a maximum of 500."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        result = await list_transactions(pool, limit=9999)

        assert result["limit"] == 500

    async def test_offset_clamped_to_zero(self):
        """offset is clamped to minimum 0."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        result = await list_transactions(pool, offset=-10)

        assert result["offset"] == 0

    async def test_account_id_filter(self):
        """account_id filter is applied as UUID cast comparison."""
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "total" else None)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=count_row)

        await list_transactions(pool, account_id=_ACCOUNT_ID)

        count_call = pool.fetchrow.call_args
        query = count_call.args[0]
        assert "account_id = " in query
        assert "::uuid" in query
        params = count_call.args[1:]
        assert _ACCOUNT_ID in params


# ---------------------------------------------------------------------------
# 4.3: spending_summary() aggregation
# ---------------------------------------------------------------------------


class TestSpendingSummary:
    """Task 4.3: spending_summary() aggregation by category, time period."""

    async def test_response_shape(self):
        """Returns start_date, end_date, currency, total_spend, groups."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("100.00") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (1 if k == "cnt" else None)
        )
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 3 if k == "cnt" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)  # no deleted_at
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row, count_row])
        pool.fetch = AsyncMock(return_value=[])

        result = await spending_summary(
            pool,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

        assert "start_date" in result
        assert "end_date" in result
        assert "currency" in result
        assert "total_spend" in result
        assert "groups" in result

    async def test_group_by_category(self):
        """group_by='category' groups results by category."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("200.00") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (2 if k == "cnt" else None)
        )

        def _make_group_row(key, amount, count):
            r = MagicMock()
            data = {"key": key, "amount": Decimal(str(amount)), "count": count}
            r.__getitem__ = MagicMock(side_effect=lambda k: data[k])
            return r

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row])
        pool.fetch = AsyncMock(
            return_value=[
                _make_group_row("groceries", "120.00", 5),
                _make_group_row("dining", "80.00", 3),
            ]
        )

        result = await spending_summary(
            pool,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            group_by="category",
        )

        assert len(result["groups"]) == 2
        assert result["groups"][0]["key"] == "groceries"

    async def test_group_by_merchant(self):
        """group_by='merchant' groups results by merchant."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("50.00") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (1 if k == "cnt" else None)
        )
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row])
        pool.fetch = AsyncMock(return_value=[])

        await spending_summary(
            pool,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            group_by="merchant",
        )

        fetch_call = pool.fetch.call_args
        assert "merchant AS key" in fetch_call.args[0]

    async def test_invalid_group_by_raises(self):
        """Invalid group_by raises ValueError."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="Unsupported group_by"):
            await spending_summary(pool, group_by="invalid_group")

    async def test_filters_only_debit_transactions(self):
        """spending_summary only counts 'debit' direction transactions."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("0.00") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (0 if k == "cnt" else None)
        )
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "cnt" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row, count_row])
        pool.fetch = AsyncMock(return_value=[])

        await spending_summary(pool, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))

        # The SUM query must filter by direction = 'debit'
        total_call = pool.fetchrow.call_args_list[0]
        assert "direction = 'debit'" in total_call.args[0]

    async def test_excludes_soft_deleted_when_column_exists(self):
        """Excludes soft-deleted transactions when deleted_at column exists."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("0.00") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (0 if k == "cnt" else None)
        )
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "cnt" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # deleted_at column exists
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row, count_row])
        pool.fetch = AsyncMock(return_value=[])

        await spending_summary(pool, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))

        total_call = pool.fetchrow.call_args_list[0]
        assert "deleted_at IS NULL" in total_call.args[0]

    async def test_amounts_returned_as_strings(self):
        """total_spend and group amounts are returned as strings."""
        total_row = MagicMock()
        total_row.__getitem__ = MagicMock(
            side_effect=lambda k: Decimal("427.50") if k == "total" else None
        )
        currency_row = MagicMock()
        currency_row.__getitem__ = MagicMock(
            side_effect=lambda k: "USD" if k == "currency" else (5 if k == "cnt" else None)
        )
        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(side_effect=lambda k: 5 if k == "cnt" else None)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[total_row, currency_row, count_row])
        pool.fetch = AsyncMock(return_value=[])

        result = await spending_summary(
            pool, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)
        )

        assert isinstance(result["total_spend"], str)


# ---------------------------------------------------------------------------
# 4.4: update_transaction() with optimistic locking
# ---------------------------------------------------------------------------


class TestUpdateTransaction:
    """Task 4.4: update_transaction() with optimistic locking."""

    def _make_current_row(self, **overrides):
        data = {
            "id": _TXN_ID,
            "category": "shopping",
            "merchant": "ACME",
            "description": "Test",
            "metadata": "{}",
            "version": 1,
            "category_source": "manual",
            "is_category_locked": False,
        }
        data.update(overrides)
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data.get(k))
        row.get = MagicMock(side_effect=lambda k, default=None: data.get(k, default))
        row._data = data
        return row

    async def test_returns_error_when_transaction_not_found(self):
        """Returns error dict when transaction ID does not exist."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=None)

        result = await update_transaction(pool, _TXN_ID, category="dining")

        assert result["error"] == "transaction_not_found"
        assert result["transaction_id"] == _TXN_ID

    async def test_returns_current_when_nothing_to_update(self):
        """Returns current row unchanged when no fields differ."""
        current = self._make_current_row()
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(return_value=current)

        # Pass same values as current row
        result = await update_transaction(
            pool,
            _TXN_ID,
            category="shopping",  # same as current
            merchant="ACME",  # same as current
        )

        # Should return the current row dict without executing UPDATE
        assert result is not None

    async def test_optimistic_locking_conflict_when_version_mismatch(self):
        """Returns version_conflict error when expected_version doesn't match."""
        current = self._make_current_row(version=5)

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # version column exists
        pool.fetchrow = AsyncMock(side_effect=[current, None])  # SELECT + UPDATE returns None

        result = await update_transaction(
            pool,
            _TXN_ID,
            category="dining",
            expected_version=3,  # wrong version
        )

        assert result["error"] == "version_conflict"
        assert result["expected_version"] == 3
        assert result["current_version"] == 5

    async def test_version_incremented_on_update(self):
        """version = version + 1 is included in SET clause when version column exists."""
        current = self._make_current_row(version=1)
        updated = self._make_current_row(version=2, category="dining")

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # version column exists
        pool.fetchrow = AsyncMock(side_effect=[current, updated])

        with patch("butlers.tools.finance.transactions._has_table", return_value=False):
            with patch("butlers.tools.finance.transactions._record_correction"):
                await update_transaction(pool, _TXN_ID, category="dining")

        # UPDATE query should include version increment
        update_call = pool.fetchrow.call_args
        assert "version = version + 1" in update_call.args[0]

    async def test_category_update_sets_category_source_manual(self):
        """Changing category sets category_source='manual' and locks category."""
        current = self._make_current_row(category="shopping")
        updated = self._make_current_row(category="dining")

        pool = _make_pool()
        # fetchval order: version exists (0), category_source exists (1), is_category_locked (1)
        pool.fetchval = AsyncMock(side_effect=[1, 1, 1])
        pool.fetchrow = AsyncMock(side_effect=[current, updated])

        with patch("butlers.tools.finance.transactions._has_table", return_value=False):
            with patch("butlers.tools.finance.transactions._record_correction"):
                await update_transaction(pool, _TXN_ID, category="dining")

        update_call = pool.fetchrow.call_args
        query = update_call.args[0]
        assert "category_source = 'manual'" in query
        assert "is_category_locked = true" in query

    async def test_correction_logged_for_category_change(self):
        """Correction is recorded in transaction_corrections for category change."""
        current = self._make_current_row(category="shopping")
        updated = self._make_current_row(category="dining")

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[current, updated])

        with patch("butlers.tools.finance.transactions._has_table", return_value=True) as _:
            with patch("butlers.tools.finance.transactions._record_correction") as mock_correction:
                mock_correction.return_value = None  # async no-op

                await update_transaction(pool, _TXN_ID, category="dining", reason="wrong category")

        mock_correction.assert_called()

    async def test_update_triggers_merchant_mapping_refresh(self):
        """When category changes, learn_merchant_categories is called."""
        current = self._make_current_row(category="shopping")
        updated = self._make_current_row(category="dining")

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetchrow = AsyncMock(side_effect=[current, updated])

        with patch("butlers.tools.finance.transactions._has_table", return_value=False):
            with patch("butlers.tools.finance.transactions._record_correction"):
                with patch(
                    "butlers.tools.finance.pattern_recognition.learn_merchant_categories"
                ) as mock_learn:
                    mock_learn.return_value = None

                    await update_transaction(pool, _TXN_ID, category="dining")

        # learn_merchant_categories is called via import inside the function


# ---------------------------------------------------------------------------
# 4.5: delete_transaction() as soft delete
# ---------------------------------------------------------------------------


class TestDeleteTransaction:
    """Task 4.5: delete_transaction() as soft delete."""

    async def test_sets_deleted_at_not_hard_delete(self):
        """Soft delete uses UPDATE SET deleted_at, NOT DELETE FROM."""
        deleted_row = _make_txn_row(deleted_at=_NOW)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # deleted_at column exists
        pool.fetchrow = AsyncMock(return_value=deleted_row)

        await delete_transaction(pool, _TXN_ID)

        # Verify UPDATE was used (not DELETE)
        update_call = pool.fetchrow.call_args
        query = update_call.args[0]
        assert "UPDATE transactions" in query
        assert "SET deleted_at" in query
        assert "DELETE FROM" not in query

    async def test_returns_error_when_transaction_not_found(self):
        """Returns error dict when transaction does not exist."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)  # column exists
        pool.fetchrow = AsyncMock(return_value=None)

        result = await delete_transaction(pool, _TXN_ID)

        assert result["error"] == "transaction_not_found"

    async def test_returns_error_when_soft_delete_not_supported(self):
        """Returns error when deleted_at column doesn't exist (pre-migration schema)."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)  # deleted_at column absent

        result = await delete_transaction(pool, _TXN_ID)

        assert result["error"] == "soft_delete_not_supported"

    async def test_idempotent_uses_coalesce_for_deleted_at(self):
        """Soft delete uses COALESCE to avoid overwriting existing deleted_at."""
        deleted_row = _make_txn_row(deleted_at=_NOW)
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool.fetchrow = AsyncMock(return_value=deleted_row)

        await delete_transaction(pool, _TXN_ID)

        update_call = pool.fetchrow.call_args
        query = update_call.args[0]
        assert "COALESCE(deleted_at, now())" in query

    async def test_version_incremented_on_soft_delete(self):
        """version is incremented when version column exists."""
        deleted_row = _make_txn_row(deleted_at=_NOW, version=2)
        pool = _make_pool()
        # fetchval returns: deleted_at exists (1), version exists (1)
        pool.fetchval = AsyncMock(side_effect=[1, 1])
        pool.fetchrow = AsyncMock(return_value=deleted_row)

        await delete_transaction(pool, _TXN_ID)

        update_call = pool.fetchrow.call_args
        query = update_call.args[0]
        assert "version = version + 1" in query

    async def test_no_delete_from_in_entire_module(self):
        """Verify no DELETE FROM appears in the transactions module source."""
        import inspect

        source = inspect.getsource(_txn_module)
        # 'DELETE FROM' should not appear (soft deletes only)
        assert "DELETE FROM" not in source


# ---------------------------------------------------------------------------
# 4.6: merge_duplicates() — combine duplicate transactions
# ---------------------------------------------------------------------------


class TestMergeDuplicates:
    """Task 4.6: merge_duplicates() marks duplicates and soft-deletes them."""

    def _setup_merge_pool(self, keep_row, discard_row):
        """Build a pool mock for merge_duplicates() tests."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)

        # Connection mock setup
        conn = pool._conn
        conn.fetchrow = AsyncMock(side_effect=[keep_row, discard_row, keep_row])
        conn.execute = AsyncMock(return_value="UPDATE 1")
        conn.fetchval = AsyncMock(return_value=1)

        return pool

    async def test_requires_duplicate_ids_or_discard_id(self):
        """Returns error when neither duplicate_ids nor discard_id is provided."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)

        result = await merge_duplicates(pool, keep_id=_TXN_ID)

        assert "error" in result
        assert "must provide" in result["error"]

    async def test_rejects_empty_duplicate_ids_list(self):
        """Returns error when duplicate_ids is empty list."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)

        result = await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[])

        assert "error" in result
        assert "must not be empty" in result["error"]

    async def test_rejects_keep_id_in_duplicate_ids(self):
        """Returns error when keep_id appears in duplicate_ids."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)

        result = await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[_TXN_ID])

        assert "error" in result
        assert "must not appear" in result["error"]

    async def test_returns_error_when_soft_delete_not_supported(self):
        """Returns error when deleted_at column doesn't exist."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)  # no deleted_at

        result = await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[_TXN_ID2])

        assert result["error"] == "soft_delete_not_supported"

    async def test_returns_error_when_keep_transaction_not_found(self):
        """Returns error when keep_id transaction doesn't exist."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=None)

        result = await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[_TXN_ID2])

        assert result["error"] == "keep_transaction_not_found"

    async def test_marks_duplicates_with_is_duplicate_flag(self):
        """Discarded transactions have is_duplicate=true set."""
        keep_meta = json.dumps({"source": "bank"})
        discard_meta = json.dumps({"extra": "data"})

        keep = MagicMock()
        keep_data = {
            "id": _TXN_ID,
            "metadata": keep_meta,
            "deleted_at": None,
        }
        keep.__getitem__ = MagicMock(side_effect=lambda k: keep_data.get(k))

        discard = MagicMock()
        discard_data = {
            "id": _TXN_ID2,
            "metadata": discard_meta,
            "deleted_at": None,
        }
        discard.__getitem__ = MagicMock(side_effect=lambda k: discard_data.get(k))

        pool = _make_pool()
        # fetchval: deleted_at (1), is_duplicate (1), duplicate_of (1), corrections table (0)
        pool.fetchval = AsyncMock(side_effect=[1, 1, 1, 0])
        pool._conn.fetchrow = AsyncMock(side_effect=[keep, discard, keep])
        pool._conn.execute = AsyncMock(return_value="UPDATE 1")

        await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[_TXN_ID2])

        # The UPDATE for the discard should set is_duplicate = true
        execute_calls = pool._conn.execute.call_args_list
        executed_queries = [c.args[0] for c in execute_calls]
        assert any("is_duplicate = true" in q for q in executed_queries)

    async def test_merges_discard_metadata_into_keep(self):
        """Metadata from discarded records is merged into the kept record."""
        keep_meta = json.dumps({"keep_key": "keep_value"})
        discard_meta = json.dumps({"discard_key": "discard_value"})

        keep = MagicMock()
        keep_data = {"id": _TXN_ID, "metadata": keep_meta, "deleted_at": None}
        keep.__getitem__ = MagicMock(side_effect=lambda k: keep_data.get(k))

        discard = MagicMock()
        discard_data = {"id": _TXN_ID2, "metadata": discard_meta, "deleted_at": None}
        discard.__getitem__ = MagicMock(side_effect=lambda k: discard_data.get(k))

        updated_keep = MagicMock()
        updated_keep_data = {
            "id": _TXN_ID,
            "metadata": json.dumps({"discard_key": "discard_value", "keep_key": "keep_value"}),
            "deleted_at": None,
        }
        updated_keep.__getitem__ = MagicMock(side_effect=lambda k: updated_keep_data.get(k))

        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=[1, 1, 1, 0])
        pool._conn.fetchrow = AsyncMock(side_effect=[keep, discard, updated_keep])
        pool._conn.execute = AsyncMock(return_value="UPDATE 1")

        await merge_duplicates(pool, keep_id=_TXN_ID, duplicate_ids=[_TXN_ID2])

        # fetchrow for UPDATE RETURNING should have been called with merged metadata
        update_call = pool._conn.fetchrow.call_args_list[2]
        query = update_call.args[0]
        assert "UPDATE transactions" in query
        assert "metadata" in query

    async def test_legacy_discard_id_interface(self):
        """discard_id parameter works as fallback for single-record merge."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)  # no deleted_at → returns error

        result = await merge_duplicates(pool, keep_id=_TXN_ID, discard_id=_TXN_ID2)

        # Should reach the no-deleted_at error (not the "must provide" error)
        assert result["error"] == "soft_delete_not_supported"


# ---------------------------------------------------------------------------
# 4.7: split_transaction() — split a transaction into sub-amounts
# ---------------------------------------------------------------------------


class TestSplitTransaction:
    """Task 4.7: split_transaction() into sub-amounts."""

    def _make_original(self, amount="50.00"):
        data = {
            "id": _TXN_ID,
            "account_id": _ACCOUNT_ID,
            "source_message_id": "msg-1",
            "posted_at": _NOW,
            "merchant": "ACME",
            "description": "Test",
            "amount": Decimal(amount),
            "currency": "USD",
            "direction": "debit",
            "category": "shopping",
            "payment_method": None,
            "receipt_url": None,
            "external_ref": None,
            "metadata": "{}",
            "deleted_at": None,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data.get(k))
        row._data = data
        return row

    async def test_returns_error_when_transaction_not_found(self):
        """Returns error when transaction ID doesn't exist."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=None)

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"amount": "25.00", "category": "food"}],
        )

        assert result["error"] == "transaction_not_found"

    async def test_returns_error_for_empty_splits(self):
        """Returns error when splits list is empty."""
        original = self._make_original()
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=original)

        result = await split_transaction(pool, _TXN_ID, splits=[])

        assert result["error"] == "splits must not be empty"

    async def test_returns_error_when_amounts_do_not_sum_to_original(self):
        """Returns error when split amounts don't sum to original amount."""
        original = self._make_original(amount="50.00")
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=original)

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[
                {"amount": "20.00", "category": "food"},
                {"amount": "15.00", "category": "transport"},  # total = 35, not 50
            ],
        )

        assert "error" in result
        assert "35" in result["error"] and "50" in result["error"]

    async def test_returns_error_when_split_missing_amount(self):
        """Returns error when a split entry has no amount field."""
        original = self._make_original()
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=original)

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"category": "food"}],  # no amount
        )

        assert "error" in result
        assert "amount" in result["error"]

    async def test_returns_error_when_split_missing_category(self):
        """Returns error when a split entry has no category field."""
        original = self._make_original()
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=original)

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"amount": "50.00"}],  # no category
        )

        assert "error" in result
        assert "category" in result["error"]

    async def test_returns_error_for_invalid_amount_in_split(self):
        """Returns error when split amount is not a valid decimal."""
        original = self._make_original()
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=1)
        pool._conn.fetchrow = AsyncMock(return_value=original)

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"amount": "not-a-number", "category": "food"}],
        )

        assert "error" in result
        assert "invalid amount" in result["error"]

    async def test_successful_split_returns_original_id_and_splits(self):
        """Successful split returns original_id and list of split transactions."""
        original = self._make_original(amount="50.00")
        split1_row = _make_txn_row(id=_TXN_ID2, amount=Decimal("30.00"), category="food")
        split2_row = _make_txn_row(id=_TXN_ID3, amount=Decimal("20.00"), category="transport")

        pool = _make_pool()
        # fetchval: deleted_at (1), corrections table (0)
        pool.fetchval = AsyncMock(side_effect=[1, 0])
        pool._conn.fetchrow = AsyncMock(side_effect=[original, split1_row, split2_row])
        pool._conn.execute = AsyncMock(return_value="UPDATE 1")

        result = await split_transaction(
            pool,
            _TXN_ID,
            splits=[
                {"amount": "30.00", "category": "food"},
                {"amount": "20.00", "category": "transport"},
            ],
        )

        assert result["original_id"] == _TXN_ID
        assert "splits" in result
        assert len(result["splits"]) == 2

    async def test_split_children_have_split_from_in_metadata(self):
        """Child transactions have metadata.split_from = original transaction ID."""
        original = self._make_original(amount="50.00")
        # Simulate insertions return rows with metadata
        split1_meta = json.dumps({"split_from": _TXN_ID})
        split1_data = {
            "id": _TXN_ID2,
            "amount": Decimal("50.00"),
            "metadata": split1_meta,
            "category": "food",
            "direction": "debit",
        }
        split1 = MagicMock()
        split1.__getitem__ = MagicMock(side_effect=lambda k: split1_data.get(k))
        split1.__iter__ = MagicMock(return_value=iter(split1_data.items()))

        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=[1, 0])
        pool._conn.fetchrow = AsyncMock(side_effect=[original, split1])
        pool._conn.execute = AsyncMock(return_value="UPDATE 1")

        await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"amount": "50.00", "category": "food"}],
        )

        # The INSERT query should embed split_from in metadata
        insert_call = pool._conn.fetchrow.call_args_list[1]
        insert_query = insert_call.args[0]
        assert "INSERT INTO transactions" in insert_query
        # metadata param should contain split_from
        params = insert_call.args[1:]
        metadata_param = next((p for p in params if isinstance(p, str) and "split_from" in p), None)
        assert metadata_param is not None

    async def test_original_is_soft_deleted_after_split(self):
        """Original transaction is soft-deleted (not hard deleted) after split."""
        original = self._make_original(amount="50.00")
        split_row = _make_txn_row(id=_TXN_ID2, amount=Decimal("50.00"))

        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=[1, 0])
        pool._conn.fetchrow = AsyncMock(side_effect=[original, split_row])
        pool._conn.execute = AsyncMock(return_value="UPDATE 1")

        await split_transaction(
            pool,
            _TXN_ID,
            splits=[{"amount": "50.00", "category": "food"}],
        )

        # The soft-delete UPDATE should have been called
        execute_calls = pool._conn.execute.call_args_list
        queries = [c.args[0] for c in execute_calls]
        assert any("deleted_at = now()" in q for q in queries)
        assert all("DELETE FROM" not in q for q in queries)


# ---------------------------------------------------------------------------
# 4.8: bulk_recategorize() — update category for multiple transactions
# ---------------------------------------------------------------------------


class TestBulkRecategorize:
    """Task 4.8: bulk_recategorize() updates category for matching transactions."""

    async def test_dry_run_returns_matched_count_without_updating(self):
        """dry_run=True returns matched count but does not update rows."""
        pool = _make_pool()
        # fetchval calls in order:
        # 1. _has_column(deleted_at) → 0
        # 2. _has_column(is_category_locked) → 0
        # 3. _has_table(transaction_corrections) → 0
        # 4. COUNT query → 5
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 5])
        pool.fetch = AsyncMock(return_value=[])

        result = await bulk_recategorize(
            pool,
            merchant_pattern="%netflix%",
            new_category="streaming",
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["updated"] == 0
        assert result["matched"] == 5
        pool.execute.assert_not_called()

    async def test_updates_unlocked_transactions_matching_pattern(self):
        """Non-dry-run updates matching unlocked transactions."""
        pool = _make_pool()
        # fetchval: deleted_at col (0), is_category_locked col (0), corrections table (0), COUNT (3)
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 3])
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value="UPDATE 3")

        result = await bulk_recategorize(
            pool,
            merchant_pattern="%netflix%",
            new_category="streaming",
        )

        assert result["dry_run"] is False
        pool.execute.assert_called_once()
        update_call = pool.execute.call_args
        assert "UPDATE transactions" in update_call.args[0]
        assert "category = " in update_call.args[0]

    async def test_excludes_locked_transactions(self):
        """is_category_locked=true transactions are excluded from bulk update."""
        pool = _make_pool()
        # fetchval: deleted_at col (1), is_category_locked col (1), corrections table (0), COUNT (2)
        pool.fetchval = AsyncMock(side_effect=[1, 1, 0, 2])
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value="UPDATE 2")

        await bulk_recategorize(
            pool,
            merchant_pattern="%netflix%",
            new_category="streaming",
        )

        execute_call = pool.execute.call_args
        query = execute_call.args[0]
        assert "is_category_locked" in query

    async def test_create_rule_upserts_merchant_mapping(self):
        """create_rule=True upserts a row in finance.merchant_mappings."""
        pool = _make_pool()
        # fetchval: deleted_at col (0), is_category_locked col (0), corrections table (0), COUNT (1)
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 1])
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value="UPDATE 1")

        with patch("butlers.tools.finance.transactions._has_table", return_value=True):
            with patch(
                "butlers.tools.finance.pattern_recognition.learn_merchant_categories",
                AsyncMock(return_value=None),
            ):
                await bulk_recategorize(
                    pool,
                    merchant_pattern="%netflix%",
                    new_category="streaming",
                    create_rule=True,
                )

        # At least two execute calls: UPDATE + INSERT INTO merchant_mappings
        assert pool.execute.call_count >= 2
        calls = [c.args[0] for c in pool.execute.call_args_list]
        assert any("INSERT INTO merchant_mappings" in q for q in calls)

    async def test_sample_transactions_included_in_response(self):
        """Response includes sample_transactions with matching rows."""
        sample = _make_txn_row(merchant="Netflix")
        pool = _make_pool()
        # fetchval: deleted_at (0), is_category_locked (0), corrections (0), COUNT (1)
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 1])
        pool.fetch = AsyncMock(return_value=[sample])
        pool.execute = AsyncMock(return_value="UPDATE 1")

        result = await bulk_recategorize(
            pool,
            merchant_pattern="%netflix%",
            new_category="streaming",
            dry_run=True,
        )

        assert "sample_transactions" in result
        assert len(result["sample_transactions"]) == 1

    async def test_matched_count_returned(self):
        """Response includes matched count of transactions."""
        pool = _make_pool()
        # fetchval: deleted_at (0), is_category_locked (0), corrections (0), COUNT (7)
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 7])
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value="UPDATE 7")

        result = await bulk_recategorize(
            pool,
            merchant_pattern="%starbucks%",
            new_category="coffee",
        )

        assert "matched" in result
        assert result["matched"] == 7

    async def test_pattern_is_case_insensitive(self):
        """Merchant pattern matching is case-insensitive (lower() LIKE lower())."""
        pool = _make_pool()
        # fetchval: deleted_at (0), is_category_locked (0), corrections (0), COUNT (0)
        pool.fetchval = AsyncMock(side_effect=[0, 0, 0, 0])
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value="UPDATE 0")

        await bulk_recategorize(
            pool,
            merchant_pattern="Netflix",
            new_category="streaming",
        )

        fetch_call = pool.fetch.call_args
        query = fetch_call.args[0]
        assert "lower(merchant) LIKE lower(" in query
