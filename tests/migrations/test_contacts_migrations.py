"""Tests for the contacts module sync tables migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "contacts" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_contacts_sync_tables.py"


def _load_migration(
    filename: str = "001_contacts_sync_tables.py",
    module_name: str = "contacts_sync_tables_migration",
):
    """Load a migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
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
        assert mod.revision == "contacts_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("contacts",)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """The migration declares upgrade()/downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_creates_contacts_source_accounts(self) -> None:
        """Upgrade creates the contacts_source_accounts table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS contacts_source_accounts" in source

    def test_source_accounts_has_required_columns(self) -> None:
        """contacts_source_accounts has all spec §4.3 columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
            "provider",
            "account_id",
            "subject_email",
            "connected_at",
            "last_success_at",
        ):
            assert column in source

    def test_source_accounts_has_composite_pk(self) -> None:
        """contacts_source_accounts PK covers (provider, account_id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The PRIMARY KEY clause must appear in the source_accounts block
        accounts_block_start = source.find("contacts_source_accounts")
        accounts_block_end = source.find("contacts_sync_state", accounts_block_start)
        accounts_block = source[accounts_block_start:accounts_block_end]
        assert "PRIMARY KEY (provider, account_id)" in accounts_block

    def test_creates_contacts_sync_state(self) -> None:
        """Upgrade creates the contacts_sync_state table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS contacts_sync_state" in source

    def test_sync_state_has_required_columns(self) -> None:
        """contacts_sync_state has all spec §4.3 columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
            "provider",
            "account_id",
            "sync_cursor",
            "cursor_issued_at",
            "last_full_sync_at",
            "last_incremental_sync_at",
            "last_error",
        ):
            assert column in source

    def test_sync_state_has_composite_pk(self) -> None:
        """contacts_sync_state PK covers (provider, account_id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        sync_block_start = source.find("contacts_sync_state")
        sync_block_end = source.find("contacts_source_links", sync_block_start)
        sync_block = source[sync_block_start:sync_block_end]
        assert "PRIMARY KEY (provider, account_id)" in sync_block

    def test_creates_contacts_source_links(self) -> None:
        """Upgrade creates the contacts_source_links table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS contacts_source_links" in source

    def test_source_links_has_required_columns(self) -> None:
        """contacts_source_links has all spec §4.3 columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
            "provider",
            "account_id",
            "external_contact_id",
            "local_contact_id",
            "source_etag",
            "first_seen_at",
            "last_seen_at",
            "deleted_at",
        ):
            assert column in source

    def test_source_links_has_composite_pk(self) -> None:
        """contacts_source_links PK covers (provider, account_id, external_contact_id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "PRIMARY KEY (provider, account_id, external_contact_id)" in source

    def test_source_links_fk_to_contacts(self) -> None:
        """contacts_source_links FK to contacts(id) is applied conditionally."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "to_regclass(format('%I.contacts', current_schema()))" in source
        assert "ADD CONSTRAINT contacts_source_links_local_contact_id_fkey" in source
        assert "REFERENCES contacts(id)" in source
        assert "ON DELETE SET NULL" in source

    def test_creates_required_indexes(self) -> None:
        """Upgrade creates performance indexes."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        expected_indexes = (
            "idx_contacts_source_links_local_contact",
            "idx_contacts_source_links_last_seen",
            "idx_contacts_source_accounts_last_success",
        )
        for index_name in expected_indexes:
            assert index_name in source, f"Missing index: {index_name}"


class TestDowngradeSQL:
    def test_drops_all_three_tables(self) -> None:
        """Downgrade removes all three tables."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS contacts_source_links" in source
        assert "DROP TABLE IF EXISTS contacts_sync_state" in source
        assert "DROP TABLE IF EXISTS contacts_source_accounts" in source

    def test_drops_indexes(self) -> None:
        """Downgrade removes the custom indexes."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_contacts_source_links_local_contact" in source
        assert "DROP INDEX IF EXISTS idx_contacts_source_links_last_seen" in source
