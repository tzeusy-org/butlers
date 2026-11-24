"""Tests for finance butler transaction CRUD extension tools.

Covers: update_transaction, delete_transaction, merge_duplicates,
        split_transaction, bulk_recategorize — introduced in bu-raub.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Categories these tests record under that the finance_006 seed lacks. ``misc`` and
# ``not-a-real-category`` stay unseeded on purpose: they exercise the unknown-category
# fallback onto ``uncategorized`` that the real transactions.category FK forces.
_TEST_CATEGORIES = (
    "a",
    "archived",
    "b",
    "bills",
    "bulk_override",
    "business",
    "cashback",
    "electronics",
    "manual_cat",
    "original",
    "other",
    "refund",
    "services",
    "test",
)


@pytest.fixture
async def finance_pool(db):
    """Core+finance clone; seeds the non-system categories these tests record under."""
    pool = await db("core", "finance")
    await pool.executemany(
        "INSERT INTO categories (name, display_name) VALUES ($1, $1) ON CONFLICT DO NOTHING",
        [(name,) for name in _TEST_CATEGORIES],
    )
    return pool


async def _insert_txn(
    pool,
    merchant="Test Co",
    amount=-10.00,
    category="shopping",
    description=None,
    metadata=None,
    expected_signal_source=None,
    account_id=None,
    external_id=None,
    source_message_id=None,
) -> dict:
    """Helper: insert a transaction and return its dict."""
    from butlers.tools.finance.transactions import record_transaction

    return await record_transaction(
        pool=pool,
        posted_at=_utcnow(),
        merchant=merchant,
        amount=amount,
        currency="USD",
        category=category,
        description=description,
        metadata=metadata,
        account_id=account_id,
        external_id=external_id,
        source_message_id=source_message_id,
        _expected_signal_source=expected_signal_source,
    )


# ---------------------------------------------------------------------------
# update_transaction
# ---------------------------------------------------------------------------


class TestUpdateTransaction:
    """Tests for update_transaction."""

    async def test_update_category(self, finance_pool):
        """update_transaction changes the category field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, merchant="Amazon", category="shopping")
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="electronics",
        )
        assert result["category"] == "electronics"
        assert result["id"] == txn["id"]

    async def test_update_merchant(self, finance_pool):
        """update_transaction changes the merchant field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, merchant="Old Name")
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            merchant="New Name",
        )
        assert result["merchant"] == "New Name"

    async def test_update_description(self, finance_pool):
        """update_transaction changes the description field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, description=None)
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            description="Updated description",
        )
        assert result["description"] == "Updated description"

    async def test_update_metadata(self, finance_pool):
        """update_transaction replaces the metadata field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, metadata={"old_key": "old_val"})
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            metadata={"new_key": "new_val"},
        )
        assert result["metadata"] == {"new_key": "new_val"}

    async def test_update_metadata_cannot_replace_server_attestation(self, finance_pool):
        from butlers.tools.finance.expected_signals import FinanceSignalSource
        from butlers.tools.finance.transactions import update_transaction

        source = FinanceSignalSource("connector:gmail", "gmail:user:owner@example.invalid")
        txn = await _insert_txn(finance_pool, expected_signal_source=source)
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            metadata={
                "note": "updated",
                "expected_signal_source": {
                    "producer": "connector:gmail",
                    "producer_endpoint_identity": "gmail:user:forged",
                },
            },
        )
        assert result["metadata"]["note"] == "updated"
        assert result["metadata"]["expected_signal_source"] == source.as_metadata()

    async def test_update_no_fields_returns_current(self, finance_pool):
        """update_transaction with no fields returns the current record unchanged."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, category="groceries")
        result = await update_transaction(pool=finance_pool, transaction_id=txn["id"])
        assert result["id"] == txn["id"]
        assert result["category"] == "groceries"

    async def test_update_not_found_returns_error(self, finance_pool):
        """update_transaction with unknown ID returns error dict."""
        import uuid

        from butlers.tools.finance.transactions import update_transaction

        fake_id = str(uuid.uuid4())
        result = await update_transaction(
            pool=finance_pool, transaction_id=fake_id, category="dining"
        )
        assert result["error"] == "transaction_not_found"
        assert result["transaction_id"] == fake_id


# ---------------------------------------------------------------------------
# delete_transaction
# ---------------------------------------------------------------------------


class TestDeleteTransaction:
    """Tests for delete_transaction."""

    async def test_delete_sets_deleted_at(self, finance_pool):
        """delete_transaction sets deleted_at on the record."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn(finance_pool)
        result = await delete_transaction(pool=finance_pool, transaction_id=txn["id"])
        assert result["id"] == txn["id"]
        assert result["deleted_at"] is not None

    async def test_delete_excluded_from_list(self, finance_pool):
        """Soft-deleted transactions are excluded from list_transactions."""
        from butlers.tools.finance.transactions import delete_transaction, list_transactions

        txn = await _insert_txn(finance_pool, merchant="Deleted Corp")
        await delete_transaction(pool=finance_pool, transaction_id=txn["id"])

        result = await list_transactions(pool=finance_pool, merchant="Deleted Corp")
        assert result["total"] == 0

    async def test_delete_is_idempotent(self, finance_pool):
        """Calling delete_transaction twice does not raise and returns the record."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn(finance_pool)
        first = await delete_transaction(pool=finance_pool, transaction_id=txn["id"])
        second = await delete_transaction(pool=finance_pool, transaction_id=txn["id"])
        # deleted_at should be the same (COALESCE preserves original timestamp)
        assert first["deleted_at"] == second["deleted_at"]

    async def test_delete_not_found(self, finance_pool):
        """delete_transaction with unknown ID returns error dict."""
        import uuid

        from butlers.tools.finance.transactions import delete_transaction

        fake_id = str(uuid.uuid4())
        result = await delete_transaction(pool=finance_pool, transaction_id=fake_id)
        assert result["error"] == "transaction_not_found"


