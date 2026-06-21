"""Integration tests for the finance module migrations.

Tests verify schema outcomes (index behavior, constraint enforcement) against
a real PostgreSQL instance provisioned by the test fixtures.

Chain-integrity (file existence, revision metadata, upgrade/downgrade callables)
is covered canonically by tests/config/test_migration_contract.py.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Migration loader helpers (shared by all migration test classes below)
# ---------------------------------------------------------------------------

_FINANCE_MIGRATIONS = Path(__file__).resolve().parents[2] / "roster" / "finance" / "migrations"


def _load_migration(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool: asyncpg.Pool, mod, direction: str) -> None:
    """Capture op.execute() SQL emitted by upgrade()/downgrade() and run it."""
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, direction)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def finance_pool(provisioned_postgres_pool):
    """Provision a fresh database with finance tables and return a pool.

    WARNING: This fixture duplicates the schema from the finance migrations
    (finance_001 through finance_005) to keep tests lightweight and avoid a
    runtime Alembic dependency.  If any migration changes, this fixture
    MUST be updated manually to stay in sync.

    Relevant migration files:
      - roster/finance/migrations/001_finance_tables.py  (finance_001)
      - roster/finance/migrations/002_merchant_mappings_trigram_index.py  (finance_002)
      - roster/finance/migrations/003_merchant_mappings_schema_correction.py  (finance_003)
      - roster/finance/migrations/004_transactions_dedup_constraint.py  (finance_004)
      - roster/finance/migrations/005_add_csv_dedup_index.py  (finance_005)
    """
    async with provisioned_postgres_pool() as pool:
        # We need to run the migrations through Alembic's op interface,
        # but for testing we can directly execute the SQL
        try:
            await pool.execute("""
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
            await pool.execute("""
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
                    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            # Create existing indexes from finance_001
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_accounts_institution
                    ON accounts (institution)
            """)
            await pool.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_institution_type_last_four
                    ON accounts (institution, type, last_four)
                    WHERE last_four IS NOT NULL
            """)
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_posted_at
                    ON transactions (posted_at DESC)
            """)
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_merchant
                    ON transactions (merchant)
            """)
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_category
                    ON transactions (category)
            """)
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_account_id
                    ON transactions (account_id)
            """)
            await pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_transactions_source_message_id
                    ON transactions (source_message_id)
            """)
            await pool.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
                    ON transactions (source_message_id, merchant, amount, posted_at)
                    WHERE source_message_id IS NOT NULL
            """)

            # Create the CSV dedup index from finance_005
            await pool.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_csv_dedup
                    ON transactions (posted_at, amount, merchant, account_id)
                    WHERE source_message_id IS NULL
            """)

        except asyncpg.PostgresError as e:
            pytest.fail(f"Failed to set up test database: {e}")

        yield pool


