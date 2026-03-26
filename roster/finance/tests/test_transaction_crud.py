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

        await merge_duplicates(pool=pool, keep_id=keep["id"], discard_id=discard["id"])

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

        result = await merge_duplicates(pool=pool, keep_id=keep["id"], discard_id=discard["id"])
        assert result["id"] == keep["id"]
        # keep's metadata is preserved; discard's non-conflicting keys are merged in
        assert result["metadata"]["source"] == "keep"
        assert result["metadata"]["extra"] == "from_discard"

    async def test_merge_keep_wins_on_metadata_conflict(self, pool):
        """Keep's metadata values win when keys conflict."""
        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool, metadata={"key": "keep_val"})
        discard = await _insert_txn(pool, metadata={"key": "discard_val"})

        result = await merge_duplicates(pool=pool, keep_id=keep["id"], discard_id=discard["id"])
        assert result["metadata"]["key"] == "keep_val"

    async def test_merge_same_id_returns_error(self, pool):
        """merge_duplicates with keep_id == discard_id returns error."""
        from butlers.tools.finance.transactions import merge_duplicates

        txn = await _insert_txn(pool)
        result = await merge_duplicates(pool=pool, keep_id=txn["id"], discard_id=txn["id"])
        assert "error" in result

    async def test_merge_keep_not_found(self, pool):
        """merge_duplicates with unknown keep_id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        discard = await _insert_txn(pool)
        result = await merge_duplicates(
            pool=pool,
            keep_id=str(uuid.uuid4()),
            discard_id=discard["id"],
        )
        assert result["error"] == "keep_transaction_not_found"

    async def test_merge_discard_not_found(self, pool):
        """merge_duplicates with unknown discard_id returns error."""
        import uuid

        from butlers.tools.finance.transactions import merge_duplicates

        keep = await _insert_txn(pool)
        result = await merge_duplicates(
            pool=pool,
            keep_id=keep["id"],
            discard_id=str(uuid.uuid4()),
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
