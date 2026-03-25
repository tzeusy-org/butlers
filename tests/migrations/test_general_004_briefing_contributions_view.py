"""Tests for roster/general/migrations/004_briefing_contributions_view.py

Validates the Alembic migration that creates the general.v_briefing_contributions
SQL view and grants SELECT on each specialist schema's state table to the General
butler's DB role.

These are static source-inspection tests that do not require a live database.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "general" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "004_briefing_contributions_view.py"

_SPECIALIST_SCHEMAS = (
    "education",
    "finance",
    "health",
    "home",
    "relationship",
    "travel",
)


def _load_migration():
    """Load the migration module dynamically without requiring an Alembic env."""
    spec = importlib.util.spec_from_file_location(
        "gen_004_briefing_contributions_view", MIGRATION_FILE
    )
    assert spec is not None, f"Could not locate spec for {MIGRATION_FILE}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestFileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file 004_briefing_contributions_view.py exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Revision ID is gen_004."""
        mod = _load_migration()
        assert mod.revision == "gen_004"

    def test_down_revision(self) -> None:
        """Migration chains from gen_003."""
        mod = _load_migration()
        assert mod.down_revision == "gen_003"

    def test_branch_labels_none(self) -> None:
        """branch_labels is None (not a branch root)."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        """Migration declares upgrade() and downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_general_role_constant(self) -> None:
        """_GENERAL_ROLE is set to butler_general_rw."""
        mod = _load_migration()
        assert hasattr(mod, "_GENERAL_ROLE")
        assert mod._GENERAL_ROLE == "butler_general_rw"

    def test_specialist_schemas_constant(self) -> None:
        """_SPECIALIST_SCHEMAS lists all six expected specialist butlers."""
        mod = _load_migration()
        assert hasattr(mod, "_SPECIALIST_SCHEMAS")
        for schema in _SPECIALIST_SCHEMAS:
            assert schema in mod._SPECIALIST_SCHEMAS, f"Missing specialist schema: {schema}"

    def test_specialist_schemas_count(self) -> None:
        """_SPECIALIST_SCHEMAS has exactly 6 entries."""
        mod = _load_migration()
        assert len(mod._SPECIALIST_SCHEMAS) == len(_SPECIALIST_SCHEMAS)


# ---------------------------------------------------------------------------
# Upgrade SQL
# ---------------------------------------------------------------------------


class TestUpgradeSQL:
    def test_creates_view_in_general_schema(self) -> None:
        """Upgrade creates the view in the general schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "general.v_briefing_contributions" in source

    def test_view_uses_create_or_replace(self) -> None:
        """Upgrade uses CREATE OR REPLACE VIEW for idempotency."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE OR REPLACE VIEW" in source

    def test_view_unions_all_specialist_schemas(self) -> None:
        """Upgrade UNIONs state entries from all six specialist schemas.

        The upgrade function iterates over _SPECIALIST_SCHEMAS to build the
        view SQL, so the schema names are not literally in the function source.
        We verify the constant is referenced and that the SQL generation helper
        iterates over it.
        """
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Upgrade must reference _SPECIALIST_SCHEMAS to build the UNION
        assert "_SPECIALIST_SCHEMAS" in source
        # The constant itself must contain all specialist schemas
        for schema in _SPECIALIST_SCHEMAS:
            assert schema in mod._SPECIALIST_SCHEMAS, (
                f"Missing specialist schema in _SPECIALIST_SCHEMAS: {schema}"
            )

    def test_view_filters_briefing_daily_prefix(self) -> None:
        """Upgrade filters rows to key LIKE 'briefing/daily/%'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "briefing/daily/%" in source

    def test_view_selects_butler_key_value_columns(self) -> None:
        """Upgrade SELECT includes butler, key, value columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "butler" in source
        assert "key" in source
        assert "value" in source

    def test_view_uses_union_all(self) -> None:
        """Upgrade view uses UNION ALL (not UNION) for performance."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "UNION ALL" in source

    def test_grants_select_on_specialist_state_tables(self) -> None:
        """Upgrade grants SELECT on each specialist schema's state table.

        The upgrade iterates over _SPECIALIST_SCHEMAS calling the grant helper,
        so individual schema names are not in the function source text.
        We verify that the grant helper is called and _SPECIALIST_SCHEMAS is used.
        """
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The upgrade must invoke the per-schema SELECT grant helper
        assert "_grant_select_on_state_if_exists" in source
        # And must iterate over the schemas constant
        assert "_SPECIALIST_SCHEMAS" in source

    def test_grants_schema_usage(self) -> None:
        """Upgrade grants USAGE on each specialist schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "_grant_schema_usage_if_exists" in source

    def test_grants_target_general_role(self) -> None:
        """Upgrade grants are targeted to the General butler role."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "butler_general_rw" in source or "_GENERAL_ROLE" in source

    def test_grant_helpers_check_role_existence(self) -> None:
        """Grant helper functions guard on pg_roles existence."""
        mod = _load_migration()
        # Inspect the helper source directly
        grant_source = inspect.getsource(mod._grant_select_on_state_if_exists)
        assert "pg_roles" in grant_source
        assert "rolname" in grant_source

    def test_grant_helpers_check_table_existence(self) -> None:
        """Grant helper functions guard on table existence via to_regclass."""
        mod = _load_migration()
        grant_source = inspect.getsource(mod._grant_select_on_state_if_exists)
        assert "to_regclass" in grant_source

    def test_grant_helpers_handle_insufficient_privilege(self) -> None:
        """Grant helper functions catch insufficient_privilege exceptions."""
        mod = _load_migration()
        grant_source = inspect.getsource(mod._grant_select_on_state_if_exists)
        assert "insufficient_privilege" in grant_source


# ---------------------------------------------------------------------------
# Downgrade SQL
# ---------------------------------------------------------------------------


class TestDowngradeSQL:
    def test_drops_view(self) -> None:
        """Downgrade drops general.v_briefing_contributions."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP VIEW IF EXISTS general.v_briefing_contributions" in source

    def test_revokes_select_on_specialist_state_tables(self) -> None:
        """Downgrade revokes SELECT on each specialist schema's state table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "_revoke_select_on_state_if_exists" in source

    def test_revokes_schema_usage(self) -> None:
        """Downgrade revokes USAGE on each specialist schema."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "_revoke_schema_usage_if_exists" in source

    def test_revoke_covers_all_specialist_schemas(self) -> None:
        """Downgrade revoke loop covers all six specialist schemas.

        The downgrade iterates over _SPECIALIST_SCHEMAS, so individual schema
        names are not in the function source. We verify the constant is used.
        """
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "_SPECIALIST_SCHEMAS" in source
        # The constant itself covers all six schemas
        for schema in _SPECIALIST_SCHEMAS:
            assert schema in mod._SPECIALIST_SCHEMAS, (
                f"Missing specialist schema in _SPECIALIST_SCHEMAS: {schema}"
            )

    def test_revoke_helpers_check_table_existence(self) -> None:
        """Revoke helper functions guard on to_regclass existence."""
        mod = _load_migration()
        revoke_source = inspect.getsource(mod._revoke_select_on_state_if_exists)
        assert "to_regclass" in revoke_source

    def test_revoke_helpers_handle_insufficient_privilege(self) -> None:
        """Revoke helper functions catch insufficient_privilege exceptions."""
        mod = _load_migration()
        revoke_source = inspect.getsource(mod._revoke_select_on_state_if_exists)
        assert "insufficient_privilege" in revoke_source
