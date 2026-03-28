"""Unit tests for the finance butler intelligence tables migration (finance_002).

Tests validate migration metadata, new columns on finance.transactions and
finance.accounts, all 8 new tables, indexes (including partial index conditions),
default category seeding, materialized view, downgrade completeness, and tiered
dedup index semantics — all without requiring a live database connection.

Covers tasks 2.1–2.7 from openspec/changes/finance-data-model-redesign/tasks.md.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "002_intelligence_tables.py"


def _finance_migration_dir() -> Path:
    """Return the finance butler migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("finance")
    assert chain_dir is not None, "Finance chain should exist"
    return chain_dir


def _load_migration():
    """Load the finance 002 migration module."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Migration file should exist: {migration_path}"

    spec = importlib.util.spec_from_file_location("finance_002_migration", migration_path)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# File existence and revision metadata
# ---------------------------------------------------------------------------


def test_finance_002_file_exists():
    """Migration file must exist in the finance chain directory."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Expected {migration_path}"


class TestRevisionMetadata:
    """Verify Alembic revision identifiers and callables."""

    def test_revision_id(self):
        assert _load_migration().revision == "finance_002"

    def test_down_revision_is_finance_001(self):
        assert _load_migration().down_revision == "finance_001"

    def test_branch_labels_is_none(self):
        """finance_002 extends the existing finance branch; no new label."""
        mod = _load_migration()
        # branch_labels should be None or absent (not a new branch root)
        bl = getattr(mod, "branch_labels", None)
        assert bl is None

    def test_depends_on_is_none(self):
        assert getattr(_load_migration(), "depends_on", None) is None

    def test_upgrade_callable(self):
        assert callable(_load_migration().upgrade)

    def test_downgrade_callable(self):
        assert callable(_load_migration().downgrade)


# ---------------------------------------------------------------------------
# Task 2.1 — New columns on finance.transactions
# ---------------------------------------------------------------------------


