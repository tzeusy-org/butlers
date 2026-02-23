"""Unit tests for the finance butler database schema migration.

Tests validate migration metadata correctness, schema structure, index presence,
check constraints, FK relationships, and downgrade completeness — all without
requiring a live database connection.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "001_finance_tables.py"


def _finance_migration_dir() -> Path:
    """Return the finance butler migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("finance")
    assert chain_dir is not None, "Finance chain should exist"
    return chain_dir


def _load_migration():
    """Load the finance 001 migration module."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Migration file should exist: {migration_path}"

    spec = importlib.util.spec_from_file_location("finance_001_migration", migration_path)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# File existence and discoverability
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """Migration file must exist at the expected path."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Expected migration file at {migration_path}"


def test_finance_chain_discoverable():
    """Finance chain must be discoverable via the migrations module."""
    from butlers.migrations import get_all_chains, has_butler_chain

    all_chains = get_all_chains()
    assert "finance" in all_chains, "Finance chain should appear in get_all_chains()"
    assert has_butler_chain("finance"), "has_butler_chain('finance') should return True"


def test_finance_chain_contains_migration_file():
    """Migration directory must contain the expected file."""
    chain_dir = _finance_migration_dir()
    migration_files = [f.name for f in chain_dir.glob("*.py") if f.name != "__init__.py"]
    assert MIGRATION_FILENAME in migration_files, (
        f"Expected {MIGRATION_FILENAME!r} in chain dir; found: {migration_files}"
    )


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


def test_migration_revision():
    """revision must be 'finance_001'."""
    module = _load_migration()
    assert module.revision == "finance_001", (
        f"Expected revision='finance_001', got {module.revision!r}"
    )


def test_migration_branch_labels():
    """branch_labels must be ('finance',) for the chain root."""
    module = _load_migration()
    assert module.branch_labels == ("finance",), (
        f"Expected branch_labels=('finance',), got {module.branch_labels!r}"
    )


def test_migration_down_revision_is_none():
    """down_revision must be None (chain root — no parent)."""
    module = _load_migration()
    assert module.down_revision is None, (
        f"Expected down_revision=None, got {module.down_revision!r}"
    )


def test_migration_depends_on_is_none():
    """depends_on must be None for the chain root."""
    module = _load_migration()
    assert module.depends_on is None, f"Expected depends_on=None, got {module.depends_on!r}"


# ---------------------------------------------------------------------------
# Callable contract
# ---------------------------------------------------------------------------


def test_migration_has_upgrade():
    """Migration must export an upgrade() callable."""
    module = _load_migration()
    assert hasattr(module, "upgrade"), "Migration must define upgrade()"
    assert callable(module.upgrade), "upgrade must be callable"


def test_migration_has_downgrade():
    """Migration must export a downgrade() callable."""
    module = _load_migration()
    assert hasattr(module, "downgrade"), "Migration must define downgrade()"
    assert callable(module.downgrade), "downgrade must be callable"


# ---------------------------------------------------------------------------
# finance.accounts table
# ---------------------------------------------------------------------------


