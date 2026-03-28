"""Tests for the finance merchant_mappings schema-correction migration (finance_003).

PR #894 (finance_002) created merchant_mappings with a simplified schema
(merchant, category, confidence, sample_count) that does not match the
application's pattern_recognition.py code. This migration (finance_003)
drops the incorrect table and recreates it with the canonical schema:
raw_pattern, normalized_merchant, category, confidence, learned_from_count,
source, is_active, metadata, created_at, updated_at.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = MODULES_DIR / "finance" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "003_merchant_mappings_schema_correction.py"


def _load_migration():
    """Load the finance_003 migration module dynamically."""
    spec = importlib.util.spec_from_file_location(
        "finance_merchant_mappings_correction_migration", MIGRATION_FILE
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestRevisionMetadata:
    def test_revision_identifiers(self) -> None:
        """The migration has correct revision metadata."""
        mod = _load_migration()
        assert mod.revision == "finance_003"
        assert mod.down_revision == "finance_002"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """The migration declares upgrade()/downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def _source(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_drops_existing_table_first(self) -> None:
        """Upgrade must drop the incorrectly-structured merchant_mappings from finance_002."""
        assert "DROP TABLE IF EXISTS merchant_mappings" in self._source()

    def test_creates_merchant_mappings_table(self) -> None:
        """Upgrade creates the merchant_mappings table."""
        assert "CREATE TABLE IF NOT EXISTS merchant_mappings" in self._source()

    def test_table_has_required_columns(self) -> None:
        """merchant_mappings has all spec-required columns."""
        source = self._source()
        for column in (
            "id",
            "raw_pattern",
            "normalized_merchant",
            "category",
            "confidence",
            "learned_from_count",
            "source",
            "is_active",
            "metadata",
            "created_at",
            "updated_at",
        ):
            assert column in source, f"Missing column: {column}"

    def test_id_is_uuid_primary_key(self) -> None:
        """id column is UUID primary key with gen_random_uuid() default."""
        source = self._source()
        assert "UUID" in source
        assert "PRIMARY KEY" in source
        assert "gen_random_uuid()" in source

    def test_raw_pattern_is_text_not_null(self) -> None:
        """raw_pattern column is TEXT NOT NULL."""
        source = self._source()
        assert "raw_pattern" in source
        assert "TEXT NOT NULL" in source

    def test_normalized_merchant_is_text_not_null(self) -> None:
        """normalized_merchant column is TEXT NOT NULL."""
        source = self._source()
        assert "normalized_merchant" in source
        assert "TEXT NOT NULL" in source

    def test_category_is_text_not_null(self) -> None:
        """category column is TEXT NOT NULL."""
        source = self._source()
        assert "category" in source
        assert "TEXT NOT NULL" in source

    def test_source_has_check_constraint(self) -> None:
        """source column has CHECK constraint for valid values."""
        source = self._source()
        assert "source" in source
        assert "CHECK" in source
        assert "'learned'" in source
        assert "'manual'" in source
        assert "'import'" in source

    def test_is_active_defaults_to_true(self) -> None:
        """is_active column defaults to true."""
        source = self._source()
        assert "is_active" in source
        assert "DEFAULT true" in source or "DEFAULT TRUE" in source.upper()

    def test_metadata_is_jsonb(self) -> None:
        """metadata column uses JSONB type."""
        assert "JSONB" in self._source()

    def test_timestamps_are_timestamptz(self) -> None:
        """Time columns use TIMESTAMPTZ for timezone-aware storage."""
        assert "TIMESTAMPTZ" in self._source()

    def test_creates_unique_pattern_index(self) -> None:
        """Upgrade creates unique index on lower(raw_pattern) WHERE is_active = true."""
        source = self._source()
        assert "uq_merchant_mapping_pattern" in source
        assert "UNIQUE INDEX" in source
        assert "lower(raw_pattern)" in source
        assert "is_active = true" in source

    def test_creates_functional_pattern_index(self) -> None:
        """Upgrade creates functional index on lower(raw_pattern) for case-insensitive lookups."""
        source = self._source()
        assert "idx_merchant_mapping_pattern_lower" in source
        assert "lower(raw_pattern)" in source

    def test_creates_normalized_merchant_index(self) -> None:
        """Upgrade creates index on normalized_merchant."""
        source = self._source()
        assert "idx_merchant_mapping_normalized" in source
        assert "normalized_merchant" in source

    def test_creates_category_index(self) -> None:
        """Upgrade creates index on category."""
        source = self._source()
        assert "idx_merchant_mapping_category" in source
        assert "category" in source

    def test_creates_active_index(self) -> None:
        """Upgrade creates index on is_active."""
        source = self._source()
        assert "idx_merchant_mapping_active" in source
        assert "is_active" in source


class TestDowngradeSQL:
    def _source(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_drops_corrected_table(self) -> None:
        """Downgrade removes the corrected merchant_mappings table."""
        assert "DROP TABLE IF EXISTS merchant_mappings" in self._source()

    def test_recreates_finance_002_schema(self) -> None:
        """Downgrade restores the finance_002 (merchant-column) schema."""
        source = self._source()
        assert "CREATE TABLE IF NOT EXISTS merchant_mappings" in source
        assert "merchant" in source
        assert "sample_count" in source

    def test_downgrade_restores_trigram_index(self) -> None:
        """Downgrade restores the GIN trigram index on merchant."""
        source = self._source()
        assert "gin_trgm_ops" in source
