"""Tests for the core_008 butler_secrets migration file."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"
CORE_MIGRATIONS_DIR = ALEMBIC_DIR / "versions" / "core"
MIGRATION_FILE = CORE_MIGRATIONS_DIR / "008_create_butler_secrets_table.py"


def _load_migration():
    """Load the core_008 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("migration_core_008", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCore008ButlerSecretsMigration:
    """Tests for the 008_create_butler_secrets_table migration."""

    def test_migration_file_exists(self):
        """The 008_create_butler_secrets_table migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self):
        """The migration has the correct revision chain identifiers."""
        mod = _load_migration()
        assert mod.revision == "core_008"
        assert mod.down_revision == "core_007"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_function_exists(self):
        """The migration exposes a callable upgrade() function."""
        mod = _load_migration()
        assert hasattr(mod, "upgrade")
        assert callable(mod.upgrade)

    def test_downgrade_function_exists(self):
        """The migration exposes a callable downgrade() function."""
        mod = _load_migration()
        assert hasattr(mod, "downgrade")
        assert callable(mod.downgrade)

    def test_upgrade_creates_butler_secrets_table(self):
        """The upgrade SQL contains a CREATE TABLE butler_secrets statement."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "butler_secrets" in source

    def test_upgrade_has_all_required_columns(self):
        """The upgrade SQL declares all 8 required columns from the schema spec."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        required_columns = [
            "secret_key",
            "secret_value",
            "category",
            "description",
            "is_sensitive",
            "created_at",
            "updated_at",
            "expires_at",
        ]
        for col in required_columns:
            assert col in source, f"Missing column: {col}"

    def test_upgrade_has_primary_key_on_secret_key(self):
        """The butler_secrets table uses secret_key as PRIMARY KEY."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "secret_key" in source
        assert "PRIMARY KEY" in source

    def test_upgrade_category_has_default(self):
        """The category column has a DEFAULT of 'general'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DEFAULT 'general'" in source

    def test_upgrade_is_sensitive_defaults_true(self):
        """The is_sensitive column defaults to true."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "is_sensitive" in source
        assert "BOOLEAN" in source
        assert "DEFAULT true" in source

    def test_upgrade_timestamps_use_timestamptz(self):
        """Timestamp columns use TIMESTAMPTZ for timezone awareness."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # At minimum 3 TIMESTAMPTZ columns: created_at, updated_at, expires_at
        assert source.count("TIMESTAMPTZ") >= 3

    def test_upgrade_creates_category_index(self):
        """The upgrade SQL creates the ix_butler_secrets_category index."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ix_butler_secrets_category" in source
        assert "category" in source

    def test_downgrade_drops_table(self):
        """The downgrade SQL drops the butler_secrets table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "butler_secrets" in source

    def test_downgrade_drops_index(self):
        """The downgrade SQL drops the category index before the table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX" in source
        assert "ix_butler_secrets_category" in source