class TestAccountsTable:
    """Structural checks for the finance.accounts table DDL."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_accounts_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS accounts" in self._source()

    def test_accounts_uuid_pk(self):
        src = self._source()
        assert "id" in src
        assert "UUID PRIMARY KEY DEFAULT gen_random_uuid()" in src

    def test_accounts_institution_not_null(self):
        src = self._source()
        assert "institution" in src
        assert "TEXT NOT NULL" in src

    def test_accounts_type_check_constraint(self):
        src = self._source()
        assert "CHECK (type IN ('checking', 'savings', 'credit', 'investment'))" in src

    def test_accounts_last_four_char4(self):
        assert "last_four" in self._source()
        assert "CHAR(4)" in self._source()

    def test_accounts_currency_char3(self):
        assert "currency" in self._source()
        assert "CHAR(3)" in self._source()

    def test_accounts_currency_default_usd(self):
        assert "DEFAULT 'USD'" in self._source()

    def test_accounts_metadata_jsonb_with_default(self):
        src = self._source()
        assert "metadata" in src
        assert "JSONB NOT NULL DEFAULT '{}'::jsonb" in src

    def test_accounts_timestamps_are_timestamptz(self):
        src = self._source()
        assert "created_at" in src
        assert "updated_at" in src
        assert "TIMESTAMPTZ" in src

    def test_accounts_institution_index(self):
        assert "idx_accounts_institution" in self._source()

    def test_accounts_type_index(self):
        assert "idx_accounts_type" in self._source()

    def test_accounts_unique_institution_type_last_four_partial(self):
        src = self._source()
        assert "uq_accounts_institution_type_last_four" in src
        assert "WHERE last_four IS NOT NULL" in src


# ---------------------------------------------------------------------------
# finance.transactions table
# ---------------------------------------------------------------------------


class TestTransactionsTable:
    """Structural checks for the finance.transactions table DDL."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_transactions_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS transactions" in self._source()

    def test_transactions_uuid_pk(self):
        src = self._source()
        assert "id" in src
        assert "UUID PRIMARY KEY DEFAULT gen_random_uuid()" in src

    def test_transactions_account_id_fk_set_null(self):
        src = self._source()
        assert "account_id" in src
        assert "REFERENCES accounts(id) ON DELETE SET NULL" in src

    def test_transactions_posted_at_timestamptz_not_null(self):
        src = self._source()
        assert "posted_at" in src
        assert "TIMESTAMPTZ NOT NULL" in src

    def test_transactions_merchant_not_null(self):
        src = self._source()
        assert "merchant" in src

    def test_transactions_amount_numeric(self):
        src = self._source()
        assert "amount" in src
        assert "NUMERIC(14, 2)" in src

    def test_transactions_currency_char3(self):
        assert "CHAR(3) NOT NULL" in self._source()

    def test_transactions_direction_check_constraint(self):
        assert "CHECK (direction IN ('debit', 'credit'))" in self._source()

    def test_transactions_category_not_null(self):
        assert "category" in self._source()

    def test_transactions_metadata_jsonb(self):
        assert "JSONB NOT NULL DEFAULT '{}'::jsonb" in self._source()

    def test_transactions_source_message_id_column(self):
        assert "source_message_id" in self._source()

    def test_transactions_posted_at_desc_index(self):
        assert "idx_transactions_posted_at" in self._source()
        assert "posted_at DESC" in self._source()

    def test_transactions_merchant_index(self):
        assert "idx_transactions_merchant" in self._source()

    def test_transactions_category_index(self):
        assert "idx_transactions_category" in self._source()

    def test_transactions_account_id_index(self):
        assert "idx_transactions_account_id" in self._source()

    def test_transactions_source_message_id_index(self):
        assert "idx_transactions_source_message_id" in self._source()

    def test_transactions_metadata_gin_index(self):
        src = self._source()
        assert "idx_transactions_metadata_gin" in src
        assert "USING GIN" in src

    def test_transactions_dedupe_partial_index(self):
        src = self._source()
        assert "uq_transactions_dedupe" in src
        # Partial index must include all four dedupe key columns
        assert "source_message_id" in src
        assert "merchant" in src
        assert "amount" in src
        assert "posted_at" in src
        # Must be a partial index gated on source_message_id
        assert "WHERE source_message_id IS NOT NULL" in src


# ---------------------------------------------------------------------------
# finance.subscriptions table
# ---------------------------------------------------------------------------