# ---------------------------------------------------------------------------
# merge_duplicates
# ---------------------------------------------------------------------------


class TestMergeDuplicates:
    """Tests for merge_duplicates."""

    async def test_merge_soft_deletes_discard(self, finance_pool):
        """merge_duplicates soft-deletes the discard transaction."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, merchant="Netflix")
        discard = await _insert_txn(finance_pool, merchant="Netflix")

        await merge_duplicates(pool=finance_pool, keep_id=keep["id"], duplicate_ids=[discard["id"]])

        # Check discard has deleted_at set
        row = await finance_pool.fetchrow(
            "SELECT deleted_at FROM transactions WHERE id = $1::uuid",
            discard["id"],
        )
        assert row["deleted_at"] is not None

    async def test_merge_keeps_keep_record(self, finance_pool):
        """merge_duplicates returns the kept record with merged metadata."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, metadata={"source": "keep"})
        discard = await _insert_txn(finance_pool, metadata={"extra": "from_discard"})

        result = await merge_duplicates(
            pool=finance_pool, keep_id=keep["id"], duplicate_ids=[discard["id"]]
        )
        assert result["id"] == keep["id"]
        # keep's metadata is preserved; discard's non-conflicting keys are merged in
        assert result["metadata"]["source"] == "keep"
        assert result["metadata"]["extra"] == "from_discard"

    async def test_merge_keep_wins_on_metadata_conflict(self, finance_pool):
        """Keep's metadata values win when keys conflict."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, metadata={"key": "keep_val"})
        discard = await _insert_txn(finance_pool, metadata={"key": "discard_val"})

        result = await merge_duplicates(
            pool=finance_pool, keep_id=keep["id"], duplicate_ids=[discard["id"]]
        )
        assert result["metadata"]["key"] == "keep_val"

    async def test_merge_preserves_only_corroborated_server_authority(self, finance_pool):
        from butlers.tools.finance.expected_signals import FinanceSignalSource
        from butlers.tools.finance.transactions import merge_duplicates

        source = FinanceSignalSource("connector:gmail", "gmail:user:owner@example.invalid")
        keep = await _insert_txn(finance_pool, expected_signal_source=source)
        corroborating = await _insert_txn(finance_pool, expected_signal_source=source)
        result = await merge_duplicates(
            pool=finance_pool, keep_id=keep["id"], duplicate_ids=[corroborating["id"]]
        )
        assert result["metadata"]["expected_signal_source"] == source.as_metadata()

        unknown = await _insert_txn(finance_pool)
        result = await merge_duplicates(
            pool=finance_pool, keep_id=keep["id"], duplicate_ids=[unknown["id"]]
        )
        assert "expected_signal_source" not in result["metadata"]

    async def test_merge_same_id_returns_error(self, finance_pool):
        """merge_duplicates with keep_id appearing in duplicate_ids returns error."""
        from butlers.tools.finance.transactions import merge_duplicates

        txn = await _insert_txn(finance_pool)
        result = await merge_duplicates(
            pool=finance_pool, keep_id=txn["id"], duplicate_ids=[txn["id"]]
        )
        assert "error" in result

    async def test_merge_keep_not_found(self, finance_pool):
        """merge_duplicates with unknown keep_id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        discard = await _insert_txn(finance_pool)
        result = await merge_duplicates(
            pool=finance_pool,
            keep_id=str(uuid.uuid4()),
            duplicate_ids=[discard["id"]],
        )
        assert result["error"] == "keep_transaction_not_found"

    async def test_merge_discard_not_found(self, finance_pool):
        """merge_duplicates with unknown duplicate id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool)
        result = await merge_duplicates(
            pool=finance_pool,
            keep_id=keep["id"],
            duplicate_ids=[str(uuid.uuid4())],
        )
        assert result["error"] == "discard_transaction_not_found"


# ---------------------------------------------------------------------------
# split_transaction
# ---------------------------------------------------------------------------


class TestSplitTransaction:
    """Tests for split_transaction."""

    async def test_split_creates_new_records(self, finance_pool):
        """split_transaction creates the requested number of split records."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00, category="shopping")
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "groceries"},
                {"amount": "40.00", "category": "dining"},
            ],
        )
        assert "splits" in result
        assert len(result["splits"]) == 2
        assert result["original_id"] == txn["id"]

    async def test_split_preserves_server_attested_provenance(self, finance_pool):
        from butlers.tools.finance.expected_signals import FinanceSignalSource
        from butlers.tools.finance.transactions import split_transaction

        source = FinanceSignalSource("connector:gmail", "gmail:user:owner@example.invalid")
        txn = await _insert_txn(finance_pool, amount=-100, expected_signal_source=source)
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60", "category": "groceries"},
                {"amount": "40", "category": "dining"},
            ],
        )
        assert all(
            row["metadata"]["expected_signal_source"] == source.as_metadata()
            for row in result["splits"]
        )

    async def test_split_soft_deletes_original(self, finance_pool):
        """split_transaction soft-deletes the original record."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-50.00)
        await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "30.00", "category": "a"},
                {"amount": "20.00", "category": "b"},
            ],
        )
        row = await finance_pool.fetchrow(
            "SELECT deleted_at FROM transactions WHERE id = $1::uuid",
            txn["id"],
        )
        assert row["deleted_at"] is not None

    async def test_split_amounts_assigned_correctly(self, finance_pool):
        """Split records have the correct amounts and categories."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00)
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "70.00", "category": "groceries"},
                {"amount": "30.00", "category": "dining"},
            ],
        )
        amounts = {Decimal(str(s["amount"])) for s in result["splits"]}
        categories = {s["category"] for s in result["splits"]}
        assert amounts == {Decimal("70.00"), Decimal("30.00")}
        assert categories == {"groceries", "dining"}

    async def test_split_mismatched_amounts_returns_error(self, finance_pool):
        """split_transaction returns error when split amounts do not sum to original."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00)
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "a"},
                {"amount": "30.00", "category": "b"},  # 60+30=90 != 100
            ],
        )
        assert "error" in result
        assert result["transaction_id"] == txn["id"]

    async def test_split_empty_splits_returns_error(self, finance_pool):
        """split_transaction with empty splits list returns error."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-50.00)
        result = await split_transaction(pool=finance_pool, transaction_id=txn["id"], splits=[])
        assert "error" in result

    async def test_split_missing_category_returns_error(self, finance_pool):
        """split_transaction returns error when a split is missing category."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-50.00)
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[{"amount": "50.00"}],  # no category
        )
        assert "error" in result

    async def test_split_not_found_returns_error(self, finance_pool):
        """split_transaction on unknown ID returns error."""
        import uuid

        from butlers.tools.finance.transactions import split_transaction

        fake_id = str(uuid.uuid4())
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=fake_id,
            splits=[{"amount": "10.00", "category": "a"}],
        )
        assert result["error"] == "transaction_not_found"

    async def test_split_description_override(self, finance_pool):
        """Split records accept per-split description overrides."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00, description="original desc")
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "a", "description": "part one"},
                {"amount": "40.00", "category": "b"},
            ],
        )
        descriptions = {s["description"] for s in result["splits"]}
        assert "part one" in descriptions


