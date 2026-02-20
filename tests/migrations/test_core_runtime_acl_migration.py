"""Tests for the core_011 runtime ACL migration file."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"
CORE_MIGRATIONS_DIR = ALEMBIC_DIR / "versions" / "core"
MIGRATION_FILE = CORE_MIGRATIONS_DIR / "011_apply_schema_acl_for_runtime_roles.py"


def _load_migration():
    """Load the core_011 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("migration_core_011", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCore011RuntimeAclMigration:
    """Tests for the 011_apply_schema_acl_for_runtime_roles migration."""

    def test_migration_file_exists(self):
        """The migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self):
        """The migration has correct revision chain identifiers."""
        mod = _load_migration()
        assert mod.revision == "core_011"
        assert mod.down_revision == "core_010"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_runtime_role_model_constants(self):
        """Migration constants include all expected schemas and runtime roles."""
        mod = _load_migration()
        assert mod._BUTLER_SCHEMAS == (
            "general",
            "health",
            "messenger",
            "relationship",
            "switchboard",
        )
        assert mod._SHARED_SCHEMA == "shared"
        assert mod._RUNTIME_ROLES == {
            "general": "butler_general_rw",
            "health": "butler_health_rw",
            "messenger": "butler_messenger_rw",
            "relationship": "butler_relationship_rw",
            "switchboard": "butler_switchboard_rw",
        }

    def test_upgrade_applies_acl_primitives(self):
        """Upgrade path wires baseline revoke, role creation, and per-role ACL grants."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "_apply_public_baseline_revokes" in source
        assert "_create_runtime_role_best_effort" in source
        assert "_grant_connect_and_search_path" in source
        assert "_grant_own_schema_privileges" in source
        assert "_grant_shared_schema_read_privileges" in source
        assert "_revoke_cross_schema_privileges" in source
        assert "_apply_default_privileges" in source

    def test_acl_helpers_include_expected_sql_contract(self):
        """Migration helper SQL includes least-privilege ACL operations."""
        mod = _load_migration()

        own_source = inspect.getsource(mod._grant_own_schema_privileges)
        assert "GRANT USAGE, CREATE ON SCHEMA" in own_source
        assert "GRANT EXECUTE ON ALL FUNCTIONS" in own_source

        shared_source = inspect.getsource(mod._grant_shared_schema_read_privileges)
        assert "REVOKE CREATE ON SCHEMA" in shared_source
        assert "ON ALL TABLES IN SCHEMA" in shared_source

        cross_source = inspect.getsource(mod._revoke_cross_schema_privileges)
        assert "REVOKE ALL ON SCHEMA" in cross_source
        assert "REVOKE ALL ON ALL TABLES" in cross_source

        defaults_source = inspect.getsource(mod._apply_default_privileges)
        assert mod._OWN_TABLE_PRIVILEGES == "SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES"
        assert "ALTER DEFAULT PRIVILEGES" in defaults_source
        assert "ON TABLES TO" in defaults_source

    def test_downgrade_revoke_cleanup_exists(self):
        """Downgrade path performs best-effort privilege cleanup."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "_revoke_role_access" in source