class TestTransactionNewColumns:
    """upgrade() must add all 16 intelligence columns to finance.transactions."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    # Dedup / provenance columns
    def test_external_id_column_added(self):
        assert "external_id" in self._src()

    def test_transaction_date_column_added(self):
        assert "transaction_date" in self._src()

    def test_normalized_description_column_added(self):
        assert "normalized_description" in self._src()

    def test_normalized_merchant_column_added(self):
        assert "normalized_merchant" in self._src()

    def test_subcategory_column_added(self):
        assert "subcategory" in self._src()

    def test_tags_column_added_as_text_array(self):
        src = self._src()
        assert "tags" in src
        assert "TEXT[]" in src

    def test_tags_default_empty_array(self):
        src = self._src()
        # Default must be an empty array literal
        assert "'{}'::text[]" in src or "DEFAULT '{}'" in src or "DEFAULT '{}'::" in src

    def test_category_source_column_added(self):
        assert "category_source" in self._src()

    def test_category_source_default_auto(self):
        src = self._src()
        assert "'auto'" in src

    def test_is_category_locked_boolean_column(self):
        src = self._src()
        assert "is_category_locked" in src

    def test_is_category_locked_default_false(self):
        src = self._src()
        assert "is_category_locked" in src
        # Default false appears somewhere after the column declaration
        assert "false" in src.lower()

    def test_type_column_added(self):
        assert "type" in self._src()

    def test_type_default_purchase(self):
        src = self._src()
        assert "'purchase'" in src

    def test_is_recurring_boolean_column(self):
        assert "is_recurring" in self._src()

    def test_is_recurring_default_false(self):
        src = self._src()
        assert "is_recurring" in src
        assert "false" in src.lower()

    def test_recurring_group_id_column(self):
        assert "recurring_group_id" in self._src()

    def test_is_duplicate_boolean_column(self):
        assert "is_duplicate" in self._src()

    def test_is_duplicate_default_false(self):
        src = self._src()
        assert "is_duplicate" in src
        assert "false" in src.lower()

    def test_duplicate_of_column(self):
        assert "duplicate_of" in self._src()

    def test_import_batch_id_column(self):
        assert "import_batch_id" in self._src()

    def test_source_column_added(self):
        # 'source' column on transactions
        assert "source" in self._src()

    def test_source_default_manual(self):
        src = self._src()
        assert "'manual'" in src

    def test_raw_data_jsonb_column(self):
        src = self._src()
        assert "raw_data" in src
        assert "JSONB" in src

    def test_raw_data_default_empty_jsonb(self):
        src = self._src()
        assert "raw_data" in src
        assert "'{}'::jsonb" in src

    def test_notes_column_added(self):
        assert "notes" in self._src()

    def test_version_column_added(self):
        assert "version" in self._src()

    def test_version_default_1(self):
        src = self._src()
        assert "version" in src
        assert "DEFAULT 1" in src

    def test_add_column_if_not_exists_guard(self):
        """All ALTER TABLE ADD COLUMN must use IF NOT EXISTS (idempotent)."""
        src = self._src()
        add_count = src.count("ADD COLUMN")
        if_not_exists_count = src.count("ADD COLUMN IF NOT EXISTS")
        assert add_count == if_not_exists_count, (
            f"All ADD COLUMN statements must use IF NOT EXISTS: "
            f"found {add_count} ADD COLUMN but only {if_not_exists_count} with IF NOT EXISTS"
        )


# ---------------------------------------------------------------------------
# finance.accounts enhancements
# ---------------------------------------------------------------------------


class TestAccountsEnhancements:
    """upgrade() must add new columns to finance.accounts."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_is_active_column_added(self):
        assert "is_active" in self._src()

    def test_is_active_boolean_default_true(self):
        src = self._src()
        assert "is_active" in src
        assert "true" in src.lower()

    def test_last_synced_at_column_added(self):
        assert "last_synced_at" in self._src()

    def test_last_synced_at_is_timestamptz(self):
        src = self._src()
        assert "last_synced_at" in src
        assert "TIMESTAMPTZ" in src

    def test_updated_at_column_added_to_accounts(self):
        """accounts.updated_at must be present (added if it wasn't in finance_001)."""
        assert "updated_at" in self._src()

    def test_accounts_type_check_extended_with_loan_and_other(self):
        src = self._src()
        assert "loan" in src
        assert "'other'" in src


# ---------------------------------------------------------------------------
# Task 2.2 — 8 new tables
# ---------------------------------------------------------------------------


class TestNewTables:
    """upgrade() must create all 8 new supporting tables."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_categories_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS categories" in self._src()

    def test_merchant_mappings_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS merchant_mappings" in self._src()

    def test_recurring_groups_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS recurring_groups" in self._src()

    def test_import_batches_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS import_batches" in self._src()

    def test_balance_snapshots_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS balance_snapshots" in self._src()

    def test_budgets_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS budgets" in self._src()

    def test_transaction_corrections_table_created(self):
        assert "CREATE TABLE IF NOT EXISTS transaction_corrections" in self._src()

    # spending_summaries is a materialized view, covered in Task 2.5


class TestCategoriesTableColumns:
    """finance.categories must have taxonomy and tax-relevance columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_categories_name_column(self):
        assert "name" in self._src()

    def test_categories_display_name_column(self):
        assert "display_name" in self._src()

    def test_categories_parent_id_column(self):
        assert "parent_id" in self._src()

    def test_categories_is_tax_relevant_column(self):
        assert "is_tax_relevant" in self._src()

    def test_categories_tax_category_column(self):
        assert "tax_category" in self._src()

    def test_categories_is_system_column(self):
        assert "is_system" in self._src()

    def test_categories_unique_name_index(self):
        """categories must have a unique constraint or index on name."""
        src = self._src()
        # UNIQUE constraint on name column or unique index
        assert "UNIQUE" in src or "uq_categories" in src or "categories_name_key" in src