# ---------------------------------------------------------------------------
# bulk_recategorize
# ---------------------------------------------------------------------------


class TestBulkRecategorize:
    """Tests for bulk_recategorize."""

    async def test_bulk_recategorize_updates_matching(self, finance_pool):
        """bulk_recategorize updates category for all matching transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize

        for _ in range(3):
            await _insert_txn(finance_pool, merchant="Netflix", category="entertainment")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%Netflix%",
            new_category="subscriptions",
        )
        assert result["matched"] == 3
        assert result["updated"] == 3
        assert result["dry_run"] is False

        # Verify DB
        count = await finance_pool.fetchval(
            "SELECT COUNT(*) FROM transactions"
            " WHERE merchant = 'Netflix' AND category = 'subscriptions'"
        )
        assert count == 3

    async def test_bulk_recategorize_dry_run_no_changes(self, finance_pool):
        """bulk_recategorize dry_run=True does not modify records."""
        from butlers.tools.finance.transactions import bulk_recategorize

        await _insert_txn(finance_pool, merchant="Spotify", category="entertainment")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%Spotify%",
            new_category="subscriptions",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["matched"] == 1
        assert result["updated"] == 0

        # Verify no DB change
        cat = await finance_pool.fetchval(
            "SELECT category FROM transactions WHERE merchant = 'Spotify'"
        )
        assert cat == "entertainment"

    async def test_bulk_recategorize_excludes_deleted(self, finance_pool):
        """bulk_recategorize does not update soft-deleted transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize, delete_transaction

        txn = await _insert_txn(finance_pool, merchant="OldCo", category="shopping")
        await delete_transaction(pool=finance_pool, transaction_id=txn["id"])

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%OldCo%",
            new_category="archived",
        )
        assert result["matched"] == 0
        assert result["updated"] == 0

    async def test_bulk_recategorize_no_match(self, finance_pool):
        """bulk_recategorize returns matched=0 when pattern matches nothing."""
        from butlers.tools.finance.transactions import bulk_recategorize

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%NoSuchMerchant%",
            new_category="other",
        )
        assert result["matched"] == 0
        assert result["updated"] == 0

    async def test_bulk_recategorize_sample_transactions(self, finance_pool):
        """bulk_recategorize returns up to 10 sample_transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize

        for i in range(5):
            await _insert_txn(finance_pool, merchant=f"SampleCo {i}", category="shopping")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%SampleCo%",
            new_category="test",
            dry_run=True,
        )
        assert isinstance(result["sample_transactions"], list)
        assert len(result["sample_transactions"]) <= 10


# ---------------------------------------------------------------------------
# Tool registration verification
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify that the finance module registers all new CRUD tools."""

    def test_transactions_module_has_all_crud_functions(self):
        """transactions module exports all 6 required functions."""
        from butlers.tools.finance import transactions as tx

        assert hasattr(tx, "record_transaction")
        assert hasattr(tx, "list_transactions")
        assert hasattr(tx, "update_transaction")
        assert hasattr(tx, "delete_transaction")
        assert hasattr(tx, "merge_duplicates")
        assert hasattr(tx, "split_transaction")
        assert hasattr(tx, "bulk_recategorize")

    def test_finance_init_exports_crud_functions(self):
        """finance tools __init__.py re-exports all CRUD functions."""
        import butlers.tools.finance as finance_tools

        assert hasattr(finance_tools, "update_transaction")
        assert hasattr(finance_tools, "delete_transaction")
        assert hasattr(finance_tools, "merge_duplicates")
        assert hasattr(finance_tools, "split_transaction")
        assert hasattr(finance_tools, "bulk_recategorize")
        assert hasattr(finance_tools, "import_transactions")

    def test_register_tools_function_exists(self):
        """register_tools function is importable from finance modules."""
        import importlib

        module = importlib.import_module("butlers.modules._roster_finance.tools")

        assert hasattr(module, "register_tools")
        assert callable(module.register_tools)


