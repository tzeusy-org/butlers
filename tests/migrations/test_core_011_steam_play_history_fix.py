"""Unit tests for core_011_steam_play_history_fix migration.

Verifies:
- Revision metadata (ID, chain linkage)
- upgrade() DDL: adds steam_account_id, app_name; renames play_date → date;
  updates unique constraint; adds FK
- downgrade() reverses DDL
- Idempotency guards (IF NOT EXISTS / DO $$ ... EXCEPTION ...)
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_011_steam_play_history_fix.py"


def _load_migration():
    """Dynamically load the core_011 migration module."""
    spec = importlib.util.spec_from_file_location("core_011", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file exists at the expected path."""
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_migration_file_is_python(self) -> None:
        """The migration file is a .py file."""
        assert MIGRATION_FILE.suffix == ".py"


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """revision == 'core_011'."""
        mod = _load_migration()
        assert mod.revision == "core_011"

    def test_down_revision(self) -> None:
        """down_revision points to core_010."""
        mod = _load_migration()
        assert mod.down_revision == "core_010"

    def test_branch_labels_are_none(self) -> None:
        """branch_labels is None."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is callable."""
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        """downgrade() is callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_adds_steam_account_id_column(self) -> None:
        """upgrade() adds steam_account_id UUID column."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "steam_account_id" in source
        assert "UUID" in source

    def test_adds_app_name_column(self) -> None:
        """upgrade() adds app_name TEXT column."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "app_name" in source
        assert "TEXT" in source

    def test_renames_play_date_to_date(self) -> None:
        """upgrade() renames play_date column to date."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "play_date" in source
        assert "RENAME COLUMN play_date TO date" in source

    def test_adds_new_unique_constraint(self) -> None:
        """upgrade() adds new unique constraint on (steam_account_id, app_id, date)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_steam_play_history_account_app_date_v2" in source
        assert "steam_account_id, app_id, date" in source

    def test_drops_old_unique_constraint(self) -> None:
        """upgrade() drops the old unique constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_steam_play_history_account_app_date" in source
        assert "DROP CONSTRAINT" in source

    def test_adds_foreign_key(self) -> None:
        """upgrade() adds FK from steam_account_id to public.steam_accounts(id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "fk_steam_play_history_account" in source
        assert "REFERENCES public.steam_accounts(id)" in source
        assert "ON DELETE CASCADE" in source

    def test_backfill_is_guarded_by_table_existence(self) -> None:
        """Back-fill update checks that public.steam_accounts exists first."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "to_regclass('public.steam_accounts')" in source

    def test_rename_is_idempotent(self) -> None:
        """Rename DDL is wrapped in a DO block checking column existence."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "information_schema.columns" in source
        assert "column_name  = 'play_date'" in source or "column_name = 'play_date'" in source

    def test_targets_connectors_schema(self) -> None:
        """All DDL targets the connectors schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "connectors.steam_play_history" in source

    def test_fk_is_guarded_for_idempotency(self) -> None:
        """FK creation is inside an idempotent DO block checking constraint existence."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The FK block must check whether the constraint already exists.
        assert "fk_steam_play_history_account" in source
        assert "pg_constraint" in source


class TestDowngradeSQL:
    def test_drops_fk(self) -> None:
        """downgrade() drops the FK constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "fk_steam_play_history_account" in source
        assert "DROP CONSTRAINT" in source

    def test_drops_new_unique_constraint(self) -> None:
        """downgrade() drops the new unique constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "uq_steam_play_history_account_app_date_v2" in source

    def test_renames_date_back_to_play_date(self) -> None:
        """downgrade() renames date column back to play_date."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "RENAME COLUMN date TO play_date" in source

    def test_restores_old_unique_constraint(self) -> None:
        """downgrade() restores the old unique constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "uq_steam_play_history_account_app_date" in source
        assert "steam_id, app_id, play_date" in source

    def test_drops_added_columns(self) -> None:
        """downgrade() drops the steam_account_id and app_name columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP COLUMN IF EXISTS steam_account_id" in source
        assert "DROP COLUMN IF EXISTS app_name" in source