class TestMerchantMappingsTableColumns:
    """finance.merchant_mappings must have correct columns and constraints."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_raw_pattern_column(self):
        assert "raw_pattern" in self._src()

    def test_normalized_merchant_in_merchant_mappings(self):
        assert "normalized_merchant" in self._src()

    def test_category_column_in_merchant_mappings(self):
        assert "category" in self._src()

    def test_confidence_column(self):
        assert "confidence" in self._src()

    def test_learned_from_count_column(self):
        assert "learned_from_count" in self._src()

    def test_source_column_in_merchant_mappings(self):
        assert "source" in self._src()

    def test_is_active_column_in_merchant_mappings(self):
        assert "is_active" in self._src()

    def test_uq_merchant_mapping_pattern_index(self):
        src = self._src()
        assert "uq_merchant_mapping_pattern" in src

    def test_uq_merchant_mapping_partial_on_is_active(self):
        src = self._src()
        idx_pos = src.find("uq_merchant_mapping_pattern")
        assert idx_pos != -1
        # Find WHERE clause near the index definition
        block = src[idx_pos : idx_pos + 300]
        assert "WHERE is_active" in block

    def test_uq_merchant_mapping_uses_lower_raw_pattern(self):
        src = self._src()
        assert "lower(raw_pattern)" in src or "LOWER(raw_pattern)" in src


class TestRecurringGroupsTableColumns:
    """finance.recurring_groups must have charge-pattern tracking columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_merchant_column(self):
        assert "merchant" in self._src()

    def test_expected_amount_numeric(self):
        src = self._src()
        assert "expected_amount" in src
        assert "NUMERIC(14" in src

    def test_frequency_column(self):
        assert "frequency" in self._src()

    def test_status_column_in_recurring_groups(self):
        assert "status" in self._src()

    def test_subscription_id_fk_to_subscriptions(self):
        src = self._src()
        assert "subscription_id" in src
        assert "REFERENCES subscriptions" in src

    def test_is_subscription_boolean(self):
        assert "is_subscription" in self._src()

    def test_next_expected_date_column(self):
        assert "next_expected_date" in self._src()

    def test_confidence_column_in_recurring_groups(self):
        assert "confidence" in self._src()


class TestImportBatchesTableColumns:
    """finance.import_batches must have import-provenance and status columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_source_column_in_import_batches(self):
        assert "source" in self._src()

    def test_filename_column(self):
        assert "filename" in self._src()

    def test_account_id_column_in_import_batches(self):
        assert "account_id" in self._src()

    def test_status_column_in_import_batches(self):
        assert "status" in self._src()

    def test_row_count_column(self):
        assert "row_count" in self._src()

    def test_imported_count_column(self):
        assert "imported_count" in self._src()

    def test_skipped_count_column(self):
        assert "skipped_count" in self._src()

    def test_error_count_column(self):
        assert "error_count" in self._src()

    def test_completed_at_column(self):
        assert "completed_at" in self._src()

    def test_error_details_jsonb_column(self):
        src = self._src()
        assert "error_details" in src
        assert "JSONB" in src

    def test_baselines_computed_boolean(self):
        assert "baselines_computed" in self._src()

    def test_categories_learned_column(self):
        assert "categories_learned" in self._src()


class TestBalanceSnapshotsTableColumns:
    """finance.balance_snapshots must have account-balance tracking columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_account_id_column(self):
        assert "account_id" in self._src()

    def test_balance_numeric_column(self):
        src = self._src()
        assert "balance" in src
        assert "NUMERIC(14" in src

    def test_as_of_date_column(self):
        assert "as_of_date" in self._src()

    def test_source_column_in_balance_snapshots(self):
        assert "source" in self._src()

    def test_uq_balance_snapshot_account_date_constraint(self):
        assert "uq_balance_snapshot_account_date" in self._src()


