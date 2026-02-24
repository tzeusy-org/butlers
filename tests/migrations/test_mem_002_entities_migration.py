"""Tests for mem_002 entities migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE_002 = MIGRATION_DIR / "002_entities.py"


def _load_migration_002():
    """Load mem_002 migration module dynamically."""
    filepath = MIGRATION_FILE_002
    spec = importlib.util.spec_from_file_location("mem_002_entities_migration", filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMem002FileStructure:
    def test_migration_file_exists(self) -> None:
        """The mem_002 migration file exists on disk."""
        assert MIGRATION_FILE_002.exists(), f"Migration file not found at {MIGRATION_FILE_002}"

    def test_revision_identifiers(self) -> None:
        """mem_002 has correct revision metadata chaining from mem_001."""
        mod = _load_migration_002()
        assert mod.revision == "mem_002"
        assert mod.down_revision == "mem_001"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """Migration declares upgrade()/downgrade() callables."""
        mod = _load_migration_002()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMem002UpgradeEntitiesTable:
    def test_creates_entities_table(self) -> None:
        """Upgrade SQL creates the entities table."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS entities" in source

    def test_entities_table_has_uuid_pk(self) -> None:
        """Entities table has UUID primary key with gen_random_uuid()."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in source

    def test_entities_table_has_tenant_id(self) -> None:
        """Entities table includes tenant_id column."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "tenant_id" in source

    def test_entities_table_has_canonical_name(self) -> None:
        """Entities table includes canonical_name VARCHAR column."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "canonical_name VARCHAR" in source

    def test_entities_table_has_entity_type_with_constraint(self) -> None:
        """Entities table has entity_type column with CHECK constraint."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "entity_type VARCHAR" in source
        assert "chk_entities_entity_type" in source
        for valid_type in ("person", "organization", "place", "other"):
            assert valid_type in source

    def test_entities_table_has_unique_tenant_canonical_type(self) -> None:
        """Entities table has unique constraint on (tenant_id, canonical_name, entity_type)."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "uq_entities_tenant_canonical_type" in source
        assert "UNIQUE (tenant_id, canonical_name, entity_type)" in source

    def test_entities_table_has_aliases_array(self) -> None:
        """Entities table has aliases TEXT[] column."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "aliases TEXT[]" in source

    def test_entities_table_has_metadata_jsonb(self) -> None:
        """Entities table has metadata JSONB column."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "metadata JSONB" in source

    def test_entities_table_has_timestamps(self) -> None:
        """Entities table has created_at and updated_at TIMESTAMPTZ columns."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "created_at TIMESTAMPTZ" in source
        assert "updated_at TIMESTAMPTZ" in source


