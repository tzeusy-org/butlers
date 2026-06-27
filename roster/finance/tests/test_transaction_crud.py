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


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with the full finance schema (incl. deleted_at)."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                institution TEXT NOT NULL,
                type        TEXT NOT NULL
                                CHECK (type IN ('checking', 'savings', 'credit', 'investment')),
                name        TEXT,
                last_four   CHAR(4),
                currency    CHAR(3) NOT NULL DEFAULT 'USD',
                metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
                source_message_id TEXT,
                posted_at         TIMESTAMPTZ NOT NULL,
                merchant          TEXT NOT NULL,
                description       TEXT,
                amount            NUMERIC(14, 2) NOT NULL,
                currency          CHAR(3) NOT NULL,
                direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
                category          TEXT NOT NULL,
                payment_method    TEXT,
                receipt_url       TEXT,
                external_ref      TEXT,
                deleted_at        TIMESTAMPTZ,
                metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
                ON transactions (source_message_id, merchant, amount, posted_at)
                WHERE source_message_id IS NOT NULL
        """)
        # merchant_mappings needed by learn_merchant_categories (called by bulk_recategorize)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS merchant_mappings (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                merchant_pattern TEXT NOT NULL UNIQUE,
                category         TEXT NOT NULL,
                confidence       NUMERIC(5, 4) NOT NULL DEFAULT 1.0,
                sample_count     INTEGER NOT NULL DEFAULT 1,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


async def _insert_txn(
    pool,
    merchant="Test Co",
    amount=-10.00,
    category="shopping",
    description=None,
    metadata=None,
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
    )


# ---------------------------------------------------------------------------
# update_transaction
# ---------------------------------------------------------------------------


class TestUpdateTransaction:
    """Tests for update_transaction."""

    async def test_update_category(self, pool):
        """update_transaction changes the category field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(pool, merchant="Amazon", category="shopping")
        result = await update_transaction(
            pool=pool,
            transaction_id=txn["id"],
            category="electronics",
        )
        assert result["category"] == "electronics"
        assert result["id"] == txn["id"]

    async def test_update_merchant(self, pool):
        """update_transaction changes the merchant field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(pool, merchant="Old Name")
        result = await update_transaction(
            pool=pool,
            transaction_id=txn["id"],
            merchant="New Name",
        )
        assert result["merchant"] == "New Name"

    async def test_update_description(self, pool):
        """update_transaction changes the description field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(pool, description=None)
        result = await update_transaction(
            pool=pool,
            transaction_id=txn["id"],
            description="Updated description",
        )
        assert result["description"] == "Updated description"

    async def test_update_metadata(self, pool):
        """update_transaction replaces the metadata field."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(pool, metadata={"old_key": "old_val"})
        result = await update_transaction(
            pool=pool,
            transaction_id=txn["id"],
            metadata={"new_key": "new_val"},
        )
        assert result["metadata"] == {"new_key": "new_val"}

    async def test_update_no_fields_returns_current(self, pool):
        """update_transaction with no fields returns the current record unchanged."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn(pool, category="groceries")
        result = await update_transaction(pool=pool, transaction_id=txn["id"])
        assert result["id"] == txn["id"]
        assert result["category"] == "groceries"

    async def test_update_not_found_returns_error(self, pool):
        """update_transaction with unknown ID returns error dict."""
        import uuid

        from butlers.tools.finance.transactions import update_transaction

        fake_id = str(uuid.uuid4())
        result = await update_transaction(pool=pool, transaction_id=fake_id, category="dining")
        assert result["error"] == "transaction_not_found"
        assert result["transaction_id"] == fake_id


# ---------------------------------------------------------------------------
# delete_transaction
# ---------------------------------------------------------------------------