# ---------------------------------------------------------------------------
# record_transaction enhanced features
# ---------------------------------------------------------------------------


class TestRecordTransactionEnhanced:
    """Tests for enhanced record_transaction with tiered dedup and merchant mapping."""

    async def test_dedup_priority1_external_id(self, finance_pool):
        """Priority-1 dedup: same (account_id, external_id) returns existing record."""
        from butlers.tools.finance.transactions import record_transaction

        # Create an account first (FK constraint requires it).
        acct_row = await finance_pool.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('TestBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()
        txn1 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="BankCo",
            amount=-50.00,
            currency="USD",
            category="shopping",
            account_id=acct_id,
            external_id="EXTID-001",
        )
        # Second call with same external_id + account_id should return existing row.
        txn2 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="BankCo",
            amount=-50.00,
            currency="USD",
            category="shopping",
            account_id=acct_id,
            external_id="EXTID-001",
        )
        assert txn1["id"] == txn2["id"]

    async def test_dedup_priority2_source_message_id(self, finance_pool):
        """Priority-2 dedup: same (source_message_id, merchant, amount, posted_at)."""
        from butlers.tools.finance.transactions import record_transaction

        posted = _utcnow()
        txn1 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="EmailCo",
            amount=-25.00,
            currency="USD",
            category="bills",
            source_message_id="msg-abc-123",
        )
        txn2 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="EmailCo",
            amount=-25.00,
            currency="USD",
            category="bills",
            source_message_id="msg-abc-123",
        )
        assert txn1["id"] == txn2["id"]

    async def test_dedup_priority3_composite_fallback(self, finance_pool):
        """Priority-3 dedup: same (account_id, posted_at, amount, merchant) with no ids."""
        from butlers.tools.finance.transactions import record_transaction

        # Create an account first (FK constraint requires it).
        acct_row = await finance_pool.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('CsvBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()
        txn1 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="CsvBank",
            amount=-75.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )
        # No external_id, no source_message_id — composite fallback should deduplicate.
        txn2 = await record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="CsvBank",
            amount=-75.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )
        assert txn1["id"] == txn2["id"]

    async def test_composite_unique_violation_returns_existing_transaction(self, finance_pool):
        """Composite DB races return the existing row instead of leaking UniqueViolationError."""
        from unittest.mock import patch

        from butlers.tools.finance import transactions

        # The race is only caught because finance_006 installs this partial unique index.
        assert await finance_pool.fetchval(
            "SELECT to_regclass('uq_txn_composite_dedupe') IS NOT NULL"
        )
        acct_row = await finance_pool.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('RaceBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()

        txn1 = await transactions.record_transaction(
            pool=finance_pool,
            posted_at=posted,
            merchant="RaceBank",
            amount=-88.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )

        with patch.object(transactions, "_deduplicate", return_value=None):
            txn2 = await transactions.record_transaction(
                pool=finance_pool,
                posted_at=posted,
                merchant="RaceBank",
                amount=-88.00,
                currency="USD",
                category="groceries",
                account_id=acct_id,
            )

        assert txn1["id"] == txn2["id"]

    async def test_auto_categorization_via_merchant_mapping(self, finance_pool):
        """record_transaction auto-assigns category from merchant_mappings."""
        from butlers.tools.finance.transactions import record_transaction

        # Seed a merchant mapping.
        await finance_pool.execute(
            """
            INSERT INTO merchant_mappings
                (raw_pattern, normalized_merchant, category, confidence, learned_from_count, source)
            VALUES ('%Netflix%', 'Netflix', 'subscriptions', 0.99, 1, 'manual')
            """
        )

        txn = await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Netflix",
            amount=-15.99,
            currency="USD",
            category="uncategorized",
        )
        assert txn["category"] == "subscriptions"
        assert txn.get("category_source") == "auto"

    async def test_explicit_category_not_overridden(self, finance_pool):
        """record_transaction does NOT override an explicit (non-uncategorized) category."""
        from butlers.tools.finance.transactions import record_transaction

        await finance_pool.execute(
            """
            INSERT INTO merchant_mappings
                (raw_pattern, normalized_merchant, category, confidence, learned_from_count, source)
            VALUES ('%Spotify%', 'Spotify', 'subscriptions', 0.99, 1, 'manual')
            """
        )

        txn = await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Spotify",
            amount=-9.99,
            currency="USD",
            category="entertainment",  # explicit override
        )
        assert txn["category"] == "entertainment"

    async def test_unknown_category_falls_back_when_category_fk_exists(self, finance_pool):
        """record_transaction stores unknown categories as uncategorized under the FK schema."""
        from butlers.tools.finance.transactions import record_transaction

        txn = await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Unknown Category Merchant",
            amount=-12.34,
            currency="USD",
            category="misc",
        )

        assert txn["category"] == "uncategorized"
        assert txn["category_source"] == "manual"
        assert txn["metadata"]["original_category"] == "misc"
        assert txn["metadata"]["warnings"] == [
            {
                "code": "unknown_category",
                "field": "category",
                "stored_as": "uncategorized",
            }
        ]

    async def test_category_is_canonicalized_case_insensitively_for_fk_schema(self, finance_pool):
        """record_transaction accepts display-style casing when categories are FK-backed."""
        from butlers.tools.finance.transactions import record_transaction

        txn = await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Grocery Merchant",
            amount=-45.67,
            currency="USD",
            category="Groceries",
        )

        assert txn["category"] == "groceries"
        assert txn["metadata"] == {}


