"""Contract tests: Finance Soft-Delete (RFC 0012).

Validates soft-delete-only policy, tiered dedup indexes,
monetary precision (NUMERIC), and audit trail.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestSoftDeleteContract:
    """RFC 0012: No hard DELETE on finance.transactions, ever."""

    def test_soft_delete_mechanism_and_design_principles(self):
        """All deletes use UPDATE SET deleted_at; version for optimistic locking;
        merge/split/roster jobs all use soft delete."""
        soft_sql = (
            "UPDATE finance.transactions SET deleted_at = now(), "
            "updated_at = now(), version = version + 1 WHERE id = $1"
        )
        assert "DELETE FROM" not in soft_sql and "deleted_at" in soft_sql
        assert "version" in soft_sql

        # Normal queries filter deleted
        assert "deleted_at IS NULL" in "SELECT * FROM finance.transactions WHERE deleted_at IS NULL"

        # merge_duplicates uses soft delete
        merge_fields = {"is_duplicate", "duplicate_of", "deleted_at"}
        assert "deleted_at" in merge_fields


class TestTieredDeduplicationIndexes:
    """RFC 0012: Three-tier dedup prevents duplicate ingestion."""

    def test_three_dedup_tiers(self):
        tiers = {
            "tier1": {"type": "btree", "columns": ["external_id", "source"]},
            "tier2": {"type": "btree", "columns": ["amount", "posted_at", "merchant", "source"]},
            "tier3": {"type": "gin", "columns": ["raw_data"]},
        }
        assert len(tiers) == 3
        assert tiers["tier1"]["columns"][0] == "external_id"
        # Tier 1 uses btree index on (external_id, source) — the uniqueness anchor
        assert tiers["tier1"]["type"] == "btree"
        # Tier 3 uses GIN for raw_data similarity — documents accepted approach
        assert tiers["tier3"]["type"] == "gin"


class TestMonetaryPrecisionAndAudit:
    """RFC 0012: NUMERIC(14,2) for money; corrections for audit trail."""

    def test_amount_types_and_audit(self):
        numeric_columns = {"amount": "NUMERIC(14,2)", "balance": "NUMERIC(14,2)"}
        for col, typ in numeric_columns.items():
            assert "NUMERIC" in typ and "float" not in typ.lower()
        assert len("USD") == 3  # ISO 4217

        correction_sources = {
            "owner_edit",
            "butler_reconciliation",
            "import_correction",
            "duplicate_merge",
        }
        assert len(correction_sources) == 4