class TestDeleteTransaction:
    """Tests for delete_transaction."""

    async def test_delete_sets_deleted_at(self, pool):
        """delete_transaction sets deleted_at on the record."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn(pool)
        result = await delete_transaction(pool=pool, transaction_id=txn["id"])
        assert result["id"] == txn["id"]
        assert result["deleted_at"] is not None

    async def test_delete_excluded_from_list(self, pool):
        """Soft-deleted transactions are excluded from list_transactions."""
        from butlers.tools.finance.transactions import delete_transaction, list_transactions

        txn = await _insert_txn(pool, merchant="Deleted Corp")
        await delete_transaction(pool=pool, transaction_id=txn["id"])

        result = await list_transactions(pool=pool, merchant="Deleted Corp")
        assert result["total"] == 0

    async def test_delete_is_idempotent(self, pool):
        """Calling delete_transaction twice does not raise and returns the record."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn(pool)
        first = await delete_transaction(pool=pool, transaction_id=txn["id"])
        second = await delete_transaction(pool=pool, transaction_id=txn["id"])
        # deleted_at should be the same (COALESCE preserves original timestamp)
        assert first["deleted_at"] == second["deleted_at"]

    async def test_delete_not_found(self, pool):
        """delete_transaction with unknown ID returns error dict."""
        import uuid

        from butlers.tools.finance.transactions import delete_transaction

        fake_id = str(uuid.uuid4())
        result = await delete_transaction(pool=pool, transaction_id=fake_id)
        assert result["error"] == "transaction_not_found"


# ---------------------------------------------------------------------------
# merge_duplicates
# ---------------------------------------------------------------------------