# ---------------------------------------------------------------------------
# list_transactions: direction and tags filters
# ---------------------------------------------------------------------------


class TestListTransactionsEnhanced:
    """Tests for list_transactions with direction and tags filters."""

    async def test_list_filter_by_direction_debit(self, finance_pool):
        """list_transactions(direction='debit') returns only debit transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Shop",
            amount=-20.00,
            currency="USD",
            category="shopping",
        )
        await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Refund",
            amount=20.00,
            currency="USD",
            category="refund",
        )

        result = await list_transactions(pool=finance_pool, direction="debit")
        assert all(item["direction"] == "debit" for item in result["items"])

    async def test_list_filter_by_direction_credit(self, finance_pool):
        """list_transactions(direction='credit') returns only credit transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Debit",
            amount=-30.00,
            currency="USD",
            category="shopping",
        )
        await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Cashback",
            amount=5.00,
            currency="USD",
            category="cashback",
        )

        result = await list_transactions(pool=finance_pool, direction="credit")
        assert all(item["direction"] == "credit" for item in result["items"])

    async def test_list_filter_by_tags(self, finance_pool):
        """list_transactions(tags=['business']) returns only tagged transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        # Insert one tagged and one untagged transaction directly.
        await finance_pool.execute(
            """
            INSERT INTO transactions (posted_at, merchant, amount, currency, direction,
                                      category, tags)
            VALUES (now(), 'BizCo', 50.00, 'USD', 'debit', 'business', ARRAY['business', 'tax'])
            """
        )
        await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="Personal",
            amount=-10.00,
            currency="USD",
            category="shopping",
        )

        result = await list_transactions(pool=finance_pool, tags=["business"])
        assert result["total"] >= 1
        assert all("business" in (item.get("tags") or []) for item in result["items"])

    async def test_list_direction_invalid_raises(self, finance_pool):
        """list_transactions raises ValueError for invalid direction."""
        from butlers.tools.finance.transactions import list_transactions

        with pytest.raises(ValueError, match="direction must be"):
            await list_transactions(pool=finance_pool, direction="invalid")

    async def test_list_excludes_soft_deleted(self, finance_pool):
        """list_transactions always excludes soft-deleted transactions."""
        from butlers.tools.finance.transactions import (
            delete_transaction,
            list_transactions,
            record_transaction,
        )

        txn = await record_transaction(
            pool=finance_pool,
            posted_at=_utcnow(),
            merchant="DeleteMe",
            amount=-5.00,
            currency="USD",
            category="test",
        )
        await delete_transaction(pool=finance_pool, transaction_id=txn["id"])

        result = await list_transactions(pool=finance_pool, merchant="DeleteMe")
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# update_transaction: optimistic locking and category lock
# ---------------------------------------------------------------------------


class TestUpdateTransactionEnhanced:
    """Tests for update_transaction with version locking and category lock."""

    async def test_category_update_sets_is_category_locked(self, finance_pool):
        """Updating category sets is_category_locked=true and category_source='manual'."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, category="shopping")
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="electronics",
        )
        assert result["category"] == "electronics"
        assert result.get("is_category_locked") is True
        assert result.get("category_source") == "manual"

    async def test_version_incremented_on_update(self, finance_pool):
        """update_transaction increments the version column."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool)
        initial_version = txn.get("version", 1)
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            description="Updated",
        )
        assert result.get("version", initial_version) > initial_version

    async def test_optimistic_locking_correct_version_succeeds(self, finance_pool):
        """update_transaction with correct expected_version succeeds."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool)
        current_version = txn.get("version", 1)
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            description="With lock",
            expected_version=current_version,
        )
        assert result.get("description") == "With lock"

    async def test_optimistic_locking_stale_version_fails(self, finance_pool):
        """update_transaction with stale expected_version returns version_conflict error."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool)
        # First update to advance version.
        await update_transaction(pool=finance_pool, transaction_id=txn["id"], description="First")

        # Now try to update with the original version (now stale).
        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            description="Second",
            expected_version=txn.get("version", 1),  # stale
        )
        assert result.get("error") == "version_conflict"
        assert "transaction_id" in result

    async def test_correction_logged_on_category_change(self, finance_pool):
        """update_transaction records a correction entry when category changes."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, category="groceries")
        await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="dining",
            reason="user manual correction",
        )
        # Verify correction was recorded.
        correction = await finance_pool.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'category'
            """,
            txn["id"],
        )
        assert correction is not None
        assert correction["old_value"] == "groceries"
        assert correction["new_value"] == "dining"

    async def test_unknown_category_falls_back_when_category_fk_exists(self, finance_pool):
        """update_transaction stores unknown categories as uncategorized under the FK schema."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, category="uncategorized")

        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="misc",
        )

        assert result["category"] == "uncategorized"
        assert result["category_source"] == "manual"
        assert result["metadata"]["original_category"] == "misc"
        assert result["metadata"]["warnings"] == [
            {
                "code": "unknown_category",
                "field": "category",
                "stored_as": "uncategorized",
            }
        ]

    async def test_category_is_canonicalized_case_insensitively_for_fk_schema(self, finance_pool):
        """update_transaction accepts display-style casing when categories are FK-backed."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(finance_pool, category="uncategorized")

        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="Groceries",
        )

        assert result["category"] == "groceries"

    async def test_unknown_category_fallback_preserves_existing_metadata(self, finance_pool):
        """The fallback warning is merged into existing metadata when the caller
        does not also replace metadata on the same call."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(
            finance_pool,
            category="uncategorized",
            metadata={"receipt_url": "https://example.com/r"},
        )

        result = await update_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            category="not-a-real-category",
        )

        assert result["category"] == "uncategorized"
        assert result["metadata"]["receipt_url"] == "https://example.com/r"
        assert result["metadata"]["original_category"] == "not-a-real-category"

    async def test_unknown_category_fallback_preserves_metadata_when_read_as_string(
        self, finance_pool
    ):
        """Regression test: some connections decode JSONB columns as raw JSON
        strings on read (asyncpg's default) rather than pre-decoded dicts, even
        though writes still accept a dict. update_transaction must not collapse
        `current_metadata` to {} in that case — doing so would silently drop
        all pre-existing metadata keys when the fallback warning is persisted.
        """
        from butlers.db import encode_jsonb
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(
            finance_pool,
            category="uncategorized",
            metadata={"receipt_url": "https://example.com/r"},
        )

        def _passthrough_decoder(data: bytes) -> str:
            # Mirrors register_jsonb_codec's wire-format handling but skips the
            # json.loads() step, so JSONB columns come back as raw text instead
            # of pre-decoded dicts -- reproducing the shape described in the
            # review thread for this fix without disturbing the write path.
            return data[1:].decode()

        async with finance_pool.acquire() as conn:
            await conn.set_type_codec(
                "jsonb",
                encoder=encode_jsonb,
                decoder=_passthrough_decoder,
                schema="pg_catalog",
                format="binary",
            )

            result = await update_transaction(
                pool=conn,
                transaction_id=txn["id"],
                category="not-a-real-category",
            )

        assert result["category"] == "uncategorized"
        assert result["metadata"]["receipt_url"] == "https://example.com/r"
        assert result["metadata"]["original_category"] == "not-a-real-category"