@pytest.mark.asyncio(loop_scope="session")
class TestCSVDedupIndexIntegration:
    """Integration tests for CSV dedup index creation and behavior."""

    @pytest.mark.integration
    async def test_csv_dedup_index_prevents_duplicate_csv_imports(
        self, finance_pool: asyncpg.Pool
    ) -> None:
        """CSV dedup index prevents duplicate transactions with same
        posted_at, amount, merchant, account_id.
        """
        pool = finance_pool
        # Setup: Insert test account
        account = await pool.fetchrow(
            """
            INSERT INTO accounts (institution, type, name, currency)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """,
            "Bank",
            "checking",
            "Test Account",
            "USD",
        )
        account_id = account["id"]

        # Transaction details
        posted_at = datetime(2026, 3, 1, 10, 30, 0, tzinfo=UTC)
        merchant = "Coffee Shop"
        amount = 5.50
        currency = "USD"

        # First insert should succeed
        result1 = await pool.fetchrow(
            """
            INSERT INTO transactions (
                account_id, posted_at, merchant, amount, currency,
                direction, category, source_message_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """,
            account_id,
            posted_at,
            merchant,
            amount,
            currency,
            "debit",
            "food",
            None,
        )
        assert result1 is not None

        # Second insert with identical CSV fields should fail due to unique constraint
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO transactions (
                    account_id, posted_at, merchant, amount, currency,
                    direction, category, source_message_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                account_id,
                posted_at,
                merchant,
                amount,
                currency,
                "debit",
                "food",
                None,
            )

    @pytest.mark.integration
    async def test_csv_dedup_index_allows_different_merchants(
        self, finance_pool: asyncpg.Pool
    ) -> None:
        """CSV dedup index allows transactions with different merchants."""
        pool = finance_pool
        # Setup: Insert test account
        account = await pool.fetchrow(
            """
            INSERT INTO accounts (institution, type, name, currency)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """,
            "Bank",
            "checking",
            "Test Account",
            "USD",
        )
        account_id = account["id"]

        posted_at = datetime(2026, 3, 1, 10, 30, 0, tzinfo=UTC)
        amount = 5.50
        currency = "USD"

        # Insert two transactions with same amount/date but different merchants
        result1 = await pool.fetchrow(
            """
            INSERT INTO transactions (
                account_id, posted_at, merchant, amount, currency,
                direction, category, source_message_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """,
            account_id,
            posted_at,
            "Coffee Shop",
            amount,
            currency,
            "debit",
            "food",
            None,
        )
        assert result1 is not None

        result2 = await pool.fetchrow(
            """
            INSERT INTO transactions (
                account_id, posted_at, merchant, amount, currency,
                direction, category, source_message_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """,
            account_id,
            posted_at,
            "Gas Station",
            amount,
            currency,
            "debit",
            "fuel",
            None,
        )
        assert result2 is not None
        assert result1["id"] != result2["id"]

    @pytest.mark.integration
    async def test_csv_dedup_index_is_defined_correctly(self, finance_pool: asyncpg.Pool) -> None:
        """Verify that CSV dedup index exists and is properly defined."""
        pool = finance_pool
        # Check that the index exists
        indexes = await pool.fetch("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'transactions'
              AND indexname = 'uq_transactions_csv_dedup'
        """)
        assert len(indexes) == 1, "CSV dedup index should exist"

        # Verify index definition
        index_def = indexes[0]["indexdef"]
        assert "UNIQUE" in index_def, "Index should be UNIQUE"
        assert "posted_at" in index_def, "Index should include posted_at"
        assert "amount" in index_def, "Index should include amount"
        assert "merchant" in index_def, "Index should include merchant"
        assert "account_id" in index_def, "Index should include account_id"
        assert "source_message_id IS NULL" in index_def, "Index should be partial"


# ---------------------------------------------------------------------------
# finance_009 — bills.reconciled_transaction_id
# ---------------------------------------------------------------------------

_MIGRATION_009 = _FINANCE_MIGRATIONS / "009_bills_reconciled_transaction_id.py"
_MIGRATION_011 = _FINANCE_MIGRATIONS / "011_drop_superseded_transaction_dedup_indexes.py"


@pytest.fixture
async def bills_pool(provisioned_postgres_pool):
    """Pool with a minimal finance schema (accounts + bills) for migration 009 tests.

    Sets up the schema at the finance_001 state for the two tables that matter:
    accounts (required by the FK in bills) and bills itself.  No other tables
    are needed to test the additive column migration.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
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
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                payee                  TEXT NOT NULL,
                amount                 NUMERIC(14, 2) NOT NULL,
                currency               CHAR(3) NOT NULL,
                due_date               DATE NOT NULL,
                frequency              TEXT NOT NULL
                                           CHECK (frequency IN (
                                               'one_time', 'weekly', 'monthly',
                                               'quarterly', 'yearly', 'custom'
                                           )),
                status                 TEXT NOT NULL
                                           CHECK (status IN ('pending', 'paid', 'overdue')),
                payment_method         TEXT,
                account_id             UUID REFERENCES accounts(id) ON DELETE SET NULL,
                source_message_id      TEXT,
                statement_period_start DATE,
                statement_period_end   DATE,
                paid_at                TIMESTAMPTZ,
                metadata               JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield pool


@pytest.mark.asyncio(loop_scope="session")
class TestBillsReconciledTransactionIdMigration:
    """Integration tests for finance_009: bills.reconciled_transaction_id (UUID NULL)."""

    @pytest.mark.integration
    async def test_upgrade_adds_nullable_uuid_column_defaulting_null(
        self, bills_pool: asyncpg.Pool
    ) -> None:
        """After upgrade the column is present, nullable, has no explicit DEFAULT, and
        reads as NULL when omitted from an INSERT.
        """
        pool = bills_pool
        mod = _load_migration("finance_009", _MIGRATION_009)
        await _apply(pool, mod, "upgrade")

        # Column metadata.
        col = await pool.fetchrow(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'bills' AND column_name = 'reconciled_transaction_id'"
        )
        assert col is not None, "reconciled_transaction_id column must exist after upgrade"
        assert col["data_type"] == "uuid", "column type must be uuid"
        assert col["is_nullable"] == "YES", "column must be nullable"
        # Nullable UUID with no explicit DEFAULT — column_default should be NULL
        # (the value is implicitly NULL when the column is omitted from an INSERT).
        assert col["column_default"] is None, "no explicit DEFAULT expression expected"

        # Index check: partial index must exist after upgrade.
        indexes = await pool.fetch(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename = 'bills'"
            "   AND indexname = 'idx_bills_reconciled_transaction_id'"
        )
        assert len(indexes) == 1, (
            "Partial index on reconciled_transaction_id must exist after upgrade"
        )

        # Behavioural check: INSERT without specifying the column yields NULL.
        row = await pool.fetchrow(
            """
            INSERT INTO bills (payee, amount, currency, due_date, frequency, status)
            VALUES ('ACME Corp', 99.00, 'USD', '2026-08-01', 'monthly', 'pending')
            RETURNING reconciled_transaction_id
            """
        )
        assert row is not None
        assert row["reconciled_transaction_id"] is None, (
            "reconciled_transaction_id must be NULL when not supplied"
        )

    @pytest.mark.integration
    async def test_downgrade_drops_column(self, bills_pool: asyncpg.Pool) -> None:
        """After upgrade then downgrade the column and its index are absent from the schema."""
        pool = bills_pool
        mod = _load_migration("finance_009", _MIGRATION_009)
        await _apply(pool, mod, "upgrade")
        await _apply(pool, mod, "downgrade")

        col = await pool.fetchrow(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_name = 'bills' AND column_name = 'reconciled_transaction_id'"
        )
        assert col is None, "reconciled_transaction_id column must be absent after downgrade"

        indexes = await pool.fetch(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename = 'bills'"
            "   AND indexname = 'idx_bills_reconciled_transaction_id'"
        )
        assert len(indexes) == 0, (
            "Partial index on reconciled_transaction_id must be absent after downgrade"
        )


@pytest.mark.asyncio(loop_scope="session")
class TestTransactionDedupIndexCleanupMigration:
    """Integration tests for finance_011: superseded transaction dedup index cleanup."""

    @pytest.mark.integration
    async def test_upgrade_drops_legacy_dedup_indexes(self, finance_pool: asyncpg.Pool) -> None:
        """After upgrade only the current tiered dedup indexes should remain authoritative."""
        pool = finance_pool
        await pool.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_composite_dedup
                ON transactions (account_id, posted_at, amount, merchant)
                NULLS NOT DISTINCT
        """)
        assert await pool.fetchval("SELECT to_regclass('uq_transactions_composite_dedup')")
        assert await pool.fetchval("SELECT to_regclass('uq_transactions_csv_dedup')")

        mod = _load_migration("finance_011", _MIGRATION_011)
        await _apply(pool, mod, "upgrade")

        assert await pool.fetchval("SELECT to_regclass('uq_transactions_composite_dedup')") is None
        assert await pool.fetchval("SELECT to_regclass('uq_transactions_csv_dedup')") is None

        posted_at = datetime(2026, 6, 21, 8, 0, tzinfo=UTC)
        for _ in range(2):
            await pool.execute(
                """
                INSERT INTO transactions (
                    posted_at, merchant, amount, currency, direction, category
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                posted_at,
                "Manual Merchant",
                10.00,
                "USD",
                "debit",
                "shopping",
            )

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE merchant = 'Manual Merchant'"
        )
        assert count == 2