class TestMergeDuplicates:
    """Tests for merge_duplicates."""

    async def test_merge_soft_deletes_discard(self, pool):
        """merge_duplicates soft-deletes the discard transaction."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool, merchant="Netflix")
        discard = await _insert_txn(pool, merchant="Netflix")

        await merge_duplicates(pool=pool, keep_id=keep["id"], duplicate_ids=[discard["id"]])

        # Check discard has deleted_at set
        row = await pool.fetchrow(
            "SELECT deleted_at FROM transactions WHERE id = $1::uuid",
            discard["id"],
        )
        assert row["deleted_at"] is not None

    async def test_merge_keeps_keep_record(self, pool):
        """merge_duplicates returns the kept record with merged metadata."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool, metadata={"source": "keep"})
        discard = await _insert_txn(pool, metadata={"extra": "from_discard"})

        result = await merge_duplicates(
            pool=pool, keep_id=keep["id"], duplicate_ids=[discard["id"]]
        )
        assert result["id"] == keep["id"]
        # keep's metadata is preserved; discard's non-conflicting keys are merged in
        assert result["metadata"]["source"] == "keep"
        assert result["metadata"]["extra"] == "from_discard"

    async def test_merge_keep_wins_on_metadata_conflict(self, pool):
        """Keep's metadata values win when keys conflict."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool, metadata={"key": "keep_val"})
        discard = await _insert_txn(pool, metadata={"key": "discard_val"})

        result = await merge_duplicates(
            pool=pool, keep_id=keep["id"], duplicate_ids=[discard["id"]]
        )
        assert result["metadata"]["key"] == "keep_val"

    async def test_merge_same_id_returns_error(self, pool):
        """merge_duplicates with keep_id appearing in duplicate_ids returns error."""
        from butlers.tools.finance.transactions import merge_duplicates

        txn = await _insert_txn(pool)
        result = await merge_duplicates(pool=pool, keep_id=txn["id"], duplicate_ids=[txn["id"]])
        assert "error" in result

    async def test_merge_keep_not_found(self, pool):
        """merge_duplicates with unknown keep_id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        discard = await _insert_txn(pool)
        result = await merge_duplicates(
            pool=pool,
            keep_id=str(uuid.uuid4()),
            duplicate_ids=[discard["id"]],
        )
        assert result["error"] == "keep_transaction_not_found"

    async def test_merge_discard_not_found(self, pool):
        """merge_duplicates with unknown duplicate id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool)
        result = await merge_duplicates(
            pool=pool,
            keep_id=keep["id"],
            duplicate_ids=[str(uuid.uuid4())],
        )
        assert result["error"] == "discard_transaction_not_found"


# ---------------------------------------------------------------------------
# split_transaction
# ---------------------------------------------------------------------------


class TestSplitTransaction:
    """Tests for split_transaction."""

    async def test_split_creates_new_records(self, pool):
        """split_transaction creates the requested number of split records."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-100.00, category="shopping")
        result = await split_transaction(
            pool=pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "groceries"},
                {"amount": "40.00", "category": "dining"},
            ],
        )
        assert "splits" in result
        assert len(result["splits"]) == 2
        assert result["original_id"] == txn["id"]

    async def test_split_soft_deletes_original(self, pool):
        """split_transaction soft-deletes the original record."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-50.00)
        await split_transaction(
            pool=pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "30.00", "category": "a"},
                {"amount": "20.00", "category": "b"},
            ],
        )
        row = await pool.fetchrow(
            "SELECT deleted_at FROM transactions WHERE id = $1::uuid",
            txn["id"],
        )
        assert row["deleted_at"] is not None

    async def test_split_amounts_assigned_correctly(self, pool):
        """Split records have the correct amounts and categories."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-100.00)
        result = await split_transaction(
            pool=pool,
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

    async def test_split_mismatched_amounts_returns_error(self, pool):
        """split_transaction returns error when split amounts do not sum to original."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-100.00)
        result = await split_transaction(
            pool=pool,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "a"},
                {"amount": "30.00", "category": "b"},  # 60+30=90 != 100
            ],
        )
        assert "error" in result
        assert result["transaction_id"] == txn["id"]

    async def test_split_empty_splits_returns_error(self, pool):
        """split_transaction with empty splits list returns error."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-50.00)
        result = await split_transaction(pool=pool, transaction_id=txn["id"], splits=[])
        assert "error" in result

    async def test_split_missing_category_returns_error(self, pool):
        """split_transaction returns error when a split is missing category."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-50.00)
        result = await split_transaction(
            pool=pool,
            transaction_id=txn["id"],
            splits=[{"amount": "50.00"}],  # no category
        )
        assert "error" in result

    async def test_split_not_found_returns_error(self, pool):
        """split_transaction on unknown ID returns error."""
        import uuid

        from butlers.tools.finance.transactions import split_transaction

        fake_id = str(uuid.uuid4())
        result = await split_transaction(
            pool=pool,
            transaction_id=fake_id,
            splits=[{"amount": "10.00", "category": "a"}],
        )
        assert result["error"] == "transaction_not_found"

    async def test_split_description_override(self, pool):
        """Split records accept per-split description overrides."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn(pool, amount=-100.00, description="original desc")
        result = await split_transaction(
            pool=pool,
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

    async def test_bulk_recategorize_updates_matching(self, pool):
        """bulk_recategorize updates category for all matching transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize

        for _ in range(3):
            await _insert_txn(pool, merchant="Netflix", category="entertainment")

        result = await bulk_recategorize(
            pool=pool,
            merchant_pattern="%Netflix%",
            new_category="subscriptions",
        )
        assert result["matched"] == 3
        assert result["updated"] == 3
        assert result["dry_run"] is False

        # Verify DB
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM transactions"
            " WHERE merchant = 'Netflix' AND category = 'subscriptions'"
        )
        assert count == 3

    async def test_bulk_recategorize_dry_run_no_changes(self, pool):
        """bulk_recategorize dry_run=True does not modify records."""
        from butlers.tools.finance.transactions import bulk_recategorize

        await _insert_txn(pool, merchant="Spotify", category="entertainment")

        result = await bulk_recategorize(
            pool=pool,
            merchant_pattern="%Spotify%",
            new_category="subscriptions",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["matched"] == 1
        assert result["updated"] == 0

        # Verify no DB change
        cat = await pool.fetchval("SELECT category FROM transactions WHERE merchant = 'Spotify'")
        assert cat == "entertainment"

    async def test_bulk_recategorize_excludes_deleted(self, pool):
        """bulk_recategorize does not update soft-deleted transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize, delete_transaction

        txn = await _insert_txn(pool, merchant="OldCo", category="shopping")
        await delete_transaction(pool=pool, transaction_id=txn["id"])

        result = await bulk_recategorize(
            pool=pool,
            merchant_pattern="%OldCo%",
            new_category="archived",
        )
        assert result["matched"] == 0
        assert result["updated"] == 0

    async def test_bulk_recategorize_no_match(self, pool):
        """bulk_recategorize returns matched=0 when pattern matches nothing."""
        from butlers.tools.finance.transactions import bulk_recategorize

        result = await bulk_recategorize(
            pool=pool,
            merchant_pattern="%NoSuchMerchant%",
            new_category="other",
        )
        assert result["matched"] == 0
        assert result["updated"] == 0

    async def test_bulk_recategorize_sample_transactions(self, pool):
        """bulk_recategorize returns up to 10 sample_transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize

        for i in range(5):
            await _insert_txn(pool, merchant=f"SampleCo {i}", category="misc")

        result = await bulk_recategorize(
            pool=pool,
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
        import importlib.util
        from pathlib import Path

        tools_path = Path(__file__).parent.parent / "modules" / "tools.py"
        spec = importlib.util.spec_from_file_location("finance_module_tools", tools_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "register_tools")
        assert callable(module.register_tools)


# ---------------------------------------------------------------------------
# Extended pool fixture with finance_002 columns
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool_v2(provisioned_postgres_pool):
    """Provision schema with finance_002 columns: version, is_category_locked, etc."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                institution TEXT NOT NULL,
                type        TEXT NOT NULL
                                CHECK (type IN ('checking', 'savings', 'credit', 'investment')),
                name        TEXT,
                last_four   CHAR(4),
                currency    CHAR(3) NOT NULL DEFAULT 'USD',
                metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
                source_message_id TEXT,
                external_id       TEXT,
                posted_at         TIMESTAMPTZ NOT NULL,
                merchant          TEXT NOT NULL,
                description       TEXT,
                amount            NUMERIC(14, 2) NOT NULL,
                currency          CHAR(3) NOT NULL,
                direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
                category          TEXT NOT NULL,
                category_source   TEXT NOT NULL DEFAULT 'manual'
                    CHECK (category_source IN ('auto', 'manual', 'ml', 'rule')),
                is_category_locked BOOLEAN NOT NULL DEFAULT false,
                payment_method    TEXT,
                receipt_url       TEXT,
                external_ref      TEXT,
                deleted_at        TIMESTAMPTZ,
                is_duplicate      BOOLEAN NOT NULL DEFAULT false,
                duplicate_of      UUID REFERENCES transactions(id),
                tags              TEXT[] NOT NULL DEFAULT '{}',
                version           INTEGER NOT NULL DEFAULT 1,
                metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
                ON transactions (source_message_id, merchant, amount, posted_at)
                WHERE source_message_id IS NOT NULL
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS transaction_corrections (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                transaction_id UUID NOT NULL,
                field_name     TEXT NOT NULL,
                old_value      TEXT,
                new_value      TEXT,
                reason         TEXT,
                source         TEXT NOT NULL DEFAULT 'manual',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS merchant_mappings (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                raw_pattern         TEXT NOT NULL,
                normalized_merchant TEXT NOT NULL,
                category            TEXT NOT NULL,
                confidence          FLOAT NOT NULL DEFAULT 1.0,
                learned_from_count  INTEGER NOT NULL DEFAULT 1,
                source              TEXT NOT NULL DEFAULT 'manual',
                is_active           BOOLEAN NOT NULL DEFAULT true,
                metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mapping_pattern
                ON merchant_mappings (lower(raw_pattern))
                WHERE is_active = true
        """)
        yield p


async def _insert_txn_v2(
    pool,
    merchant="Test Co",
    amount=-10.00,
    category="shopping",
    description=None,
    metadata=None,
    account_id=None,
    external_id=None,
    source_message_id=None,
) -> dict:
    """Helper: insert a transaction into the v2 schema."""
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
    )


# ---------------------------------------------------------------------------
# record_transaction enhanced features
# ---------------------------------------------------------------------------


class TestRecordTransactionEnhanced:
    """Tests for enhanced record_transaction with tiered dedup and merchant mapping."""

    async def test_dedup_priority1_external_id(self, pool_v2):
        """Priority-1 dedup: same (account_id, external_id) returns existing record."""
        from butlers.tools.finance.transactions import record_transaction

        # Create an account first (FK constraint requires it).
        acct_row = await pool_v2.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('TestBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()
        txn1 = await record_transaction(
            pool=pool_v2,
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
            pool=pool_v2,
            posted_at=posted,
            merchant="BankCo",
            amount=-50.00,
            currency="USD",
            category="shopping",
            account_id=acct_id,
            external_id="EXTID-001",
        )
        assert txn1["id"] == txn2["id"]

    async def test_dedup_priority2_source_message_id(self, pool_v2):
        """Priority-2 dedup: same (source_message_id, merchant, amount, posted_at)."""
        from butlers.tools.finance.transactions import record_transaction

        posted = _utcnow()
        txn1 = await record_transaction(
            pool=pool_v2,
            posted_at=posted,
            merchant="EmailCo",
            amount=-25.00,
            currency="USD",
            category="bills",
            source_message_id="msg-abc-123",
        )
        txn2 = await record_transaction(
            pool=pool_v2,
            posted_at=posted,
            merchant="EmailCo",
            amount=-25.00,
            currency="USD",
            category="bills",
            source_message_id="msg-abc-123",
        )
        assert txn1["id"] == txn2["id"]

    async def test_dedup_priority3_composite_fallback(self, pool_v2):
        """Priority-3 dedup: same (account_id, posted_at, amount, merchant) with no ids."""
        from butlers.tools.finance.transactions import record_transaction

        # Create an account first (FK constraint requires it).
        acct_row = await pool_v2.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('CsvBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()
        txn1 = await record_transaction(
            pool=pool_v2,
            posted_at=posted,
            merchant="CsvBank",
            amount=-75.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )
        # No external_id, no source_message_id — composite fallback should deduplicate.
        txn2 = await record_transaction(
            pool=pool_v2,
            posted_at=posted,
            merchant="CsvBank",
            amount=-75.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )
        assert txn1["id"] == txn2["id"]

    async def test_composite_unique_violation_returns_existing_transaction(self, pool_v2):
        """Composite DB races return the existing row instead of leaking UniqueViolationError."""
        from unittest.mock import patch

        from butlers.tools.finance import transactions

        await pool_v2.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_txn_composite_dedupe
                ON transactions (account_id, posted_at, amount, merchant)
                WHERE external_id IS NULL AND source_message_id IS NULL
        """)
        acct_row = await pool_v2.fetchrow(
            """
            INSERT INTO accounts (institution, type, currency)
            VALUES ('RaceBank', 'checking', 'USD')
            RETURNING id
            """
        )
        acct_id = str(acct_row["id"])
        posted = _utcnow()

        txn1 = await transactions.record_transaction(
            pool=pool_v2,
            posted_at=posted,
            merchant="RaceBank",
            amount=-88.00,
            currency="USD",
            category="groceries",
            account_id=acct_id,
        )

        with patch.object(transactions, "_deduplicate", return_value=None):
            txn2 = await transactions.record_transaction(
                pool=pool_v2,
                posted_at=posted,
                merchant="RaceBank",
                amount=-88.00,
                currency="USD",
                category="groceries",
                account_id=acct_id,
            )

        assert txn1["id"] == txn2["id"]

    async def test_auto_categorization_via_merchant_mapping(self, pool_v2):
        """record_transaction auto-assigns category from merchant_mappings."""
        from butlers.tools.finance.transactions import record_transaction

        # Seed a merchant mapping.
        await pool_v2.execute(
            """
            INSERT INTO merchant_mappings
                (raw_pattern, normalized_merchant, category, confidence, learned_from_count, source)
            VALUES ('%Netflix%', 'Netflix', 'subscriptions', 0.99, 1, 'manual')
            """
        )

        txn = await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Netflix",
            amount=-15.99,
            currency="USD",
            category="uncategorized",
        )
        assert txn["category"] == "subscriptions"
        assert txn.get("category_source") == "auto"

    async def test_explicit_category_not_overridden(self, pool_v2):
        """record_transaction does NOT override an explicit (non-uncategorized) category."""
        from butlers.tools.finance.transactions import record_transaction

        await pool_v2.execute(
            """
            INSERT INTO merchant_mappings
                (raw_pattern, normalized_merchant, category, confidence, learned_from_count, source)
            VALUES ('%Spotify%', 'Spotify', 'subscriptions', 0.99, 1, 'manual')
            """
        )

        txn = await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Spotify",
            amount=-9.99,
            currency="USD",
            category="entertainment",  # explicit override
        )
        assert txn["category"] == "entertainment"


# ---------------------------------------------------------------------------
# list_transactions: direction and tags filters
# ---------------------------------------------------------------------------


class TestListTransactionsEnhanced:
    """Tests for list_transactions with direction and tags filters."""

    async def test_list_filter_by_direction_debit(self, pool_v2):
        """list_transactions(direction='debit') returns only debit transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Shop",
            amount=-20.00,
            currency="USD",
            category="shopping",
        )
        await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Refund",
            amount=20.00,
            currency="USD",
            category="refund",
        )

        result = await list_transactions(pool=pool_v2, direction="debit")
        assert all(item["direction"] == "debit" for item in result["items"])

    async def test_list_filter_by_direction_credit(self, pool_v2):
        """list_transactions(direction='credit') returns only credit transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Debit",
            amount=-30.00,
            currency="USD",
            category="shopping",
        )
        await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Cashback",
            amount=5.00,
            currency="USD",
            category="cashback",
        )

        result = await list_transactions(pool=pool_v2, direction="credit")
        assert all(item["direction"] == "credit" for item in result["items"])

    async def test_list_filter_by_tags(self, pool_v2):
        """list_transactions(tags=['business']) returns only tagged transactions."""
        from butlers.tools.finance.transactions import list_transactions, record_transaction

        # Insert one tagged and one untagged transaction directly.
        await pool_v2.execute(
            """
            INSERT INTO transactions (posted_at, merchant, amount, currency, direction,
                                      category, tags)
            VALUES (now(), 'BizCo', 50.00, 'USD', 'debit', 'business', ARRAY['business', 'tax'])
            """
        )
        await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="Personal",
            amount=-10.00,
            currency="USD",
            category="shopping",
        )

        result = await list_transactions(pool=pool_v2, tags=["business"])
        assert result["total"] >= 1
        assert all("business" in (item.get("tags") or []) for item in result["items"])

    async def test_list_direction_invalid_raises(self, pool_v2):
        """list_transactions raises ValueError for invalid direction."""
        from butlers.tools.finance.transactions import list_transactions

        with pytest.raises(ValueError, match="direction must be"):
            await list_transactions(pool=pool_v2, direction="invalid")

    async def test_list_excludes_soft_deleted(self, pool_v2):
        """list_transactions always excludes soft-deleted transactions."""
        from butlers.tools.finance.transactions import (
            delete_transaction,
            list_transactions,
            record_transaction,
        )

        txn = await record_transaction(
            pool=pool_v2,
            posted_at=_utcnow(),
            merchant="DeleteMe",
            amount=-5.00,
            currency="USD",
            category="test",
        )
        await delete_transaction(pool=pool_v2, transaction_id=txn["id"])

        result = await list_transactions(pool=pool_v2, merchant="DeleteMe")
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# update_transaction: optimistic locking and category lock
# ---------------------------------------------------------------------------