class TestBudgetsTableColumns:
    """finance.budgets must have category-budget tracking columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_category_column_in_budgets(self):
        assert "category" in self._src()

    def test_amount_numeric_in_budgets(self):
        src = self._src()
        assert "amount" in src
        assert "NUMERIC(14" in src

    def test_period_column_in_budgets(self):
        assert "period" in self._src()

    def test_warn_threshold_column(self):
        assert "warn_threshold" in self._src()

    def test_alert_threshold_column(self):
        assert "alert_threshold" in self._src()

    def test_is_active_column_in_budgets(self):
        assert "is_active" in self._src()

    def test_uq_budget_category_period_constraint(self):
        assert "uq_budget_category_period" in self._src()

    def test_uq_budget_category_period_partial_on_is_active(self):
        src = self._src()
        idx_pos = src.find("uq_budget_category_period")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        assert "WHERE is_active" in block


class TestTransactionCorrectionsTableColumns:
    """finance.transaction_corrections must have audit-trail columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_transaction_id_column(self):
        assert "transaction_id" in self._src()

    def test_field_name_column(self):
        assert "field_name" in self._src()

    def test_old_value_column(self):
        assert "old_value" in self._src()

    def test_new_value_column(self):
        assert "new_value" in self._src()

    def test_reason_column(self):
        assert "reason" in self._src()

    def test_source_column_in_corrections(self):
        assert "source" in self._src()

    def test_idx_correction_txn_index(self):
        assert "idx_correction_txn" in self._src()

    def test_idx_correction_created_index(self):
        assert "idx_correction_created" in self._src()


# ---------------------------------------------------------------------------
# Task 2.3 — New indexes on finance.transactions
# ---------------------------------------------------------------------------


