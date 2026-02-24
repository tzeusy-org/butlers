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


# ---------------------------------------------------------------------------
# Tests for 002_contact_info_shared.py
# ---------------------------------------------------------------------------

MIGRATION_FILE_002 = MIGRATION_DIR / "002_contact_info_shared.py"


def _load_migration_002():
    """Load the 002_contact_info_shared migration module dynamically."""
    return _load_migration("002_contact_info_shared.py", "contacts_contact_info_shared_migration")


class TestMigration002FileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file 002_contact_info_shared.py exists on disk."""
        assert MIGRATION_FILE_002.exists(), f"Migration file not found at {MIGRATION_FILE_002}"


class TestMigration002RevisionMetadata:
    def test_revision_identifiers(self) -> None:
        """The migration has correct revision metadata."""
        mod = _load_migration_002()
        assert mod.revision == "contacts_002"
        assert mod.down_revision == "contacts_001"
        assert mod.branch_labels is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """The migration declares upgrade()/downgrade() callables."""
        mod = _load_migration_002()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMigration002UpgradeSQL:
    def test_creates_shared_schema(self) -> None:
        """Upgrade creates the shared schema if it doesn't exist."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE SCHEMA IF NOT EXISTS shared" in source

    def test_creates_shared_contact_info(self) -> None:
        """Upgrade creates the shared.contact_info table."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS shared.contact_info" in source

    def test_shared_contact_info_has_required_columns(self) -> None:
        """shared.contact_info has all required columns."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        for column in ("id", "contact_id", "type", "value", "label", "is_primary", "created_at"):
            assert column in source

    def test_creates_required_indexes(self) -> None:
        """Upgrade creates performance indexes on shared.contact_info."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_shared_contact_info_type_value" in source
        assert "idx_shared_contact_info_contact_id" in source

    def test_grants_write_access_to_contacts_module_roles(self) -> None:
        """Upgrade grants INSERT/UPDATE/DELETE on shared.contact_info to butler roles."""
        mod = _load_migration_002()
        # The privilege constant should include write ops
        assert hasattr(mod, "_CONTACT_INFO_TABLE_PRIVILEGES")
        assert "INSERT" in mod._CONTACT_INFO_TABLE_PRIVILEGES
        assert "UPDATE" in mod._CONTACT_INFO_TABLE_PRIVILEGES
        assert "DELETE" in mod._CONTACT_INFO_TABLE_PRIVILEGES
        # Check that the known contacts-module butler roles are present in the module constant
        assert hasattr(mod, "_CONTACTS_MODULE_ROLES")
        for role in ("butler_general_rw", "butler_health_rw", "butler_relationship_rw"):
            assert role in mod._CONTACTS_MODULE_ROLES

    def test_migrates_existing_data(self) -> None:
        """Upgrade copies existing per-schema contact_info rows to shared."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "INSERT INTO shared.contact_info" in source
        assert "ON CONFLICT (id) DO NOTHING" in source


class TestMigration002DowngradeSQL:
    def test_drops_shared_contact_info(self) -> None:
        """Downgrade removes shared.contact_info."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS shared.contact_info" in source

    def test_drops_indexes(self) -> None:
        """Downgrade removes the custom indexes."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "idx_shared_contact_info_contact_id" in source
        assert "idx_shared_contact_info_type_value" in source

    def test_revokes_write_privileges(self) -> None:
        """Downgrade revokes write privileges from contacts module roles."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "REVOKE" in source
        assert "INSERT" in source or "UPDATE" in source