class TestMem002UpgradeIdempotentColumns:
    """Verify that upgrade() adds required columns idempotently via ALTER TABLE.

    A pre-existing entities table (e.g. from a partial previous migration run)
    causes CREATE TABLE IF NOT EXISTS to silently skip.  The migration must
    then ensure every required column exists using ADD COLUMN IF NOT EXISTS so
    that the subsequent index creations do not fail with 'column does not exist'.
    """

    def test_alter_table_adds_tenant_id_if_not_exists(self) -> None:
        """Upgrade adds tenant_id idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS tenant_id" in source

    def test_alter_table_adds_canonical_name_if_not_exists(self) -> None:
        """Upgrade adds canonical_name idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS canonical_name" in source

    def test_alter_table_adds_entity_type_if_not_exists(self) -> None:
        """Upgrade adds entity_type idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS entity_type" in source

    def test_alter_table_adds_aliases_if_not_exists(self) -> None:
        """Upgrade adds aliases idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS aliases" in source

    def test_alter_table_adds_metadata_if_not_exists(self) -> None:
        """Upgrade adds metadata idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS metadata" in source

    def test_alter_table_adds_created_at_if_not_exists(self) -> None:
        """Upgrade adds created_at idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS created_at" in source

    def test_alter_table_adds_updated_at_if_not_exists(self) -> None:
        """Upgrade adds updated_at idempotently via ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ADD COLUMN IF NOT EXISTS updated_at" in source

    def test_alter_table_statements_precede_index_creation(self) -> None:
        """ALTER TABLE idempotent column additions appear before the index creation statements.

        This ordering is critical: indexes on tenant_id, canonical_name, aliases, and metadata
        must only be created after those columns are guaranteed to exist.
        """
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        tenant_id_alter_pos = source.index("ADD COLUMN IF NOT EXISTS tenant_id")
        first_index_pos = source.index("CREATE INDEX IF NOT EXISTS idx_entities_tenant_canonical")
        assert tenant_id_alter_pos < first_index_pos, (
            "ALTER TABLE tenant_id must appear before index creation"
        )


class TestMem002UpgradeIndexes:
    def test_gin_index_on_aliases(self) -> None:
        """Upgrade creates a GIN index on entities.aliases."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_entities_aliases" in source
        assert "USING gin(aliases)" in source

    def test_gin_index_on_metadata(self) -> None:
        """Upgrade creates a GIN index on entities.metadata."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_entities_metadata" in source
        assert "USING gin(metadata)" in source

    def test_tenant_canonical_index(self) -> None:
        """Upgrade creates a composite index on (tenant_id, canonical_name)."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_entities_tenant_canonical" in source
        assert "tenant_id, canonical_name" in source


class TestMem002UpgradeFactsFK:
    def test_entity_id_fk_added_to_facts(self) -> None:
        """Upgrade adds entity_id nullable FK column to facts table."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ALTER TABLE facts" in source
        assert "ADD COLUMN IF NOT EXISTS entity_id UUID" in source
        assert "REFERENCES entities(id)" in source

    def test_entity_id_fk_on_delete_restrict(self) -> None:
        """entity_id FK uses ON DELETE RESTRICT to protect entity records."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "ON DELETE RESTRICT" in source

    def test_partial_unique_index_with_entity_id_includes_scope(self) -> None:
        """Unique partial index on (entity_id, scope, predicate) WHERE entity_id IS NOT NULL."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_entity_scope_predicate_active" in source
        assert "entity_id, scope, predicate" in source
        assert "entity_id IS NOT NULL" in source
        assert "validity = 'active'" in source

    def test_partial_unique_index_without_entity_id_includes_scope(self) -> None:
        """Unique partial index on (scope, subject, predicate) WHERE entity_id IS NULL."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_no_entity_subject_predicate_active" in source
        assert "scope, subject, predicate" in source
        assert "entity_id IS NULL" in source


class TestMem002Downgrade:
    def test_downgrade_drops_entity_id_indexes(self) -> None:
        """Downgrade removes both partial unique indexes on facts."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active" in source
        assert "DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active" in source

    def test_downgrade_drops_entity_id_column(self) -> None:
        """Downgrade removes entity_id column from facts table."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "DROP COLUMN IF EXISTS entity_id" in source

    def test_downgrade_drops_entities_table(self) -> None:
        """Downgrade removes entities table with CASCADE."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS entities CASCADE" in source

    def test_downgrade_drops_in_correct_order(self) -> None:
        """Downgrade drops indexes, then FK column, then entities table (dependency order)."""
        mod = _load_migration_002()
        source = inspect.getsource(mod.downgrade)
        idx1_pos = source.index("idx_facts_entity_scope_predicate_active")
        idx2_pos = source.index("idx_facts_no_entity_subject_predicate_active")
        col_pos = source.index("DROP COLUMN IF EXISTS entity_id")
        table_pos = source.index("DROP TABLE IF EXISTS entities CASCADE")
        # Indexes dropped first, then column, then entities table
        assert idx1_pos < col_pos
        assert idx2_pos < col_pos
        assert col_pos < table_pos