class TestUpdateTransactionEnhanced:
    """Tests for update_transaction with version locking and category lock."""

    async def test_category_update_sets_is_category_locked(self, pool_v2):
        """Updating category sets is_category_locked=true and category_source='manual'."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn_v2(pool_v2, category="shopping")
        result = await update_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            category="electronics",
        )
        assert result["category"] == "electronics"
        assert result.get("is_category_locked") is True
        assert result.get("category_source") == "manual"

    async def test_version_incremented_on_update(self, pool_v2):
        """update_transaction increments the version column."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn_v2(pool_v2)
        initial_version = txn.get("version", 1)
        result = await update_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            description="Updated",
        )
        assert result.get("version", initial_version) > initial_version

    async def test_optimistic_locking_correct_version_succeeds(self, pool_v2):
        """update_transaction with correct expected_version succeeds."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn_v2(pool_v2)
        current_version = txn.get("version", 1)
        result = await update_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            description="With lock",
            expected_version=current_version,
        )
        assert result.get("description") == "With lock"

    async def test_optimistic_locking_stale_version_fails(self, pool_v2):
        """update_transaction with stale expected_version returns version_conflict error."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn_v2(pool_v2)
        # First update to advance version.
        await update_transaction(pool=pool_v2, transaction_id=txn["id"], description="First")

        # Now try to update with the original version (now stale).
        result = await update_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            description="Second",
            expected_version=txn.get("version", 1),  # stale
        )
        assert result.get("error") == "version_conflict"
        assert "transaction_id" in result

    async def test_correction_logged_on_category_change(self, pool_v2):
        """update_transaction records a correction entry when category changes."""
        from butlers.tools.finance.transactions import update_transaction

        txn = await _insert_txn_v2(pool_v2, category="groceries")
        await update_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            category="dining",
            reason="user manual correction",
        )
        # Verify correction was recorded.
        correction = await pool_v2.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'category'
            """,
            txn["id"],
        )
        assert correction is not None
        assert correction["old_value"] == "groceries"
        assert correction["new_value"] == "dining"


# ---------------------------------------------------------------------------
# delete_transaction: version increment
# ---------------------------------------------------------------------------


class TestDeleteTransactionEnhanced:
    """Tests for delete_transaction version increment."""

    async def test_delete_increments_version(self, pool_v2):
        """delete_transaction increments the version column."""
        from butlers.tools.finance.transactions import delete_transaction

        txn = await _insert_txn_v2(pool_v2)
        initial_version = txn.get("version", 1)
        result = await delete_transaction(pool=pool_v2, transaction_id=txn["id"])
        assert result.get("version", initial_version) > initial_version


# ---------------------------------------------------------------------------
# merge_duplicates: duplicate_ids list and audit trail
# ---------------------------------------------------------------------------


class TestMergeDuplicatesEnhanced:
    """Tests for merge_duplicates with duplicate_ids list and finance_002 columns."""

    async def test_merge_multiple_duplicates(self, pool_v2):
        """merge_duplicates accepts a list of duplicate_ids and processes all."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn_v2(pool_v2, merchant="MergeTest")
        dup1 = await _insert_txn_v2(pool_v2, merchant="MergeTest")
        dup2 = await _insert_txn_v2(pool_v2, merchant="MergeTest")

        result = await merge_duplicates(
            pool=pool_v2,
            keep_id=keep["id"],
            duplicate_ids=[dup1["id"], dup2["id"]],
        )
        assert result["id"] == keep["id"]

        # Both duplicates should be soft-deleted.
        for did in [dup1["id"], dup2["id"]]:
            row = await pool_v2.fetchrow(
                "SELECT deleted_at, is_duplicate FROM transactions WHERE id = $1::uuid", did
            )
            assert row["deleted_at"] is not None
            assert row["is_duplicate"] is True

    async def test_merge_sets_duplicate_of(self, pool_v2):
        """merge_duplicates sets duplicate_of=keep_id on discarded records."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn_v2(pool_v2, merchant="DupOf")
        dup = await _insert_txn_v2(pool_v2, merchant="DupOf")

        await merge_duplicates(
            pool=pool_v2,
            keep_id=keep["id"],
            duplicate_ids=[dup["id"]],
        )

        row = await pool_v2.fetchrow(
            "SELECT duplicate_of FROM transactions WHERE id = $1::uuid", dup["id"]
        )
        assert str(row["duplicate_of"]) == keep["id"]

    async def test_merge_correction_logged(self, pool_v2):
        """merge_duplicates records a correction entry in transaction_corrections."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn_v2(pool_v2, merchant="AuditMerge")
        dup = await _insert_txn_v2(pool_v2, merchant="AuditMerge")

        await merge_duplicates(
            pool=pool_v2,
            keep_id=keep["id"],
            duplicate_ids=[dup["id"]],
        )

        correction = await pool_v2.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'merge'
            """,
            keep["id"],
        )
        assert correction is not None
        assert dup["id"] in correction["new_value"]

    async def test_merge_keep_id_in_duplicate_ids_returns_error(self, pool_v2):
        """merge_duplicates returns error when keep_id appears in duplicate_ids."""
        from butlers.tools.finance.transactions import merge_duplicates

        txn = await _insert_txn_v2(pool_v2)
        result = await merge_duplicates(
            pool=pool_v2,
            keep_id=txn["id"],
            duplicate_ids=[txn["id"]],
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# split_transaction: metadata.split_from and corrections
# ---------------------------------------------------------------------------


class TestSplitTransactionEnhanced:
    """Tests for split_transaction with metadata.split_from and corrections."""

    async def test_split_children_have_split_from_in_metadata(self, pool_v2):
        """Split records have metadata.split_from set to the original transaction ID."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn_v2(pool_v2, amount=-100.00)
        result = await split_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            splits=[
                {"amount": "60.00", "category": "groceries"},
                {"amount": "40.00", "category": "dining"},
            ],
        )
        for child in result["splits"]:
            assert child.get("metadata", {}).get("split_from") == txn["id"]

    async def test_split_correction_logged(self, pool_v2):
        """split_transaction records a correction entry in transaction_corrections."""
        from butlers.tools.finance.transactions import split_transaction

        txn = await _insert_txn_v2(pool_v2, amount=-50.00)
        await split_transaction(
            pool=pool_v2,
            transaction_id=txn["id"],
            splits=[
                {"amount": "30.00", "category": "a"},
                {"amount": "20.00", "category": "b"},
            ],
        )

        correction = await pool_v2.fetchrow(
            """
            SELECT * FROM transaction_corrections
            WHERE transaction_id = $1::uuid AND field_name = 'split'
            """,
            txn["id"],
        )
        assert correction is not None