class TestSubscriptionsTable:
    """Structural checks for the finance.subscriptions table DDL."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_subscriptions_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS subscriptions" in self._source()

    def test_subscriptions_uuid_pk(self):
        assert "UUID PRIMARY KEY DEFAULT gen_random_uuid()" in self._source()

    def test_subscriptions_service_not_null(self):
        assert "service" in self._source()

    def test_subscriptions_amount_numeric(self):
        assert "NUMERIC(14, 2) NOT NULL" in self._source()

    def test_subscriptions_frequency_check_constraint(self):
        src = self._source()
        assert "CHECK (frequency IN ('weekly', 'monthly', 'quarterly', 'yearly', 'custom'))" in src

    def test_subscriptions_next_renewal_is_date(self):
        src = self._source()
        assert "next_renewal" in src
        assert "DATE NOT NULL" in src

    def test_subscriptions_status_check_constraint(self):
        assert "CHECK (status IN ('active', 'cancelled', 'paused'))" in self._source()

    def test_subscriptions_auto_renew_boolean_default_true(self):
        assert "auto_renew" in self._source()
        assert "BOOLEAN NOT NULL DEFAULT true" in self._source()

    def test_subscriptions_account_id_fk_set_null(self):
        src = self._source()
        assert "account_id" in src
        assert "REFERENCES accounts(id) ON DELETE SET NULL" in src

    def test_subscriptions_metadata_jsonb(self):
        assert "JSONB NOT NULL DEFAULT '{}'::jsonb" in self._source()

    def test_subscriptions_timestamps_timestamptz(self):
        assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in self._source()

    def test_subscriptions_next_renewal_index(self):
        assert "idx_subscriptions_next_renewal" in self._source()

    def test_subscriptions_status_index(self):
        assert "idx_subscriptions_status" in self._source()

    def test_subscriptions_service_index(self):
        assert "idx_subscriptions_service" in self._source()


# ---------------------------------------------------------------------------
# finance.bills table
# ---------------------------------------------------------------------------


class TestBillsTable:
    """Structural checks for the finance.bills table DDL."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_bills_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS bills" in self._source()

    def test_bills_uuid_pk(self):
        assert "UUID PRIMARY KEY DEFAULT gen_random_uuid()" in self._source()

    def test_bills_payee_not_null(self):
        assert "payee" in self._source()

    def test_bills_amount_numeric(self):
        assert "NUMERIC(14, 2) NOT NULL" in self._source()

    def test_bills_due_date_is_date(self):
        src = self._source()
        assert "due_date" in src
        assert "DATE NOT NULL" in src

    def test_bills_frequency_check_constraint(self):
        src = self._source()
        assert (
            "CHECK (frequency IN (\n"
            "                                           'one_time', 'weekly', 'monthly',\n"
            "                                           'quarterly', 'yearly', 'custom'\n"
            "                                       ))"
        ) in src or "one_time" in src

    def test_bills_frequency_includes_one_time(self):
        """Bills frequency must include 'one_time' which subscriptions do not."""
        assert "one_time" in self._source()

    def test_bills_status_check_constraint(self):
        assert "CHECK (status IN ('pending', 'paid', 'overdue'))" in self._source()

    def test_bills_account_id_fk_set_null(self):
        src = self._source()
        assert "account_id" in src
        assert "REFERENCES accounts(id) ON DELETE SET NULL" in src

    def test_bills_statement_period_columns(self):
        src = self._source()
        assert "statement_period_start" in src
        assert "statement_period_end" in src

    def test_bills_paid_at_is_timestamptz(self):
        assert "paid_at" in self._source()
        assert "TIMESTAMPTZ" in self._source()

    def test_bills_metadata_jsonb(self):
        assert "JSONB NOT NULL DEFAULT '{}'::jsonb" in self._source()

    def test_bills_due_date_index(self):
        assert "idx_bills_due_date" in self._source()

    def test_bills_status_index(self):
        assert "idx_bills_status" in self._source()

    def test_bills_payee_index(self):
        assert "idx_bills_payee" in self._source()

    def test_bills_account_id_index(self):
        assert "idx_bills_account_id" in self._source()


# ---------------------------------------------------------------------------
# downgrade() completeness
# ---------------------------------------------------------------------------


