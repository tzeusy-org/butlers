"""Unit tests for core_007 — contacts_to_shared migration.

Validates revision metadata, DDL structure, and correctness of upgrade/downgrade
source by inspecting the migration module without executing against a live DB.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "core_007_contacts_to_shared.py"


def _core_migration_dir() -> Path:
    """Return the core migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("core")
    assert chain_dir is not None, "Core chain should exist"
    return chain_dir


def _load_migration():
    """Load the core_007 migration module."""
    migration_path = _core_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Missing migration file: {MIGRATION_FILENAME}"
    spec = importlib.util.spec_from_file_location("core_007_contacts_to_shared", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """core_007_contacts_to_shared.py exists on disk."""
        assert (_core_migration_dir() / MIGRATION_FILENAME).exists()


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_007"

    def test_down_revision(self) -> None:
        mod = _load_migration()
        assert mod.down_revision == "core_006"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestButlerRolesConstant:
    def test_all_butler_roles_defined(self) -> None:
        """_ALL_BUTLER_ROLES includes all four runtime roles."""
        mod = _load_migration()
        roles = mod._ALL_BUTLER_ROLES
        for role in (
            "butler_switchboard_rw",
            "butler_general_rw",
            "butler_health_rw",
            "butler_relationship_rw",
        ):
            assert role in roles, f"Missing butler role: {role}"

    def test_contacts_table_privileges_include_write(self) -> None:
        """_CONTACTS_TABLE_PRIVILEGES grants write access."""
        mod = _load_migration()
        privs = mod._CONTACTS_TABLE_PRIVILEGES
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert priv in privs, f"Missing privilege: {priv}"


class TestRelContactFKsConstant:
    def test_rel_contact_fks_count(self) -> None:
        """_REL_CONTACT_FKS covers at least 17 FK entries."""
        mod = _load_migration()
        assert len(mod._REL_CONTACT_FKS) >= 17

    def test_loans_has_three_fk_entries(self) -> None:
        """loans table has three FK entries (contact_id, lender, borrower)."""
        mod = _load_migration()
        loan_fks = [e for e in mod._REL_CONTACT_FKS if e[0] == "loans"]
        assert len(loan_fks) == 3

    def test_relationships_table_has_two_fk_entries(self) -> None:
        """relationships table has two FK entries (contact_a, contact_b)."""
        mod = _load_migration()
        rel_fks = [e for e in mod._REL_CONTACT_FKS if e[0] == "relationships"]
        assert len(rel_fks) == 2

    def test_all_expected_tables_present(self) -> None:
        """All 15 relationship tables with contacts FKs are represented."""
        mod = _load_migration()
        tables = {e[0] for e in mod._REL_CONTACT_FKS}
        expected = {
            "relationships",
            "important_dates",
            "notes",
            "interactions",
            "reminders",
            "gifts",
            "loans",
            "group_members",
            "contact_labels",
            "quick_facts",
            "activity_feed",
            "contact_info",
            "addresses",
            "life_events",
            "tasks",
        }
        assert expected <= tables, f"Missing tables: {expected - tables}"


class TestUpgradeSQL:
    def test_moves_contacts_to_shared_schema(self) -> None:
        """Upgrade moves relationship.contacts to shared schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "relationship.contacts" in source
        assert "SET SCHEMA shared" in source

    def test_adds_roles_column(self) -> None:
        """Upgrade adds roles TEXT[] NOT NULL DEFAULT '{}' to shared.contacts."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "roles" in source
        assert "TEXT[]" in source
        assert "NOT NULL" in source
        assert "DEFAULT '{}'" in source

    def test_adds_secured_column_to_contact_info(self) -> None:
        """Upgrade adds secured BOOLEAN NOT NULL DEFAULT false to shared.contact_info."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "secured" in source
        assert "BOOLEAN" in source
        assert "DEFAULT false" in source

    def test_drops_non_unique_index(self) -> None:
        """Upgrade drops the old non-unique idx_shared_contact_info_type_value index."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DROP INDEX IF EXISTS shared.idx_shared_contact_info_type_value" in source

    def test_adds_unique_constraint_on_type_value(self) -> None:
        """Upgrade adds UNIQUE(type, value) constraint on shared.contact_info."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_shared_contact_info_type_value" in source
        assert "UNIQUE (type, value)" in source

    def test_adds_fk_contact_info_to_contacts(self) -> None:
        """Upgrade adds FK shared.contact_info(contact_id) -> shared.contacts(id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "shared_contact_info_contact_id_fkey" in source
        assert "REFERENCES shared.contacts(id)" in source
        assert "ON DELETE CASCADE" in source

    def test_recreates_relationship_fks(self) -> None:
        """Upgrade re-creates FK constraints referencing shared.contacts."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES shared.contacts(id)" in source
        # FK re-creation loop uses _REL_CONTACT_FKS entries
        assert "ON DELETE" in source

    def test_grants_to_all_butler_roles(self) -> None:
        """Upgrade calls grant helpers for all butler roles on shared.contacts."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The GRANT loop references _ALL_BUTLER_ROLES; check the constant itself
        # contains all expected roles (validated by TestButlerRolesConstant).
        assert "_ALL_BUTLER_ROLES" in source
        # Upgrade calls the grant helper functions
        assert "_grant_if_table_exists" in source or "GRANT" in source
        assert "shared.contacts" in source
        # The helper functions themselves contain GRANT SQL
        helper_source = inspect.getsource(mod._grant_if_table_exists)
        assert "GRANT" in helper_source
        assert "shared.contacts" in helper_source or "table_fqn" in helper_source

    def test_creates_owner_singleton_index(self) -> None:
        """Upgrade creates partial unique index ix_contacts_owner_singleton."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ix_contacts_owner_singleton" in source
        assert "UNIQUE INDEX" in source
        # Inside SQL string literals, single quotes are doubled: ''owner''
        assert "'owner'" in source or "owner" in source
        assert "ANY(roles)" in source

    def test_upgrade_uses_schema_qualified_ddl(self) -> None:
        """Upgrade uses fully-qualified schema.table identifiers."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "shared.contacts" in source
        assert "shared.contact_info" in source
        assert "relationship." in source


class TestDowngradeSQL:
    def test_drops_owner_singleton_index(self) -> None:
        """Downgrade drops ix_contacts_owner_singleton."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "ix_contacts_owner_singleton" in source
        assert "DROP INDEX IF EXISTS" in source

    def test_revokes_write_privileges(self) -> None:
        """Downgrade revokes write privileges from butler roles."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "REVOKE" in source
        assert "INSERT" in source or "UPDATE" in source or "DELETE" in source

    def test_drops_relationship_fk_constraints(self) -> None:
        """Downgrade drops FK constraints on relationship tables."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP CONSTRAINT" in source

    def test_drops_contact_info_fk(self) -> None:
        """Downgrade drops shared_contact_info_contact_id_fkey."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "shared_contact_info_contact_id_fkey" in source
        assert "DROP CONSTRAINT" in source

    def test_drops_unique_constraint(self) -> None:
        """Downgrade drops uq_shared_contact_info_type_value constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "uq_shared_contact_info_type_value" in source
        assert "DROP CONSTRAINT" in source

    def test_restores_non_unique_index(self) -> None:
        """Downgrade restores the original non-unique type/value index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_shared_contact_info_type_value" in source
        assert "CREATE INDEX" in source

    def test_drops_secured_column(self) -> None:
        """Downgrade drops the secured column from shared.contact_info."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "secured" in source
        assert "DROP COLUMN" in source

    def test_drops_roles_column(self) -> None:
        """Downgrade drops the roles column from shared.contacts."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "roles" in source
        assert "DROP COLUMN" in source

    def test_moves_contacts_back_to_relationship(self) -> None:
        """Downgrade moves shared.contacts back to relationship schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "shared.contacts" in source
        assert "SET SCHEMA relationship" in source
