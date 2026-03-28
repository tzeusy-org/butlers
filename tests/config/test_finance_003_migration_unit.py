"""Unit tests for the finance_003 transactions dedup constraint migration.

Validates migration metadata, upgrade SQL (index name, columns, NULLS NOT DISTINCT
clause), and downgrade SQL — all without requiring a live database connection.

Issue: bu-793z
PR review finding: _insert_batch used ON CONFLICT DO NOTHING without a conflict
target, making the fallback dedup guard ineffective.  This migration adds the
missing unique index so the conflict target is resolvable.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "003_transactions_dedup_constraint.py"


def _finance_migration_dir() -> Path:
    """Return the finance butler migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("finance")
    assert chain_dir is not None, "Finance chain should exist"
    return chain_dir


def _load_migration():
    """Load the finance_003 migration module."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Migration file should exist: {migration_path}"

    spec = importlib.util.spec_from_file_location("finance_003_migration", migration_path)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


def test_finance_003_file_exists():
    """Migration file must exist in the finance chain directory."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Expected {migration_path}"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    """Verify Alembic revision identifiers and callables."""

    def test_revision_id(self):
        assert _load_migration().revision == "finance_003"

    def test_down_revision_is_finance_002(self):
        """finance_003 must chain from finance_002 (intelligence tables migration)."""
        assert _load_migration().down_revision == "finance_002"

    def test_branch_labels_is_none(self):
        """finance_003 extends the existing finance branch; no new branch label."""
        mod = _load_migration()
        bl = getattr(mod, "branch_labels", None)
        assert bl is None

    def test_depends_on_is_none(self):
        assert getattr(_load_migration(), "depends_on", None) is None

    def test_upgrade_callable(self):
        assert callable(_load_migration().upgrade)

    def test_downgrade_callable(self):
        assert callable(_load_migration().downgrade)


# ---------------------------------------------------------------------------
# upgrade() — unique index creation
# ---------------------------------------------------------------------------


class TestUpgradeCreatesDeduplicateIndex:
    """upgrade() must create a unique index with the correct shape."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_unique_index(self):
        """Must use CREATE UNIQUE INDEX."""
        assert "CREATE UNIQUE INDEX" in self._src()

    def test_uses_if_not_exists(self):
        """Index creation must be idempotent (IF NOT EXISTS)."""
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in self._src()

    def test_index_name_is_uq_transactions_composite_dedup(self):
        """Index must use the canonical name for the composite dedup constraint."""
        assert "uq_transactions_composite_dedup" in self._src()

    def test_index_targets_transactions_table(self):
        assert "ON transactions" in self._src()

    def test_index_includes_account_id(self):
        """account_id is the nullable FK — must be part of the composite key."""
        assert "account_id" in self._src()

    def test_index_includes_posted_at(self):
        """posted_at is always non-null; it forms the time dimension of the key."""
        assert "posted_at" in self._src()

    def test_index_includes_amount(self):
        """amount disambiguates same-merchant charges on the same day."""
        assert "amount" in self._src()

    def test_index_includes_merchant(self):
        """merchant name is the business identity component of the key."""
        assert "merchant" in self._src()

    def test_index_columns_in_correct_order(self):
        """Column order: account_id, posted_at, amount, merchant."""
        src = self._src()
        account_pos = src.index("account_id")
        posted_pos = src.index("posted_at")
        amount_pos = src.index("amount")
        merchant_pos = src.index("merchant")
        assert account_pos < posted_pos, "account_id must precede posted_at"
        assert posted_pos < amount_pos, "posted_at must precede amount"
        assert amount_pos < merchant_pos, "amount must precede merchant"

    def test_nulls_not_distinct_clause(self):
        """NULLS NOT DISTINCT is required so NULL account_id rows deduplicate.

        Without this clause PostgreSQL treats each NULL as distinct from all
        other NULLs, defeating the dedup for unlinked transactions.
        """
        assert "NULLS NOT DISTINCT" in self._src()

    def test_no_partial_where_clause(self):
        """The index must cover all rows, not just a subset via WHERE.

        A partial index on e.g. 'WHERE account_id IS NOT NULL' would leave
        unlinked transactions un-protected.  NULLS NOT DISTINCT handles
        the NULL case without needing a separate partial index.
        """
        assert "WHERE" not in self._src()

    def test_no_column_level_type_cast(self):
        """Index columns must not contain unexpected casts that alter semantics."""
        src = self._src()
        # ::uuid cast on the column itself would change what the index stores
        assert "account_id::uuid" not in src


# ---------------------------------------------------------------------------
# downgrade() — index removal
# ---------------------------------------------------------------------------


class TestDowngradeRemovesIndex:
    """downgrade() must cleanly remove the unique index."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_drops_index(self):
        assert "DROP INDEX" in self._src()

    def test_uses_if_exists(self):
        """Drop must be idempotent (IF EXISTS)."""
        assert "DROP INDEX IF EXISTS" in self._src()

    def test_drops_correct_index(self):
        assert "uq_transactions_composite_dedup" in self._src()

    def test_no_table_drop(self):
        """downgrade() must not drop the transactions table itself."""
        assert "DROP TABLE" not in self._src()


# ---------------------------------------------------------------------------
# Structural / cross-cutting checks
# ---------------------------------------------------------------------------


class TestMigrationStructure:
    """Cross-cutting structural checks."""

    def test_migration_is_importable(self):
        """Migration module must be importable without errors."""
        mod = _load_migration()
        assert mod is not None

    def test_upgrade_and_downgrade_are_inverses(self):
        """downgrade() must undo exactly what upgrade() created.

        Both must reference the same index name to form a symmetric pair.
        """
        upgrade_src = inspect.getsource(_load_migration().upgrade)
        downgrade_src = inspect.getsource(_load_migration().downgrade)
        assert "uq_transactions_composite_dedup" in upgrade_src
        assert "uq_transactions_composite_dedup" in downgrade_src

    def test_upgrade_does_not_create_tables(self):
        """This migration only adds an index; it must not create tables."""
        assert "CREATE TABLE" not in inspect.getsource(_load_migration().upgrade)

    def test_uses_op_execute(self):
        """Migration must use op.execute() for DDL (project convention)."""
        assert "op.execute" in inspect.getsource(_load_migration().upgrade)
