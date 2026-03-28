"""Tests for the finance merchant_mappings migration (finance_002)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = MODULES_DIR / "finance" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "002_finance_merchant_mappings.py"


def _load_migration():
    """Load the finance_002 migration module dynamically."""
    spec = importlib.util.spec_from_file_location(
        "finance_merchant_mappings_migration", MIGRATION_FILE
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
        assert mod.revision == "finance_002"
        assert mod.down_revision == "finance_001"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """The migration declares upgrade()/downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_creates_merchant_mappings_table(self) -> None:
        """Upgrade creates the merchant_mappings table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS merchant_mappings" in source

    def test_table_has_required_columns(self) -> None:
        """merchant_mappings has all spec-required columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
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
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "UUID" in source
        assert "PRIMARY KEY" in source
        assert "gen_random_uuid()" in source

    def test_raw_pattern_is_text_not_null(self) -> None:
        """raw_pattern column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "raw_pattern" in source
        assert "TEXT NOT NULL" in source

    def test_normalized_merchant_is_text_not_null(self) -> None:
        """normalized_merchant column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "normalized_merchant" in source
        assert "TEXT NOT NULL" in source

    def test_category_is_text_not_null(self) -> None:
        """category column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "category" in source
        assert "TEXT NOT NULL" in source

    def test_source_has_check_constraint(self) -> None:
        """source column has CHECK constraint for valid values."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "source" in source
        assert "CHECK" in source
        assert "'learned'" in source
        assert "'manual'" in source
        assert "'import'" in source

    def test_is_active_defaults_to_true(self) -> None:
        """is_active column defaults to true."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "is_active" in source
        assert "DEFAULT true" in source or "DEFAULT TRUE" in source.upper()

    def test_metadata_is_jsonb(self) -> None:
        """metadata column uses JSONB type."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "JSONB" in source

    def test_timestamps_are_timestamptz(self) -> None:
        """Time columns use TIMESTAMPTZ for timezone-aware storage."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "TIMESTAMPTZ" in source

    def test_creates_unique_pattern_index(self) -> None:
        """Upgrade creates unique index on lower(raw_pattern) WHERE is_active = true."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_merchant_mapping_pattern" in source
        assert "UNIQUE INDEX" in source
        assert "lower(raw_pattern)" in source
        assert "is_active = true" in source

    def test_creates_functional_pattern_index(self) -> None:
        """Upgrade creates functional index on lower(raw_pattern) for case-insensitive lookups."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_merchant_mapping_pattern_lower" in source
        assert "lower(raw_pattern)" in source

    def test_creates_normalized_merchant_index(self) -> None:
        """Upgrade creates index on normalized_merchant."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_merchant_mapping_normalized" in source
        assert "normalized_merchant" in source

    def test_creates_category_index(self) -> None:
        """Upgrade creates index on category."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_merchant_mapping_category" in source
        assert "category" in source

    def test_creates_active_index(self) -> None:
        """Upgrade creates index on is_active."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_merchant_mapping_active" in source
        assert "is_active" in source


class TestDowngradeSQL:
    def test_drops_merchant_mappings_table(self) -> None:
        """Downgrade removes the merchant_mappings table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS merchant_mappings" in source
