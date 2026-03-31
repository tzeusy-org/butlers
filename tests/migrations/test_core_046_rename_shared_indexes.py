"""Unit tests for core_046_rename_shared_indexes migration.

Verifies:
1. Migration file exists and is importable.
2. Revision metadata is correct (ID core_046, chains from core_045).
3. upgrade() renames all shared_ prefixed indexes and constraints on public
   schema tables to non-shared equivalents.
4. downgrade() restores the old shared_ names.
5. All renames use IF EXISTS guards (idempotent / safe on collapsed DBs).

These are pure-unit tests that inspect source code without executing SQL.
No Docker / PostgreSQL container is required.

Issue: bu-zszt.4
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_046_rename_shared_indexes.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_migration():
    """Dynamically load the core_046 migration module."""
    spec = importlib.util.spec_from_file_location("core_046_rename_shared_indexes", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_migration_file_is_python(self) -> None:
        assert MIGRATION_FILE.suffix == ".py"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_046"

    def test_down_revision(self) -> None:
        """Must chain from core_045."""
        mod = _load_migration()
        assert mod.down_revision == "core_045"

    def test_branch_labels_are_none(self) -> None:
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — entities table renames
# ---------------------------------------------------------------------------


class TestUpgradeEntitiesRenames:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_renames_check_constraint(self) -> None:
        assert "chk_shared_entities_entity_type" in self._src
        assert "chk_entities_entity_type" in self._src

    def test_renames_tenant_canonical_index(self) -> None:
        assert "idx_shared_entities_tenant_canonical" in self._src
        assert "idx_entities_tenant_canonical" in self._src

    def test_renames_aliases_index(self) -> None:
        assert "idx_shared_entities_aliases" in self._src
        assert "idx_entities_aliases" in self._src

    def test_renames_metadata_index(self) -> None:
        assert "idx_shared_entities_metadata" in self._src
        assert "idx_entities_metadata" in self._src

    def test_renames_name_index(self) -> None:
        assert "idx_shared_entities_name" in self._src
        assert "idx_entities_name" in self._src

    def test_renames_name_trgm_index(self) -> None:
        assert "idx_shared_entities_name_trgm" in self._src
        assert "idx_entities_name_trgm" in self._src

    def test_renames_aliases_trgm_index(self) -> None:
        assert "idx_shared_entities_aliases_trgm" in self._src
        assert "idx_entities_aliases_trgm" in self._src

    def test_renames_updated_at_index(self) -> None:
        assert "idx_shared_entities_updated_at" in self._src
        assert "idx_entities_updated_at" in self._src


# ---------------------------------------------------------------------------
# upgrade() — contact_info table renames
# ---------------------------------------------------------------------------


class TestUpgradeContactInfoRenames:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_renames_unique_constraint(self) -> None:
        assert "uq_shared_contact_info_type_value" in self._src
        assert "uq_contact_info_type_value" in self._src

    def test_renames_contact_id_index(self) -> None:
        assert "idx_shared_contact_info_contact_id" in self._src
        assert "idx_contact_info_contact_id" in self._src

    def test_renames_parent_id_index(self) -> None:
        assert "ix_shared_contact_info_parent_id" in self._src
        assert "ix_contact_info_parent_id" in self._src


# ---------------------------------------------------------------------------
# upgrade() — entity_info table renames
# ---------------------------------------------------------------------------


class TestUpgradeEntityInfoRenames:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_renames_unique_constraint(self) -> None:
        assert "uq_shared_entity_info_entity_type" in self._src
        assert "uq_entity_info_entity_type" in self._src

    def test_renames_entity_id_index(self) -> None:
        assert "idx_shared_entity_info_entity_id" in self._src
        assert "idx_entity_info_entity_id" in self._src

    def test_renames_type_index(self) -> None:
        assert "idx_shared_entity_info_type" in self._src
        assert "idx_entity_info_type" in self._src


# ---------------------------------------------------------------------------
# Idempotency guards — inspects the full module source (helpers + functions)
# ---------------------------------------------------------------------------


class TestIdempotencyGuards:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        # Use full module source so we capture the helper functions too.
        self._src = inspect.getsource(_load_migration())

    def test_uses_if_exists_for_indexes(self) -> None:
        """Index renames must check existence before renaming."""
        assert "IF EXISTS" in self._src
        assert "pg_indexes" in self._src

    def test_uses_if_exists_for_constraints(self) -> None:
        """Constraint renames must check existence via information_schema."""
        assert "information_schema.table_constraints" in self._src

    def test_uses_alter_index_rename(self) -> None:
        assert "ALTER INDEX" in self._src
        assert "RENAME TO" in self._src

    def test_uses_alter_table_rename_constraint(self) -> None:
        assert "RENAME CONSTRAINT" in self._src


# ---------------------------------------------------------------------------
# downgrade() — restores shared_ names
# ---------------------------------------------------------------------------


class TestDowngradeRestoresSharedNames:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().downgrade)

    def test_restores_entities_check_constraint(self) -> None:
        assert "chk_shared_entities_entity_type" in self._src

    def test_restores_entities_tenant_canonical_index(self) -> None:
        assert "idx_shared_entities_tenant_canonical" in self._src

    def test_restores_entities_aliases_index(self) -> None:
        assert "idx_shared_entities_aliases" in self._src

    def test_restores_entities_metadata_index(self) -> None:
        assert "idx_shared_entities_metadata" in self._src

    def test_restores_entities_name_index(self) -> None:
        assert "idx_shared_entities_name" in self._src

    def test_restores_entities_name_trgm_index(self) -> None:
        assert "idx_shared_entities_name_trgm" in self._src

    def test_restores_entities_aliases_trgm_index(self) -> None:
        assert "idx_shared_entities_aliases_trgm" in self._src

    def test_restores_entities_updated_at_index(self) -> None:
        assert "idx_shared_entities_updated_at" in self._src

    def test_restores_contact_info_unique_constraint(self) -> None:
        assert "uq_shared_contact_info_type_value" in self._src

    def test_restores_contact_info_contact_id_index(self) -> None:
        assert "idx_shared_contact_info_contact_id" in self._src

    def test_restores_contact_info_parent_id_index(self) -> None:
        assert "ix_shared_contact_info_parent_id" in self._src

    def test_restores_entity_info_unique_constraint(self) -> None:
        assert "uq_shared_entity_info_entity_type" in self._src

    def test_restores_entity_info_entity_id_index(self) -> None:
        assert "idx_shared_entity_info_entity_id" in self._src

    def test_restores_entity_info_type_index(self) -> None:
        assert "idx_shared_entity_info_type" in self._src

    def test_downgrade_uses_if_exists_guards(self) -> None:
        """Downgrade must also be idempotent (guards live in helpers)."""
        # Inspect the whole module to capture helper functions shared with downgrade.
        full_src = inspect.getsource(_load_migration())
        assert "IF EXISTS" in full_src
