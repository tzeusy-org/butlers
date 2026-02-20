"""Tests for the core_010 schema bootstrap migration file."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"
CORE_MIGRATIONS_DIR = ALEMBIC_DIR / "versions" / "core"
MIGRATION_FILE = CORE_MIGRATIONS_DIR / "010_bootstrap_one_db_schemas.py"


def _load_migration():
    """Load the core_010 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("migration_core_010", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCore010SchemaBootstrapMigration:
    """Tests for the 010_bootstrap_one_db_schemas migration."""

    def test_migration_file_exists(self):
        """The migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self):
        """The migration has correct revision chain identifiers."""
        mod = _load_migration()
        assert mod.revision == "core_010"
        assert mod.down_revision == "core_009"
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

    def test_upgrade_creates_required_schemas(self):
        """Upgrade SQL includes all required schema names."""
        mod = _load_migration()
        assert mod._REQUIRED_SCHEMAS == (
            "shared",
            "general",
            "health",
            "messenger",
            "relationship",
            "switchboard",
        )
        source = inspect.getsource(mod.upgrade)
        assert "CREATE SCHEMA IF NOT EXISTS" in source

    def test_upgrade_sets_owner_best_effort(self):
        """Upgrade normalizes schema ownership when permissions allow."""
        mod = _load_migration()
        source = inspect.getsource(mod._set_schema_owner_best_effort)
        assert "ALTER SCHEMA" in source
        assert "OWNER TO CURRENT_USER" in source
        assert "insufficient_privilege" in source

    def test_downgrade_is_non_destructive_for_non_empty_schemas(self):
        """Downgrade handles non-empty schemas safely."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP SCHEMA IF EXISTS" in source
        assert "dependent_objects_still_exist" in source