# ---------------------------------------------------------------------------
# delete_transaction: version increment
# ---------------------------------------------------------------------------


class TestDeleteTransactionEnhanced:
    """Tests for delete_transaction version increment."""

    async def test_delete_increments_version(self, finance_pool):
        """delete_transaction increments the version column."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn(finance_pool)
        initial_version = txn.get("version", 1)
        result = await delete_transaction(pool=finance_pool, transaction_id=txn["id"])
        assert result.get("version", initial_version) > initial_version


# ---------------------------------------------------------------------------
# merge_duplicates: duplicate_ids list and audit trail
# ---------------------------------------------------------------------------


class TestMergeDuplicatesEnhanced:
    """Tests for merge_duplicates with duplicate_ids list and finance_002 columns."""

    async def test_merge_multiple_duplicates(self, finance_pool):
        """merge_duplicates accepts a list of duplicate_ids and processes all."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, merchant="MergeTest")
        dup1 = await _insert_txn(finance_pool, merchant="MergeTest")
        dup2 = await _insert_txn(finance_pool, merchant="MergeTest")

        result = await merge_duplicates(
            pool=finance_pool,
            keep_id=keep["id"],
            duplicate_ids=[dup1["id"], dup2["id"]],
        )
        assert result["id"] == keep["id"]

        # Both duplicates should be soft-deleted.
        for did in [dup1["id"], dup2["id"]]:
            row = await finance_pool.fetchrow(
                "SELECT deleted_at, is_duplicate FROM transactions WHERE id = $1::uuid", did
            )
            assert row["deleted_at"] is not None
            assert row["is_duplicate"] is True

    async def test_merge_sets_duplicate_of(self, finance_pool):
        """merge_duplicates sets duplicate_of=keep_id on discarded records."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, merchant="DupOf")
        dup = await _insert_txn(finance_pool, merchant="DupOf")

        await merge_duplicates(
            pool=finance_pool,
            keep_id=keep["id"],
            duplicate_ids=[dup["id"]],
        )

        row = await finance_pool.fetchrow(
            "SELECT duplicate_of FROM transactions WHERE id = $1::uuid", dup["id"]
        )
        assert str(row["duplicate_of"]) == keep["id"]

    async def test_merge_correction_logged(self, finance_pool):
        """merge_duplicates records a correction entry in transaction_corrections."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(finance_pool, merchant="AuditMerge")
        dup = await _insert_txn(finance_pool, merchant="AuditMerge")

        await merge_duplicates(
            pool=finance_pool,
            keep_id=keep["id"],
            duplicate_ids=[dup["id"]],
        )

        correction = await finance_pool.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'merge'
            """,
            keep["id"],
        )
        assert correction is not None
        assert dup["id"] in correction["new_value"]

    async def test_merge_keep_id_in_duplicate_ids_returns_error(self, finance_pool):
        """merge_duplicates returns error when keep_id appears in duplicate_ids."""
        from butlers.tools.finance.transactions import merge_duplicates

        txn = await _insert_txn(finance_pool)
        result = await merge_duplicates(
            pool=finance_pool,
            keep_id=txn["id"],
            duplicate_ids=[txn["id"]],
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# split_transaction: metadata.split_from and corrections
# ---------------------------------------------------------------------------


class TestSplitTransactionEnhanced:
    """Tests for split_transaction with metadata.split_from and corrections."""

    async def test_split_children_have_split_from_in_metadata(self, finance_pool):
        """Split records have metadata.split_from set to the original transaction ID."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00)
        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "groceries"},
                {"amount": "40.00", "category": "dining"},
            ],
        )
        for child in result["splits"]:
            assert child.get("metadata", {}).get("split_from") == txn["id"]

    async def test_split_correction_logged(self, finance_pool):
        """split_transaction records a correction entry in transaction_corrections."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-50.00)
        await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "30.00", "category": "a"},
                {"amount": "20.00", "category": "b"},
            ],
        )

        correction = await finance_pool.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'split'
            """,
            txn["id"],
        )
        assert correction is not None

    async def test_unknown_split_category_falls_back_when_category_fk_exists(self, finance_pool):
        """split_transaction stores unknown split categories as uncategorized under the FK schema."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-100.00, category="uncategorized")

        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "groceries"},
                {"amount": "40.00", "category": "not-a-real-category"},
            ],
        )

        known, unknown = result["splits"]
        assert known["category"] == "groceries"
        assert unknown["category"] == "uncategorized"
        assert unknown["metadata"]["original_category"] == "not-a-real-category"
        assert unknown["metadata"]["warnings"] == [
            {
                "code": "unknown_category",
                "field": "category",
                "stored_as": "uncategorized",
            }
        ]

    async def test_split_category_is_canonicalized_case_insensitively_for_fk_schema(
        self, finance_pool
    ):
        """split_transaction accepts display-style casing when categories are FK-backed."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(finance_pool, amount=-30.00, category="uncategorized")

        result = await split_transaction(
            pool=finance_pool,
            transaction_id=txn["id"],
            splits=[{"amount": "30.00", "category": "Groceries"}],
        )

        assert result["splits"][0]["category"] == "groceries"


