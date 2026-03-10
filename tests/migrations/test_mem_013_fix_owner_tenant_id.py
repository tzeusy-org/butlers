"""Tests for mem_013 fix_owner_tenant_id migration.

Validates revision metadata, SQL correctness, and idempotency of the
data back-fill migration that normalises tenant_id='owner' -> 'shared'.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE_013 = MIGRATION_DIR / "013_fix_owner_tenant_id.py"


def _load_migration_013():
    """Load mem_013 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("mem_013_fix_owner_tenant_id", MIGRATION_FILE_013)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMem013FileStructure:
    def test_migration_file_exists(self) -> None:
        """The mem_013 migration file exists on disk."""
        assert MIGRATION_FILE_013.exists(), f"Migration file not found at {MIGRATION_FILE_013}"

    def test_revision_identifiers(self) -> None:
        """mem_013 has correct revision metadata chaining from mem_012."""
        mod = _load_migration_013()
        assert mod.revision == "mem_013"
        assert mod.down_revision == "mem_012"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """Migration declares upgrade()/downgrade() callables."""
        mod = _load_migration_013()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMem013UpgradeSQL:
    def test_updates_owner_tenant_id_to_shared(self) -> None:
        """Upgrade SQL sets tenant_id = 'shared' where tenant_id = 'owner'."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "tenant_id = 'owner'" in source
        assert "tenant_id = 'shared'" in source

    def test_updates_entities_table(self) -> None:
        """Upgrade SQL operates on the entities table."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "UPDATE entities" in source

    def test_skips_duplicates_with_not_exists(self) -> None:
        """Upgrade SQL guards against unique constraint violations using NOT EXISTS."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "NOT EXISTS" in source

    def test_duplicate_guard_matches_on_canonical_name_and_entity_type(self) -> None:
        """Duplicate guard checks both canonical_name and entity_type columns."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "canonical_name" in source
        assert "entity_type" in source

    def test_duplicate_guard_checks_shared_tenant(self) -> None:
        """Duplicate guard looks for existing rows with tenant_id = 'shared'."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        # The subquery should reference 'shared' in the NOT EXISTS guard
        assert source.count("'shared'") >= 2  # SET and the guard subquery

    def test_also_updates_updated_at(self) -> None:
        """Upgrade SQL refreshes updated_at to now() for migrated rows."""
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "updated_at = now()" in source

    def test_upgrade_is_idempotent_by_design(self) -> None:
        """A second run of upgrade produces no rows to update (no 'owner' tenants left)."""
        # This is a structural test: the WHERE clause filters exactly tenant_id='owner',
        # so if there are no such rows the UPDATE is a no-op.
        mod = _load_migration_013()
        source = inspect.getsource(mod.upgrade)
        assert "WHERE tenant_id = 'owner'" in source


class TestMem013Downgrade:
    def test_downgrade_is_noop(self) -> None:
        """Downgrade is intentionally a no-op (irreversible data migration)."""
        mod = _load_migration_013()
        # downgrade should exist and be callable but have no side effects
        # (it should be effectively empty — just 'pass').
        assert callable(mod.downgrade)
        source = inspect.getsource(mod.downgrade)
        # Should not contain any SQL UPDATE/DELETE/INSERT statements
        assert "UPDATE" not in source
        assert "INSERT" not in source
        assert "DELETE" not in source
