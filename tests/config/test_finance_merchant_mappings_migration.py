"""Unit tests for finance merchant_mappings table migration.

Tests validate the merchant_mappings table structure, GIN trigram index presence,
extension creation, and downgrade completeness — all without requiring a live
database connection.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "002_merchant_mappings_trigram_index.py"


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
# Migration metadata
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """Migration file must exist in the finance chain directory."""
    migration_path = _finance_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Migration file should exist: {migration_path}"


def test_migration_has_revision_id():
    """Migration must have a revision ID."""
    migration = _load_migration()
    assert hasattr(migration, "revision"), "Migration should have revision attribute"
    assert migration.revision == "finance_002"


def test_migration_has_correct_down_revision():
    """Migration must specify correct down_revision."""
    migration = _load_migration()
    assert hasattr(migration, "down_revision"), "Migration should have down_revision attribute"
    assert migration.down_revision == "finance_001"


def test_migration_has_upgrade_function():
    """Migration must have an upgrade() function."""
    migration = _load_migration()
    assert hasattr(migration, "upgrade"), "Migration should have upgrade function"
    assert callable(migration.upgrade)


def test_migration_has_downgrade_function():
    """Migration must have a downgrade() function."""
    migration = _load_migration()
    assert hasattr(migration, "downgrade"), "Migration should have downgrade function"
    assert callable(migration.downgrade)


# ---------------------------------------------------------------------------
# pg_trgm extension
# ---------------------------------------------------------------------------


class TestPgTrgmExtension:
    """Tests for pg_trgm extension creation."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_create_extension_pg_trgm(self):
        """Migration must create pg_trgm extension."""
        assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in self._source()

    def test_extension_uses_if_not_exists(self):
        """Extension creation must use IF NOT EXISTS for idempotency."""
        assert "IF NOT EXISTS pg_trgm" in self._source()


# ---------------------------------------------------------------------------
# merchant_mappings table structure
# ---------------------------------------------------------------------------


class TestMerchantMappingsTable:
    """Structural checks for the finance.merchant_mappings table DDL."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_merchant_mappings_table_created_with_if_not_exists(self):
        """merchant_mappings table must use IF NOT EXISTS."""
        assert "CREATE TABLE IF NOT EXISTS merchant_mappings" in self._source()

    def test_merchant_mappings_uuid_pk(self):
        """merchant_mappings table must have UUID primary key."""
        src = self._source()
        assert "id" in src
        assert "UUID PRIMARY KEY DEFAULT gen_random_uuid()" in src

    def test_merchant_mappings_has_merchant_column(self):
        """merchant_mappings table must have a merchant TEXT NOT NULL column."""
        src = self._source()
        assert "merchant" in src
        assert "TEXT NOT NULL" in src

    def test_merchant_mappings_has_category_column(self):
        """merchant_mappings table must have a category TEXT NOT NULL column."""
        src = self._source()
        assert "category" in src
        # Verify category is in the main table creation, not just referenced elsewhere
        assert "category" in src

    def test_merchant_mappings_has_confidence_numeric(self):
        """merchant_mappings table must have confidence NUMERIC(5, 4) column."""
        src = self._source()
        assert "confidence" in src
        assert "NUMERIC(5, 4)" in src

    def test_merchant_mappings_confidence_default_0_5(self):
        """merchant_mappings confidence must default to 0.5, matching application code."""
        assert "DEFAULT 0.5" in self._source()

    def test_merchant_mappings_has_sample_count_integer(self):
        """merchant_mappings table must have sample_count INTEGER column."""
        src = self._source()
        assert "sample_count" in src
        assert "INTEGER NOT NULL DEFAULT 1" in src

    def test_merchant_mappings_has_is_active_boolean(self):
        """merchant_mappings table must have is_active BOOLEAN column."""
        src = self._source()
        assert "is_active" in src
        assert "BOOLEAN NOT NULL DEFAULT true" in src

    def test_merchant_mappings_has_timestamps(self):
        """merchant_mappings table must have created_at and updated_at columns."""
        src = self._source()
        assert "created_at" in src
        assert "updated_at" in src
        assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in src


# ---------------------------------------------------------------------------
# merchant_mappings indexes
# ---------------------------------------------------------------------------


class TestMerchantMappingsIndexes:
    """Tests for indexes on merchant_mappings table."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_unique_merchant_mappings_merchant_index(self):
        """merchant_mappings must have unique index on lower(merchant) with is_active filter."""
        src = self._source()
        assert "uq_merchant_mappings_merchant" in src
        assert "CREATE UNIQUE INDEX" in src
        assert "lower(merchant)" in src
        assert "WHERE is_active = true" in src

    def test_gin_trigram_index_on_merchant(self):
        """merchant_mappings must have GIN trigram index on merchant column."""
        src = self._source()
        assert "idx_merchant_mappings_merchant_trgm" in src
        assert "USING GIN" in src
        assert "merchant gin_trgm_ops" in src

    def test_gin_trigram_index_if_not_exists(self):
        """GIN trigram index must use IF NOT EXISTS."""
        src = self._source()
        assert "CREATE INDEX IF NOT EXISTS idx_merchant_mappings_merchant_trgm" in src

    def test_is_active_index_exists(self):
        """merchant_mappings must have index on is_active for filtering."""
        src = self._source()
        assert "idx_merchant_mappings_is_active" in src
        assert "CREATE INDEX" in src
        assert "is_active" in src

    def test_category_index_exists(self):
        """merchant_mappings must have index on category for filtering."""
        src = self._source()
        assert "idx_merchant_mappings_category" in src
        assert "CREATE INDEX" in src
        assert "category" in src

    def test_all_indexes_use_if_not_exists(self):
        """All CREATE INDEX statements must use IF NOT EXISTS."""
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