# ---------------------------------------------------------------------------
# bulk_recategorize: create_rule and is_category_locked
# ---------------------------------------------------------------------------


class TestBulkRecategorizeEnhanced:
    """Tests for bulk_recategorize with create_rule and category lock."""

    async def test_bulk_recategorize_create_rule_upserts_mapping(self, pool_v2):
        """bulk_recategorize with create_rule=True upserts a merchant_mappings row."""
        from butlers.tools.finance.transactions import bulk_recategorize

        await _insert_txn_v2(pool_v2, merchant="RuleTest Corp", category="misc")
        result = await bulk_recategorize(
            pool=pool_v2,
            merchant_pattern="%RuleTest%",
            new_category="services",
            create_rule=True,
        )
        assert result["create_rule"] is True

        # Verify mapping was created.
        mapping = await pool_v2.fetchrow(
            "SELECT * FROM merchant_mappings WHERE raw_pattern = $1",
            "%RuleTest%",
        )
        assert mapping is not None
        assert mapping["category"] == "services"
        assert mapping["normalized_merchant"] == "RuleTest"

    async def test_bulk_recategorize_skips_locked_transactions(self, pool_v2):
        """bulk_recategorize does not update category-locked transactions."""
        from butlers.tools.finance.transactions import bulk_recategorize, update_transaction

        txn = await _insert_txn_v2(pool_v2, merchant="LockedMerchant", category="original")
        # Lock the category by doing a manual update.
        await update_transaction(pool=pool_v2, transaction_id=txn["id"], category="manual_cat")
        # Now the transaction should be category-locked.

        result = await bulk_recategorize(
            pool=pool_v2,
            merchant_pattern="%LockedMerchant%",
            new_category="bulk_override",
        )
        # The locked transaction should not be updated.
        assert result["updated"] == 0

    async def test_bulk_recategorize_create_rule_response_key(self, pool_v2):
        """bulk_recategorize result always includes create_rule key."""
        from butlers.tools.finance.transactions import bulk_recategorize

        result = await bulk_recategorize(
            pool=pool_v2,
            merchant_pattern="%NothingMatches%",
            new_category="test",
        )
        assert "create_rule" in result
        assert result["create_rule"] is False


# ---------------------------------------------------------------------------
# spending_summary: deleted_at IS NULL filter
# ---------------------------------------------------------------------------


class TestSpendingSummaryEnhanced:
    """Tests for spending_summary with deleted_at exclusion."""

    async def test_spending_summary_excludes_deleted(self, pool_v2):
        """spending_summary does not count soft-deleted transactions."""
        from datetime import date

        from butlers.tools.finance.spending import spending_summary
        from butlers.tools.finance.transactions import delete_transaction, record_transaction

        today = _utcnow()
        txn = await record_transaction(
            pool=pool_v2,
            posted_at=today,
            merchant="DeletedSpend",
            amount=-200.00,
            currency="USD",
            category="shopping",
        )
        await delete_transaction(pool=pool_v2, transaction_id=txn["id"])

        result = await spending_summary(
            pool=pool_v2,
            start_date=date(today.year, today.month, today.day),
            end_date=date(today.year, today.month, today.day),
        )
        # The deleted transaction should not appear in the total.
        assert Decimal(result["total_spend"]) == Decimal("0")