class TestSchemaExistenceCacheIdentity:
    """Regression guard for bu-3keyu.

    ``_has_table``/``_has_column`` memoize schema facts per pool for the
    lifetime of the process (see roster/finance/tools/transactions.py). Under
    the full CI suite, thousands of short-lived pools are created and closed
    within one worker process; if the cache were keyed by ``id(pool)`` (a
    plain memory address), a garbage-collected pool's freed address could be
    reused by a brand-new, unrelated pool, causing that new pool to silently
    inherit a stale (and possibly wrong) schema fact -- e.g. a pool whose
    schema genuinely has the ``categories`` FK table reading back a dead
    pool's cached "no such table," skipping the unknown-category fallback and
    tripping a real FK violation on insert. This is exactly the flake this
    bead was filed for. Keying by the pool object itself (not id(pool)) fixes
    it: a dict holds a strong reference to its keys, so the same address can
    never be reused while a cache entry for it still exists.
    """

    async def test_table_and_column_cache_are_keyed_by_pool_object_not_id(self, finance_pool):
        """Cache dicts must never be keyed by a raw int (id(pool))."""
        from butlers.tools.finance import transactions as _txn_module

        await _txn_module._has_table(finance_pool, "categories")
        await _txn_module._has_column(finance_pool, "transactions", "category")

        assert not any(isinstance(key, int) for key in _txn_module._table_existence_cache)
        assert not any(isinstance(key, int) for key in _txn_module._column_existence_cache)

    async def test_unrelated_pool_never_observes_another_pools_cached_facts(self, finance_pool, db):
        """Two distinct, concurrently-alive pools must never share cache entries."""
        from butlers.tools.finance.transactions import _has_table

        # A second migrated clone with the taxonomy table dropped caches
        # "no categories table" first -- the stale fact behind bu-3keyu.
        other_pool = await db("core", "finance")
        await other_pool.execute("DROP TABLE categories CASCADE")
        assert await _has_table(other_pool, "categories") is False

        # finance_pool genuinely has the table and must see the truth for its own
        # schema, not other_pool's cached fact.
        assert await _has_table(finance_pool, "categories") is True

        # And other_pool's own cached fact must remain unaffected.
        assert await _has_table(other_pool, "categories") is False


# ---------------------------------------------------------------------------
# bulk_recategorize: create_rule and is_category_locked
# ---------------------------------------------------------------------------