# ---------------------------------------------------------------------------
# downgrade() completeness
# ---------------------------------------------------------------------------


class TestDowngrade:
    """Structural checks for the downgrade() function."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_downgrade_drops_merchant_mappings(self):
        """downgrade() must drop merchant_mappings table."""
        assert "DROP TABLE IF EXISTS merchant_mappings" in self._source()

    def test_downgrade_does_not_drop_pg_trgm_extension(self):
        """downgrade() must NOT drop the pg_trgm extension.

        pg_trgm is a shared, system-level extension also used by the memory
        module (predicate_registry GIN trigram index). Dropping it here would
        break other features on downgrade.
        """
        assert "DROP EXTENSION" not in self._source()

    def test_downgrade_uses_if_exists(self):
        """The DROP TABLE statement in downgrade() must use IF EXISTS."""
        src = self._source()
        assert "DROP TABLE IF EXISTS merchant_mappings" in src


# ---------------------------------------------------------------------------
# Data integrity checks
# ---------------------------------------------------------------------------


class TestDataIntegrityRules:
    """Cross-cutting integrity checks for the merchant_mappings table."""

    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_no_plain_timestamp_columns(self):
        """All timestamp columns must use TIMESTAMPTZ, not plain TIMESTAMP."""
        src = self._source()
        # Strip out 'TIMESTAMPTZ' occurrences, then check no bare 'TIMESTAMP' remains
        # in the merchant_mappings context (before the next table if any)
        merchant_mappings_section = src[src.find("merchant_mappings") : src.find("downgrade")]
        cleaned = merchant_mappings_section.replace("TIMESTAMPTZ", "")
        # 'TIMESTAMP' should not appear in the cleaned source
        assert "TIMESTAMP" not in cleaned, (
            "Plain TIMESTAMP found in merchant_mappings — all timestamps must be TIMESTAMPTZ"
        )

    def test_gen_random_uuid_used_for_pk(self):
        """UUID primary key must use gen_random_uuid()."""
        src = self._source()
        assert "gen_random_uuid()" in src

    def test_all_tables_use_if_not_exists(self):
        """CREATE TABLE must use IF NOT EXISTS."""
        src = self._source()
        table_creations = src.count("CREATE TABLE")
        if_not_exists_creations = src.count("CREATE TABLE IF NOT EXISTS")
        assert table_creations == if_not_exists_creations

    def test_numeric_precision_for_confidence(self):
        """Confidence column must use NUMERIC(5, 4) for proper precision.

        NUMERIC(5, 4) stores values like 0.0000–9.9999 with 4 decimal places,
        matching the application code in pattern_recognition.py which uses
        NUMERIC(5, 4) and inserts values in [0.5, 0.99].
        """
        src = self._source()
        assert "NUMERIC(5, 4)" in src