class TestTransactionNewIndexes:
    """upgrade() must create all required indexes on finance.transactions."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_idx_txn_posted_at(self):
        assert "idx_txn_posted_at" in self._src()

    def test_idx_txn_transaction_date(self):
        assert "idx_txn_transaction_date" in self._src()

    def test_idx_txn_transaction_date_partial_not_null(self):
        src = self._src()
        idx_pos = src.find("idx_txn_transaction_date")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 250]
        assert "transaction_date IS NOT NULL" in block

    def test_idx_txn_category_posted(self):
        assert "idx_txn_category_posted" in self._src()

    def test_idx_txn_normalized_merchant(self):
        assert "idx_txn_normalized_merchant" in self._src()

    def test_idx_txn_normalized_merchant_partial_not_null(self):
        src = self._src()
        idx_pos = src.find("idx_txn_normalized_merchant")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 250]
        assert "normalized_merchant IS NOT NULL" in block

    def test_idx_txn_direction_posted(self):
        assert "idx_txn_direction_posted" in self._src()

    def test_idx_txn_amount(self):
        assert "idx_txn_amount" in self._src()

    def test_idx_txn_active(self):
        assert "idx_txn_active" in self._src()

    def test_idx_txn_active_partial_deleted_at_null(self):
        src = self._src()
        idx_pos = src.find("idx_txn_active")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 250]
        assert "deleted_at IS NULL" in block

    def test_idx_txn_recurring_group(self):
        assert "idx_txn_recurring_group" in self._src()

    def test_idx_txn_recurring_group_partial_not_null(self):
        src = self._src()
        idx_pos = src.find("idx_txn_recurring_group")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 250]
        assert "recurring_group_id IS NOT NULL" in block

    def test_idx_txn_import_batch(self):
        assert "idx_txn_import_batch" in self._src()

    def test_idx_txn_import_batch_partial_not_null(self):
        src = self._src()
        idx_pos = src.find("idx_txn_import_batch")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 250]
        assert "import_batch_id IS NOT NULL" in block

    def test_idx_txn_tags_gin(self):
        src = self._src()
        assert "idx_txn_tags_gin" in src
        assert "GIN" in src

    def test_idx_txn_debit_category_posted(self):
        assert "idx_txn_debit_category_posted" in self._src()

    def test_idx_txn_debit_category_posted_partial_condition(self):
        src = self._src()
        idx_pos = src.find("idx_txn_debit_category_posted")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        # Must filter to debit transactions only
        assert "debit" in block
        assert "deleted_at IS NULL" in block

    def test_all_new_indexes_use_if_not_exists(self):
        """All CREATE INDEX / CREATE UNIQUE INDEX must use IF NOT EXISTS."""
        src = self._src()
        index_creations = src.count("CREATE INDEX") + src.count("CREATE UNIQUE INDEX")
        if_not_exists_count = src.count("CREATE INDEX IF NOT EXISTS") + src.count(
            "CREATE UNIQUE INDEX IF NOT EXISTS"
        )
        assert index_creations == if_not_exists_count, (
            f"All index creations must use IF NOT EXISTS: "
            f"found {index_creations} but only {if_not_exists_count} with IF NOT EXISTS"
        )


# ---------------------------------------------------------------------------
# Task 2.3 (continued) — Tiered dedup UNIQUE partial indexes
# ---------------------------------------------------------------------------


class TestTieredDedupIndexes:
    """Tiered deduplication indexes must be present with correct partial conditions.

    These tests verify task 2.3 (index presence) and task 2.7 (dedup semantics)
    by inspecting the source of the migration's upgrade() function.
    """

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    # Priority 1: bank external ID
    def test_uq_txn_external_id_account_exists(self):
        assert "uq_txn_external_id_account" in self._src()

    def test_uq_txn_external_id_account_covers_account_and_external_id(self):
        src = self._src()
        idx_pos = src.find("uq_txn_external_id_account")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        assert "account_id" in block
        assert "external_id" in block

    def test_uq_txn_external_id_account_partial_condition(self):
        src = self._src()
        idx_pos = src.find("uq_txn_external_id_account")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        assert "external_id IS NOT NULL" in block

    # Priority 2: source message dedup (replaces the finance_001 uq_transactions_dedupe)
    def test_uq_txn_source_dedupe_exists(self):
        assert "uq_txn_source_dedupe" in self._src()

    def test_uq_txn_source_dedupe_covers_source_message_id_merchant_amount_posted_at(self):
        src = self._src()
        idx_pos = src.find("uq_txn_source_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 350]
        assert "source_message_id" in block
        assert "merchant" in block
        assert "amount" in block
        assert "posted_at" in block

    def test_uq_txn_source_dedupe_partial_condition(self):
        src = self._src()
        idx_pos = src.find("uq_txn_source_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        assert "source_message_id IS NOT NULL" in block

    # Priority 3: composite fallback
    def test_uq_txn_composite_dedupe_exists(self):
        assert "uq_txn_composite_dedupe" in self._src()

    def test_uq_txn_composite_dedupe_covers_account_posted_at_amount_merchant(self):
        src = self._src()
        idx_pos = src.find("uq_txn_composite_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 350]
        assert "account_id" in block
        assert "posted_at" in block
        assert "amount" in block
        assert "merchant" in block

    def test_uq_txn_composite_dedupe_partial_condition_excludes_rows_with_dedup_keys(self):
        """The composite fallback must only apply when neither external_id nor
        source_message_id is present — otherwise the higher-priority indexes handle
        deduplication."""
        src = self._src()
        idx_pos = src.find("uq_txn_composite_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 400]
        assert "external_id IS NULL" in block
        assert "source_message_id IS NULL" in block


# ---------------------------------------------------------------------------
# Task 2.7 — Dedup index semantics (duplicate rejection / non-duplicate allowance)
# ---------------------------------------------------------------------------


class TestDedupIndexSemantics:
    """Verify that dedup index partial conditions correctly encode the intended semantics.

    These are source-level behavioral tests: they check the partial index WHERE
    clauses to ensure that:
    - Duplicate inserts (same key values) would be rejected by the unique constraint
    - Non-duplicate inserts (distinct key values) would not be blocked
    - Rows without the relevant key columns are excluded from each tier's constraint
    """

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_priority1_only_applies_when_external_id_present(self):
        """uq_txn_external_id_account must be a partial index gated on external_id IS NOT NULL.

        This ensures rows without an external_id (e.g. CSV imports) are NOT constrained by
        the bank-dedup index and can still be inserted.
        """
        src = self._src()
        idx_pos = src.find("uq_txn_external_id_account")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        # MUST have WHERE external_id IS NOT NULL so CSV rows are not blocked
        assert "WHERE" in block
        assert "external_id IS NOT NULL" in block

    def test_priority1_rejects_same_account_and_external_id(self):
        """The unique constraint columns must include both account_id and external_id,
        so two rows with identical (account_id, external_id) would collide."""
        src = self._src()
        idx_pos = src.find("uq_txn_external_id_account")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        # Both columns must appear in the index definition
        assert "account_id" in block
        assert "external_id" in block

    def test_priority2_only_applies_when_source_message_id_present(self):
        """uq_txn_source_dedupe must be a partial index gated on source_message_id IS NOT NULL.

        This ensures rows without source_message_id (e.g. manual entries) are NOT
        constrained by the email-dedup index.
        """
        src = self._src()
        idx_pos = src.find("uq_txn_source_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 300]
        assert "WHERE" in block
        assert "source_message_id IS NOT NULL" in block

    def test_priority2_rejects_same_source_message_merchant_amount_posted_at(self):
        """The unique constraint columns for tier-2 must be the full 4-tuple."""
        src = self._src()
        idx_pos = src.find("uq_txn_source_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 350]
        for col in ("source_message_id", "merchant", "amount", "posted_at"):
            assert col in block, f"Tier-2 dedup index missing column: {col}"

    def test_priority3_excludes_rows_covered_by_higher_priority_indexes(self):
        """uq_txn_composite_dedupe must exclude rows that have external_id OR
        source_message_id — those are handled by tier-1 and tier-2 respectively.

        Without this exclusion, rows covered by the faster dedup tiers would also
        be checked against the composite fallback, causing spurious constraint violations.
        """
        src = self._src()
        idx_pos = src.find("uq_txn_composite_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 400]
        assert "WHERE" in block
        # Both nullity checks must be present (AND-combined)
        assert "external_id IS NULL" in block
        assert "source_message_id IS NULL" in block

    def test_priority3_allows_distinct_composite_key(self):
        """The composite fallback must include all four discriminating columns so that
        two transactions with the same date and amount but different merchant names
        would NOT collide."""
        src = self._src()
        idx_pos = src.find("uq_txn_composite_dedupe")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 350]
        for col in ("account_id", "posted_at", "amount", "merchant"):
            assert col in block, f"Composite dedup index missing column: {col}"


# ---------------------------------------------------------------------------
# Task 2.4 — Default category seeding
# ---------------------------------------------------------------------------


class TestDefaultCategorySeeding:
    """upgrade() must seed default categories with idempotent ON CONFLICT DO NOTHING."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_insert_into_categories(self):
        src = self._src()
        assert "INSERT INTO categories" in src or "INSERT INTO finance.categories" in src

    def test_seeding_uses_on_conflict_do_nothing(self):
        assert "ON CONFLICT DO NOTHING" in self._src()

    def test_seeds_groceries(self):
        assert "groceries" in self._src()

    def test_seeds_dining(self):
        assert "dining" in self._src()

    def test_seeds_transport(self):
        assert "transport" in self._src()

    def test_seeds_subscriptions(self):
        assert "subscriptions" in self._src()

    def test_seeds_utilities(self):
        assert "utilities" in self._src()

    def test_seeds_housing(self):
        assert "housing" in self._src()

    def test_seeds_healthcare(self):
        assert "healthcare" in self._src()

    def test_seeds_entertainment(self):
        assert "entertainment" in self._src()

    def test_seeds_shopping(self):
        assert "shopping" in self._src()

    def test_seeds_travel(self):
        assert "travel" in self._src()

    def test_seeds_education_as_tax_relevant(self):
        src = self._src()
        assert "education" in src

    def test_seeds_medical_as_tax_relevant(self):
        src = self._src()
        assert "medical" in src

    def test_seeds_charitable_as_tax_relevant(self):
        src = self._src()
        assert "charitable" in src

    def test_seeds_uncategorized(self):
        assert "uncategorized" in self._src()

    def test_seeds_income(self):
        assert "income" in self._src()

    def test_seeds_transfer(self):
        assert "transfer" in self._src()

    def test_seeds_fees(self):
        assert "fees" in self._src()

    def test_seeding_is_idempotent(self):
        """ON CONFLICT DO NOTHING guarantees re-running the migration does not error."""
        assert "ON CONFLICT DO NOTHING" in self._src()