class TestBulkRecategorizeEnhanced:
    """Tests for bulk_recategorize with create_rule and category lock."""

    async def test_bulk_recategorize_create_rule_upserts_mapping(self, finance_pool):
        """bulk_recategorize with create_rule=True upserts a merchant_mappings row."""
        from butlers.tools.finance.transactions import bulk_recategorize

        await _insert_txn(finance_pool, merchant="RuleTest Corp", category="shopping")
        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%RuleTest%",
            new_category="services",
            create_rule=True,
        )
        assert result["create_rule"] is True

        # Verify mapping was created.
        mapping = await finance_pool.fetchrow(
            "SELECT * FROM merchant_mappings WHERE raw_pattern = $1",
            "%RuleTest%",
        )
        assert mapping is not None
        assert mapping["category"] == "services"
        assert mapping["normalized_merchant"] == "RuleTest"

    async def test_bulk_recategorize_skips_locked_transactions(self, finance_pool):
        """bulk_recategorize does not update category-locked transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize, update_transaction

        txn = await _insert_txn(finance_pool, merchant="LockedMerchant", category="original")
        # Lock the category by doing a manual update.
        await update_transaction(pool=finance_pool, transaction_id=txn["id"], category="manual_cat")
        # Now the transaction should be category-locked.

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%LockedMerchant%",
            new_category="bulk_override",
        )
        # The locked transaction should not be updated.
        assert result["updated"] == 0

    async def test_bulk_recategorize_create_rule_response_key(self, finance_pool):
        """bulk_recategorize result always includes create_rule key."""
        from butlers.tools.finance.transactions import bulk_recategorize

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%NothingMatches%",
            new_category="test",
        )
        assert "create_rule" in result
        assert result["create_rule"] is False

    async def test_bulk_recategorize_unknown_category_falls_back_when_category_fk_exists(
        self, finance_pool
    ):
        """bulk_recategorize stores an out-of-taxonomy new_category as uncategorized
        under the FK schema instead of raising ForeignKeyViolationError.

        Regression test for bu-f3guj: bulk_recategorize previously wrote
        new_category directly via a bulk UPDATE, bypassing the
        _resolve_category_for_insert taxonomy guard that record_transaction /
        update_transaction / split_transaction already route through (bu-24jwd,
        PR #2848), so an out-of-taxonomy category tripped the
        transactions.category -> categories.name FK on migrated schemas.
        """
        from butlers.tools.finance.transactions import bulk_recategorize

        txn = await _insert_txn(finance_pool, merchant="FkBulkMerchant", category="uncategorized")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%FkBulkMerchant%",
            new_category="not-a-real-category",
        )

        assert result["updated"] == 1
        assert result["resolved_category"] == "uncategorized"
        assert result["used_category_fallback"] is True
        assert result["original_category"] == "not-a-real-category"

        row = await finance_pool.fetchrow(
            "SELECT category FROM transactions WHERE id = $1::uuid", txn["id"]
        )
        assert row["category"] == "uncategorized"

    async def test_bulk_recategorize_category_is_canonicalized_case_insensitively_for_fk_schema(
        self, finance_pool
    ):
        """bulk_recategorize accepts display-style casing when categories are FK-backed."""
        from butlers.tools.finance.transactions import bulk_recategorize

        txn = await _insert_txn(finance_pool, merchant="FkBulkCanon", category="uncategorized")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%FkBulkCanon%",
            new_category="Groceries",
        )

        assert result["updated"] == 1
        assert result["resolved_category"] == "groceries"
        assert "used_category_fallback" not in result

        row = await finance_pool.fetchrow(
            "SELECT category FROM transactions WHERE id = $1::uuid", txn["id"]
        )
        assert row["category"] == "groceries"

    async def test_bulk_recategorize_create_rule_uses_resolved_category_under_fk_schema(
        self, finance_pool
    ):
        """The create_rule merchant_mappings upsert stores the resolved (not raw)
        category, so it does not later leak an out-of-taxonomy value into
        auto-categorization on schemas where merchant_mappings.category also
        carries the categories FK (fk_merchant_mappings_category, finance_006)."""
        from butlers.tools.finance.transactions import bulk_recategorize

        await _insert_txn(finance_pool, merchant="FkBulkRuleMerchant", category="uncategorized")

        result = await bulk_recategorize(
            pool=finance_pool,
            merchant_pattern="%FkBulkRuleMerchant%",
            new_category="not-a-real-category",
            create_rule=True,
        )
        assert result["resolved_category"] == "uncategorized"

        mapping = await finance_pool.fetchrow(
            "SELECT category FROM merchant_mappings WHERE raw_pattern = $1",
            "%FkBulkRuleMerchant%",
        )
        assert mapping is not None
        assert mapping["category"] == "uncategorized"


# ---------------------------------------------------------------------------
# spending_summary: deleted_at IS NULL filter
# ---------------------------------------------------------------------------


class TestSpendingSummaryEnhanced:
    """Tests for spending_summary with deleted_at exclusion."""

    async def test_spending_summary_excludes_deleted(self, finance_pool):
        """spending_summary does not count soft-deleted transactions."""
        from datetime import date

        from butlers.tools.finance.spending import spending_summary
        from butlers.tools.finance.transactions import delete_transaction, record_transaction

        today = _utcnow()
        txn = await record_transaction(
            pool=finance_pool,
            posted_at=today,
            merchant="DeletedSpend",
            amount=-200.00,
            currency="USD",
            category="shopping",
        )
        await delete_transaction(pool=finance_pool, transaction_id=txn["id"])

        result = await spending_summary(
            pool=finance_pool,
            start_date=date(today.year, today.month, today.day),
            end_date=date(today.year, today.month, today.day),
        )
        # The deleted transaction should not appear in the total.
        assert Decimal(result["total_spend"]) == Decimal("0")
