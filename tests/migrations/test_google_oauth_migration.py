"""Tests for the core_009 migration: google_oauth_credentials → butler_secrets."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"
CORE_MIGRATIONS_DIR = ALEMBIC_DIR / "versions" / "core"
MIGRATION_FILE = CORE_MIGRATIONS_DIR / "009_migrate_google_oauth_to_butler_secrets.py"


def _load_migration():
    """Load the core_009 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("migration_core_009", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCore009MigrationFile:
    """Tests for the 009_migrate_google_oauth_to_butler_secrets migration."""

    def test_migration_file_exists(self):
        """The migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self):
        """The migration has correct revision chain identifiers."""
        mod = _load_migration()
        assert mod.revision == "core_009"
        assert mod.down_revision == "core_008"
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

    def test_upgrade_reads_from_old_table(self):
        """The upgrade SQL references google_oauth_credentials."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "google_oauth_credentials" in source

    def test_upgrade_writes_to_butler_secrets(self):
        """The upgrade SQL inserts into butler_secrets."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "butler_secrets" in source
        assert "INSERT INTO" in source

    def test_upgrade_migrates_all_four_keys(self):
        """The upgrade SQL covers all four Google credential keys."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for key in [
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_REFRESH_TOKEN",
            "GOOGLE_OAUTH_SCOPES",
        ]:
            assert key in source, f"Key {key!r} not found in upgrade SQL"

    def test_upgrade_drops_old_table(self):
        """The upgrade SQL drops google_oauth_credentials."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DROP TABLE" in source
        assert "google_oauth_credentials" in source

    def test_upgrade_uses_on_conflict_upsert(self):
        """The upgrade SQL uses ON CONFLICT DO UPDATE for idempotency."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ON CONFLICT" in source
        assert "DO UPDATE" in source

    def test_upgrade_guards_against_missing_table(self):
        """The upgrade SQL handles the case where the old table doesn't exist."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Should check information_schema or use IF EXISTS
        assert "information_schema" in source or "IF EXISTS" in source

    def test_downgrade_recreates_old_table(self):
        """The downgrade SQL re-creates google_oauth_credentials."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "CREATE TABLE" in source
        assert "google_oauth_credentials" in source

    def test_downgrade_removes_butler_secrets_rows(self):
        """The downgrade SQL cleans up the four Google rows from butler_secrets."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DELETE FROM" in source
        assert "butler_secrets" in source
        # The migration uses module-level _KEY_* variables (f-string style),
        # so we check that all four variable names are referenced in the DELETE
        for var in [
            "_KEY_CLIENT_ID",
            "_KEY_CLIENT_SECRET",
            "_KEY_REFRESH_TOKEN",
            "_KEY_SCOPES",
        ]:
            assert var in source, f"Variable {var!r} not referenced in downgrade DELETE SQL"