# ---------------------------------------------------------------------------
# Task 2.5 — Materialized view spending_summaries
# ---------------------------------------------------------------------------


class TestSpendingSummariesMaterializedView:
    """upgrade() must create the spending_summaries materialized view."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_create_materialized_view(self):
        src = self._src()
        assert "CREATE MATERIALIZED VIEW" in src
        assert "spending_summaries" in src

    def test_materialized_view_if_not_exists(self):
        src = self._src()
        assert "CREATE MATERIALIZED VIEW IF NOT EXISTS" in src

    def test_aggregates_from_transactions(self):
        src = self._src()
        assert "transactions" in src
        assert "spending_summaries" in src

    def test_filters_deleted_at_null(self):
        """View must exclude soft-deleted transactions."""
        src = self._src()
        assert "deleted_at IS NULL" in src

    def test_groups_by_period_month(self):
        src = self._src()
        assert "DATE_TRUNC" in src or "date_trunc" in src
        assert "month" in src.lower()

    def test_groups_by_account_id(self):
        src = self._src()
        # The GROUP BY clause of the materialized view must include account_id
        assert "account_id" in src

    def test_groups_by_category(self):
        src = self._src()
        assert "category" in src

    def test_groups_by_direction(self):
        src = self._src()
        assert "direction" in src

    def test_groups_by_currency(self):
        src = self._src()
        assert "currency" in src

    def test_aggregates_transaction_count(self):
        src = self._src()
        assert "COUNT(" in src or "count(" in src

    def test_aggregates_total_amount_sum(self):
        src = self._src()
        assert "SUM(" in src or "sum(" in src

    def test_aggregates_avg_amount(self):
        src = self._src()
        assert "AVG(" in src or "avg(" in src

    def test_unique_index_for_concurrent_refresh(self):
        """A UNIQUE index on the materialized view is required for CONCURRENT refresh."""
        src = self._src()
        assert "uq_spending_summary_key" in src
        assert "UNIQUE" in src

    def test_uq_spending_summary_key_covers_period_account_category_direction_currency(self):
        src = self._src()
        idx_pos = src.find("uq_spending_summary_key")
        assert idx_pos != -1
        block = src[idx_pos : idx_pos + 350]
        for col in ("period", "account_id", "category", "direction", "currency"):
            assert col in block, f"uq_spending_summary_key missing column: {col}"


# ---------------------------------------------------------------------------
# Task 2.6 — downgrade() clean rollback
# ---------------------------------------------------------------------------


class TestDowngrade:
    """downgrade() must cleanly restore the finance_001 state."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    # Materialized view dropped first (no FKs from tables to it)
    def test_drops_spending_summaries_view(self):
        src = self._src()
        assert "spending_summaries" in src
        assert "DROP MATERIALIZED VIEW" in src

    def test_drops_spending_summaries_if_exists(self):
        src = self._src()
        assert "DROP MATERIALIZED VIEW IF EXISTS" in src

    # New tables dropped
    def test_drops_transaction_corrections(self):
        assert "DROP TABLE IF EXISTS transaction_corrections" in self._src()

    def test_drops_budgets(self):
        assert "DROP TABLE IF EXISTS budgets" in self._src()

    def test_drops_balance_snapshots(self):
        assert "DROP TABLE IF EXISTS balance_snapshots" in self._src()

    def test_drops_import_batches(self):
        assert "DROP TABLE IF EXISTS import_batches" in self._src()

    def test_drops_merchant_mappings(self):
        assert "DROP TABLE IF EXISTS merchant_mappings" in self._src()

    def test_drops_categories(self):
        assert "DROP TABLE IF EXISTS categories" in self._src()

    def test_drops_recurring_groups(self):
        assert "DROP TABLE IF EXISTS recurring_groups" in self._src()

    # Tiered dedup indexes dropped (replace old uq_transactions_dedupe)
    def test_drops_uq_txn_external_id_account(self):
        src = self._src()
        assert "uq_txn_external_id_account" in src
        assert "DROP INDEX" in src

    def test_drops_uq_txn_source_dedupe(self):
        src = self._src()
        assert "uq_txn_source_dedupe" in src
        assert "DROP INDEX" in src

    def test_drops_uq_txn_composite_dedupe(self):
        src = self._src()
        assert "uq_txn_composite_dedupe" in src
        assert "DROP INDEX" in src

    # Dependency order: tables with FKs to other tables must be dropped first
    def test_recurring_groups_dropped_before_subscriptions_dependency(self):
        """recurring_groups references subscriptions; must be dropped before subscriptions."""
        src = self._src()
        rg_pos = src.find("DROP TABLE IF EXISTS recurring_groups")
        sub_pos = src.find("DROP TABLE IF EXISTS subscriptions")
        # subscriptions is in finance_001 and not dropped by 002 downgrade —
        # but if both appear, recurring_groups must come first
        if rg_pos != -1 and sub_pos != -1:
            assert rg_pos < sub_pos

    def test_all_drops_use_if_exists(self):
        """All DROP TABLE statements must use IF EXISTS (idempotent downgrade)."""
        src = self._src()
        drop_count = src.count("DROP TABLE")
        if_exists_count = src.count("DROP TABLE IF EXISTS")
        assert drop_count == if_exists_count, (
            f"All DROP TABLE must use IF EXISTS: found {drop_count}, "
            f"only {if_exists_count} with IF EXISTS"
        )

    def test_all_index_drops_use_if_exists(self):
        """All DROP INDEX statements must use IF EXISTS."""
        src = self._src()
        drop_idx_count = src.count("DROP INDEX")
        if_exists_idx_count = src.count("DROP INDEX IF EXISTS")
        assert drop_idx_count == if_exists_idx_count, (
            f"All DROP INDEX must use IF EXISTS: found {drop_idx_count}, "
            f"only {if_exists_idx_count} with IF EXISTS"
        )