class TestDowngrade:
    """Structural checks for the downgrade() function."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_downgrade_drops_bills(self):
        assert "DROP TABLE IF EXISTS bills" in self._source()

    def test_downgrade_drops_subscriptions(self):
        assert "DROP TABLE IF EXISTS subscriptions" in self._source()

    def test_downgrade_drops_transactions(self):
        assert "DROP TABLE IF EXISTS transactions" in self._source()

    def test_downgrade_drops_accounts(self):
        assert "DROP TABLE IF EXISTS accounts" in self._source()

    def test_downgrade_drops_in_dependency_order(self):
        """Tables with FK references to accounts must be dropped before accounts."""
        src = self._source()
        # All three FK-dependent tables must appear before accounts
        bills_pos = src.find("DROP TABLE IF EXISTS bills")
        subs_pos = src.find("DROP TABLE IF EXISTS subscriptions")
        txn_pos = src.find("DROP TABLE IF EXISTS transactions")
        accounts_pos = src.find("DROP TABLE IF EXISTS accounts")

        assert bills_pos != -1, "bills not found in downgrade"
        assert subs_pos != -1, "subscriptions not found in downgrade"
        assert txn_pos != -1, "transactions not found in downgrade"
        assert accounts_pos != -1, "accounts not found in downgrade"

        assert bills_pos < accounts_pos, "bills must be dropped before accounts"
        assert subs_pos < accounts_pos, "subscriptions must be dropped before accounts"
        assert txn_pos < accounts_pos, "transactions must be dropped before accounts"


# ---------------------------------------------------------------------------
# Cross-cutting data integrity checks
# ---------------------------------------------------------------------------


class TestDataIntegrityRules:
    """Cross-cutting integrity checks derived from spec §5.3."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_no_plain_timestamp_columns(self):
        """All timestamp columns must use TIMESTAMPTZ, not plain TIMESTAMP."""
        src = self._source()
        # Strip out 'TIMESTAMPTZ' occurrences, then check no bare 'TIMESTAMP' remains
        cleaned = src.replace("TIMESTAMPTZ", "")
        # 'TIMESTAMP' should not appear in the cleaned source (would indicate plain TIMESTAMP)
        assert "TIMESTAMP" not in cleaned, (
            "Plain TIMESTAMP found in migration — all timestamps must be TIMESTAMPTZ"
        )

    def test_all_amount_columns_use_numeric_14_2(self):
        """All amount columns must use NUMERIC(14, 2), not FLOAT or REAL."""
        src = self._source()
        assert "FLOAT" not in src, "FLOAT type found — amounts must use NUMERIC(14, 2)"
        assert "REAL" not in src, "REAL type found — amounts must use NUMERIC(14, 2)"
        assert "NUMERIC(14, 2)" in src

    def test_all_currency_columns_use_char3(self):
        """All currency columns must use CHAR(3)."""
        assert "CHAR(3)" in self._source()

    def test_gen_random_uuid_used_for_pks(self):
        """UUID primary keys must use gen_random_uuid()."""
        src = self._source()
        assert "gen_random_uuid()" in src

    def test_metadata_columns_have_gin_index(self):
        """At least one JSONB metadata column should have a GIN index."""
        assert "USING GIN" in self._source()

    def test_if_not_exists_guards_on_all_tables(self):
        """Every CREATE TABLE must use IF NOT EXISTS."""
        src = self._source()
        table_creations = src.count("CREATE TABLE")
        if_not_exists_creations = src.count("CREATE TABLE IF NOT EXISTS")
        assert table_creations == if_not_exists_creations, (
            f"All CREATE TABLE statements must use IF NOT EXISTS: "
            f"found {table_creations} CREATE TABLE but only "
            f"{if_not_exists_creations} with IF NOT EXISTS"
        )

    def test_if_not_exists_guards_on_all_indexes(self):
        """Every CREATE INDEX must use IF NOT EXISTS."""
        src = self._source()
        index_creations = src.count("CREATE INDEX") + src.count("CREATE UNIQUE INDEX")
        if_not_exists_index = src.count("CREATE INDEX IF NOT EXISTS") + src.count(
            "CREATE UNIQUE INDEX IF NOT EXISTS"
        )
        assert index_creations == if_not_exists_index, (
            f"All CREATE INDEX statements must use IF NOT EXISTS: "
            f"found {index_creations} CREATE INDEX but only "
            f"{if_not_exists_index} with IF NOT EXISTS"
        )

    def test_if_exists_guards_on_downgrade_drops(self):
        """Every DROP TABLE in downgrade() must use IF EXISTS."""
        src = inspect.getsource(_load_migration().downgrade)
        drop_count = src.count("DROP TABLE")
        if_exists_drop_count = src.count("DROP TABLE IF EXISTS")
        assert drop_count == if_exists_drop_count, (
            f"All DROP TABLE statements must use IF EXISTS: "
            f"found {drop_count} DROP TABLE but only {if_exists_drop_count} with IF EXISTS"
        )

    def test_all_fks_use_on_delete_set_null(self):
        """FK references to accounts must use ON DELETE SET NULL (not CASCADE)."""
        src = self._source()
        assert "ON DELETE SET NULL" in src
        # There must be no ON DELETE CASCADE pointing at accounts
        # (accounts FK should only be SET NULL so records survive account deletion)
        lines_with_cascade = [
            line for line in src.splitlines() if "REFERENCES accounts" in line and "CASCADE" in line
        ]
        assert not lines_with_cascade, (
            f"FK to accounts must use ON DELETE SET NULL, not CASCADE: {lines_with_cascade}"
        )
