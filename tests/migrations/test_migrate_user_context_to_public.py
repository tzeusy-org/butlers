"""Tests for the core_046_migrate_user_context_to_public Alembic migration.

Covers:
  - File layout and module loadability
  - Revision chain (core_046 revises core_045)
  - upgrade() moves shared.user_context to public schema
  - upgrade() drops the shared schema after migration
  - downgrade() recreates shared schema and moves table back
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_046_migrate_user_context_to_public.py"


def _load_migration():
    """Dynamically load the core_046 migration module."""
    spec = importlib.util.spec_from_file_location(
        "core_046_migrate_user_context_to_public", MIGRATION_FILE
    )
    assert spec is not None, f"Cannot locate migration at {MIGRATION_FILE}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """core_046_migrate_user_context_to_public.py exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"

    def test_migration_file_loadable(self) -> None:
        """core_046_migrate_user_context_to_public.py can be imported without errors."""
        mod = _load_migration()
        assert mod is not None


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is 'core_046'."""
        mod = _load_migration()
        assert mod.revision == "core_046"

    def test_down_revision(self) -> None:
        """Migration revises core_045."""
        mod = _load_migration()
        assert mod.down_revision == "core_045"

    def test_branch_labels_none(self) -> None:
        """Migration has no branch label (belongs to linear core chain)."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        """downgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# Upgrade behaviour
# ---------------------------------------------------------------------------


class TestUpgrade:
    def test_moves_table_to_public_schema(self) -> None:
        """upgrade() uses ALTER TABLE ... SET SCHEMA public."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ALTER TABLE shared.user_context SET SCHEMA public" in source

    def test_drops_shared_schema(self) -> None:
        """upgrade() drops the shared schema after migration."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DROP SCHEMA IF EXISTS shared" in source

    def test_verifies_shared_schema_empty(self) -> None:
        """upgrade() checks information_schema.tables for remaining shared tables."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "information_schema.tables" in source
        assert "table_schema" in source
        assert "'shared'" in source

    def test_does_not_recreate_table(self) -> None:
        """upgrade() does not use CREATE TABLE (uses SET SCHEMA instead)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" not in source


# ---------------------------------------------------------------------------
# Downgrade behaviour
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_recreates_shared_schema(self) -> None:
        """downgrade() recreates the shared schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "CREATE SCHEMA IF NOT EXISTS shared" in source

    def test_moves_table_back_to_shared(self) -> None:
        """downgrade() uses ALTER TABLE public.user_context SET SCHEMA shared."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "ALTER TABLE public.user_context SET SCHEMA shared" in source