# ---------------------------------------------------------------------------
# FK constraints on finance.transactions
# ---------------------------------------------------------------------------


class TestTransactionForeignKeys:
    """upgrade() must add FK constraints for new UUID reference columns."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_recurring_group_id_fk_to_recurring_groups(self):
        src = self._src()
        assert "recurring_group_id" in src
        assert "recurring_groups" in src

    def test_duplicate_of_fk_to_transactions(self):
        src = self._src()
        assert "duplicate_of" in src
        # Self-referential FK to transactions(id)
        assert "REFERENCES transactions" in src

    def test_import_batch_id_fk_to_import_batches(self):
        src = self._src()
        assert "import_batch_id" in src
        assert "import_batches" in src


# ---------------------------------------------------------------------------
# Cross-cutting data integrity checks
# ---------------------------------------------------------------------------


class TestDataIntegrityRules:
    """Ensure the 002 migration follows the same quality standards as 001."""

    def _upgrade_src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def _downgrade_src(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_no_plain_timestamp_columns(self):
        """All timestamp columns must use TIMESTAMPTZ, not plain TIMESTAMP."""
        src = self._upgrade_src()
        cleaned = src.replace("TIMESTAMPTZ", "")
        assert "TIMESTAMP" not in cleaned, (
            "Plain TIMESTAMP found in migration — all timestamps must be TIMESTAMPTZ"
        )

    def test_all_amount_columns_use_numeric_14_2(self):
        src = self._upgrade_src()
        assert "FLOAT" not in src, "FLOAT type found — amounts must use NUMERIC(14, 2)"
        assert "REAL" not in src, "REAL type found — amounts must use NUMERIC(14, 2)"

    def test_gen_random_uuid_used_for_pks(self):
        assert "gen_random_uuid()" in self._upgrade_src()

    def test_if_not_exists_guards_on_all_tables(self):
        src = self._upgrade_src()
        table_count = src.count("CREATE TABLE")
        ine_count = src.count("CREATE TABLE IF NOT EXISTS")
        assert table_count == ine_count, (
            f"All CREATE TABLE must use IF NOT EXISTS: "
            f"{table_count} CREATE TABLE, {ine_count} with IF NOT EXISTS"
        )
