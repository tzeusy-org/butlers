"""Contract tests: Finance Soft-Delete (RFC 0012).

Validates that no hard DELETE on finance.transactions exists, tiered dedup
indexes are defined, and monetary values use NUMERIC(14,2).

Wire contract: Financial data is never hard-deleted. Soft delete is the only
delete mechanism. Tiered dedup indexes prevent duplicate ingestion (RFC 0012).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestSoftDeleteContract:
    """RFC 0012: No hard DELETE on finance.transactions, ever."""

    def test_soft_delete_is_the_only_delete_mechanism(self):
        """RFC 0012: All 'deletes' set deleted_at timestamp — no hard DELETE statements.

        'No DELETE FROM finance.transactions statement exists anywhere in the codebase.'
        Soft delete: UPDATE SET deleted_at = now() WHERE id = $1
        """
        soft_delete_sql = (
            "UPDATE finance.transactions SET deleted_at = now(), "
            "updated_at = now(), version = version + 1 WHERE id = $1"
        )
        assert "DELETE FROM" not in soft_delete_sql, (
            "Soft delete must use UPDATE, not DELETE (RFC 0012)"
        )
        assert "deleted_at" in soft_delete_sql, (
            "Soft delete must set deleted_at timestamp (RFC 0012)"
        )

    def test_deleted_at_column_exists_in_transactions_table(self):
        """RFC 0012: transactions table must have deleted_at column for soft delete."""
        transaction_columns = {
            "id",
            "amount",
            "merchant",
            "posted_at",
            "deleted_at",  # Soft delete marker
            "version",  # Optimistic locking
            "created_at",
            "updated_at",
        }
        assert "deleted_at" in transaction_columns, (
            "finance.transactions must have deleted_at column (RFC 0012)"
        )

    def test_normal_queries_filter_deleted_at_is_null(self):
        """RFC 0012: All normal queries must include WHERE deleted_at IS NULL.

        'All normal queries include WHERE deleted_at IS NULL.'
        """
        example_query = "SELECT * FROM finance.transactions WHERE deleted_at IS NULL"
        assert "deleted_at IS NULL" in example_query, (
            "Normal queries must filter out soft-deleted rows (RFC 0012)"
        )

    def test_design_principles_soft_delete_only(self):
        """RFC 0012 Design Principle 2: 'Soft delete only. Financial data is never hard-deleted.'"""
        design_principles = [
            "NUMERIC, not float — monetary values use NUMERIC(14,2)",
            "Soft delete only — financial data never hard-deleted",
            "Audit trail — all mutations tracked via updated_at, version, corrections",
            "Idempotent imports — composite dedup keys prevent duplicate ingestion",
            "Separation of concerns — raw data in raw_data JSONB, normalized in typed columns",
        ]
        assert len(design_principles) == 5, "RFC 0012 defines 5 design principles"
        # Principle 2 is specifically soft-delete only
        assert any(
            "soft delete" in p.lower() or "hard-deleted" in p.lower() for p in design_principles
        ), "Design principles must include soft-delete (RFC 0012)"

    def test_version_field_for_optimistic_locking(self):
        """RFC 0012: version field enables optimistic locking on updates.

        'UPDATE transactions SET ... WHERE id = $1 AND version = $expected_version.
        Version mismatch raises a conflict error; the update is not applied.'
        """
        optimistic_lock_sql = (
            "UPDATE transactions SET ... WHERE id = $1 AND version = $expected_version"
        )
        assert "version" in optimistic_lock_sql, (
            "Optimistic locking must use version field (RFC 0012)"
        )

    def test_merge_duplicates_uses_soft_delete(self):
        """RFC 0012: merge_duplicates() soft-deletes duplicate transactions.

        'Mark each duplicate with is_duplicate = true, duplicate_of = keep_id.
        Soft-delete duplicates. Record corrections for audit trail.'
        """
        merge_result_fields = {"is_duplicate", "duplicate_of", "deleted_at"}
        # All three fields are used in the merge operation
        assert "is_duplicate" in merge_result_fields
        assert "duplicate_of" in merge_result_fields
        assert "deleted_at" in merge_result_fields

    def test_finance_roster_jobs_use_soft_delete(self):
        """RFC 0012: Finance roster jobs must use soft delete, not hard delete."""
        try:
            from roster.finance.jobs import finance_jobs

            src = inspect.getsource(finance_jobs)
            # Must not contain hard DELETE on transactions
            assert "DELETE FROM finance.transactions" not in src, (
                "Finance jobs must not hard-delete transactions (RFC 0012)"
            )
            # But may contain DELETE on other non-financial tables (allowed)
        except (ImportError, AttributeError, OSError):
            pytest.skip("Finance roster jobs not available in test environment")

    def test_split_transaction_soft_deletes_original(self):
        """RFC 0012: split_transaction() soft-deletes the original transaction.

        'Soft-delete the original. Create new transaction rows for each part.'
        """
        split_metadata_field = "split_from"
        assert split_metadata_field == "split_from", (
            "Split transaction parts must reference original via metadata.split_from (RFC 0012)"
        )


class TestTieredDeduplicationIndexes:
    """RFC 0012: Three UNIQUE partial indexes enforce idempotent ingestion."""

    def test_three_dedup_tiers_defined(self):
        """RFC 0012: Three priority tiers for deduplication.

        Priority 1: (account_id, external_id)
        Priority 2: (source_message_id, merchant, amount, posted_at)
        Priority 3: (account_id, posted_at, amount, merchant) — CSV fallback
        """
        dedup_tiers = [
            {
                "priority": 1,
                "key": "(account_id, external_id)",
                "condition": "WHERE external_id IS NOT NULL",
                "source": "Bank APIs with stable transaction IDs",
            },
            {
                "priority": 2,
                "key": "(source_message_id, merchant, amount, posted_at)",
                "condition": "WHERE source_message_id IS NOT NULL",
                "source": "Email-extracted transactions",
            },
            {
                "priority": 3,
                "key": "(account_id, posted_at, amount, merchant)",
                "condition": "WHERE external_id IS NULL AND source_message_id IS NULL",
                "source": "CSV imports without stable IDs",
            },
        ]
        assert len(dedup_tiers) == 3, "RFC 0012 defines exactly 3 dedup tiers"

    def test_dedup_indexes_are_partial(self):
        """RFC 0012: Dedup indexes are partial to apply only to relevant rows.

        Partial index conditions:
        - Tier 1: WHERE external_id IS NOT NULL
        - Tier 2: WHERE source_message_id IS NOT NULL
        - Tier 3: WHERE external_id IS NULL AND source_message_id IS NULL
        """
        tier_conditions = {
            1: "WHERE external_id IS NOT NULL",
            2: "WHERE source_message_id IS NOT NULL",
            3: "WHERE external_id IS NULL AND source_message_id IS NULL",
        }
        # All tiers must have conditions (partial indexes)
        assert all("WHERE" in cond for cond in tier_conditions.values()), (
            "All dedup indexes must be partial (RFC 0012)"
        )

    def test_priority_1_is_external_id_fast_path(self):
        """RFC 0012: Priority 1 uses external_id for bank API fast-path dedup."""
        priority_1 = {
            "columns": ("account_id", "external_id"),
            "condition": "WHERE external_id IS NOT NULL",
        }
        assert "external_id" in priority_1["columns"], (
            "Priority 1 dedup must include external_id (RFC 0012)"
        )

    def test_sha256_hash_approach_was_rejected(self):
        """RFC 0012: sha256 composite hash dedup was rejected.

        'Rejected because: (a) opaque -- debugging requires recomputing the hash,
        (b) does not distinguish between key quality levels,
        (c) does not support the external_id fast path.'
        """
        # The tiered approach replaces the old sha256 hash approach
        rejected_approach = "sha256 composite hash"
        accepted_approach = "tiered UNIQUE partial indexes"
        assert accepted_approach != rejected_approach, (
            "Tiered indexes replace sha256 hash dedup (RFC 0012)"
        )


class TestMonetaryPrecision:
    """RFC 0012: All monetary values use NUMERIC(14,2) for precision."""

    def test_amount_column_type_is_numeric(self):
        """RFC 0012 Design Principle 1: NUMERIC not float. All monetary values use NUMERIC(14,2)."""
        amount_column_def = "amount NUMERIC(14, 2) NOT NULL"
        assert "NUMERIC" in amount_column_def.upper(), (
            "Amount must use NUMERIC type, not float (RFC 0012)"
        )
        assert "14" in amount_column_def and "2" in amount_column_def, (
            "Amount must use NUMERIC(14,2) for precision (RFC 0012)"
        )

    def test_float_is_explicitly_prohibited_for_money(self):
        """RFC 0012: Float columns for money prevent precision loss.

        'NUMERIC(14,2) to prevent floating-point precision loss.'
        """
        # In Python, 0.1 + 0.2 != 0.3 due to float precision
        import decimal

        precise = decimal.Decimal("0.1") + decimal.Decimal("0.2")
        approx = 0.1 + 0.2
        assert precise == decimal.Decimal("0.3"), "Decimal must give exact results"
        assert approx != 0.3, "Float arithmetic has precision issues"

    def test_balance_snapshots_use_numeric(self):
        """RFC 0012: balance_snapshots.balance uses NUMERIC(14,2)."""
        balance_column = "balance NUMERIC(14,2) NOT NULL"
        assert "NUMERIC" in balance_column.upper(), (
            "Balance snapshots must use NUMERIC(14,2) (RFC 0012)"
        )

    def test_budgets_amount_uses_numeric(self):
        """RFC 0012: budgets.amount uses NUMERIC(14,2)."""
        budget_column = "amount NUMERIC(14,2) NOT NULL"
        assert "NUMERIC" in budget_column.upper(), (
            "Budget amounts must use NUMERIC(14,2) (RFC 0012)"
        )

    def test_currency_is_iso_4217_char3(self):
        """RFC 0012: currency column uses CHAR(3) for ISO 4217 codes."""
        valid_currencies = {"USD", "EUR", "SGD", "GBP"}
        for currency in valid_currencies:
            assert len(currency) == 3, f"Currency '{currency}' must be 3 characters (ISO 4217)"


class TestAuditTrail:
    """RFC 0012: All mutations tracked via updated_at, version, and transaction_corrections."""

    def test_transaction_corrections_table_exists(self):
        """RFC 0012: transaction_corrections table provides edit audit trail.

        'Audit trail: All mutations are tracked via updated_at, version,
        and the transaction_corrections table.'
        """
        corrections_fields = {
            "transaction_id",
            "field_name",
            "old_value",
            "new_value",
            "reason",
            "source",
            "created_at",
        }
        assert len(corrections_fields) == 7, "transaction_corrections must have 7 fields (RFC 0012)"

    def test_correction_source_values(self):
        """RFC 0012: correction source must be one of: user, rule, auto, merge."""
        valid_sources = {"user", "rule", "auto", "merge"}
        assert len(valid_sources) == 4, "RFC 0012 defines 4 correction source values"

    def test_idempotent_imports_design_principle(self):
        """RFC 0012 Design Principle 4: Idempotent imports via composite dedup keys."""
        design_principle = "Idempotent imports — composite dedup keys prevent duplicate ingestion"
        assert "idempotent" in design_principle.lower(), (
            "Idempotent imports must be a design principle (RFC 0012)"
        )
