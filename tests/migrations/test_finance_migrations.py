"""Tests for the finance module migrations."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.unit

# Find the finance module migrations relative to this test file
# tests/migrations/test_finance_migrations.py -> roster/finance/migrations/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION_DIR = REPO_ROOT / "roster" / "finance" / "migrations"


def _load_migration(filename: str, module_name: str):
    """Load a migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFinanceMigrationFileLayout:
    """Test that migration files exist and are properly structured."""

    def test_finance_001_migration_file_exists(self) -> None:
        """The initial finance migration file exists on disk."""
        migration_file = MIGRATION_DIR / "001_finance_tables.py"
        assert migration_file.exists(), f"Migration file not found at {migration_file}"

    def test_finance_002_migration_file_exists(self) -> None:
        """The merchant mappings trigram migration file exists on disk."""
        migration_file = MIGRATION_DIR / "002_merchant_mappings_trigram_index.py"
        assert migration_file.exists(), f"Migration file not found at {migration_file}"

    def test_finance_005_migration_file_exists(self) -> None:
        """The CSV dedup migration file exists on disk."""
        migration_file = MIGRATION_DIR / "005_add_csv_dedup_index.py"
        assert migration_file.exists(), f"Migration file not found at {migration_file}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestFinance005RevisionMetadata:
    """Test revision metadata for finance_005 migration."""

    def test_revision_identifiers(self) -> None:
        """The migration has correct revision metadata."""
        mod = _load_migration("005_add_csv_dedup_index.py", "finance_005_migration")
        assert mod.revision == "finance_005"
        assert mod.down_revision == "finance_004"
        assert mod.branch_labels is None
        assert mod.depends_on is None


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


class TestCSVDedupIndexIntegration:
    """Integration tests for CSV dedup index creation and behavior."""

    @pytest.mark.integration
    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
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
        assert "WHERE source_message_id IS NULL" in index_def, "Index should be partial"
