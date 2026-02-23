"""Unit tests for rel_008 - entity_id FK migration on the contacts table."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "rel_008_entity_id_fk.py"


def _relationship_migration_dir() -> Path:
    """Return the relationship migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("relationship")
    assert chain_dir is not None, "Relationship chain should exist"
    return chain_dir


def _load_migration():
    """Load the rel_008 migration module."""
    migration_path = _relationship_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Missing migration file: {MIGRATION_FILENAME}"
    spec = importlib.util.spec_from_file_location("rel_008_entity_id_fk", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration has the correct revision ID."""
        mod = _load_migration()
        assert mod.revision == "rel_008"

    def test_down_revision(self) -> None:
        """Migration chains from rel_007."""
        mod = _load_migration()
        assert mod.down_revision == "rel_007"

    def test_branch_labels_none(self) -> None:
        """Non-root migrations should have branch_labels=None."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on should be None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """Migration declares upgrade() and downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_adds_entity_id_column(self) -> None:
        """Upgrade adds entity_id column to contacts table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "entity_id" in source
        assert "contacts" in source
        assert "UUID" in source

    def test_entity_id_nullable(self) -> None:
        """entity_id column is added without NOT NULL (nullable)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Should NOT force NOT NULL at column creation
        assert "NOT NULL" not in source.split("entity_id")[1].split("REFERENCES")[0]

    def test_cross_schema_fk_references_general_entities(self) -> None:
        """FK constraint references general.entities with explicit schema qualification."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "general.entities" in source

    def test_fk_on_delete_set_null(self) -> None:
        """FK uses ON DELETE SET NULL so deleting an entity nullifies contacts."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ON DELETE SET NULL" in source

    def test_fk_constraint_name(self) -> None:
        """FK constraint has an explicit, predictable name."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "contacts_entity_id_fkey" in source

    def test_index_on_entity_id(self) -> None:
        """Upgrade creates an index on entity_id."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ix_contacts_entity_id" in source
        assert "CREATE INDEX" in source

    def test_index_partial_not_null(self) -> None:
        """Index is partial: only covers non-NULL entity_id values."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "WHERE entity_id IS NOT NULL" in source


class TestDowngradeSQL:
    def test_drops_index(self) -> None:
        """Downgrade removes the entity_id index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS ix_contacts_entity_id" in source

    def test_drops_fk_constraint(self) -> None:
        """Downgrade removes the FK constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP CONSTRAINT IF EXISTS contacts_entity_id_fkey" in source

    def test_drops_entity_id_column(self) -> None:
        """Downgrade removes the entity_id column."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP COLUMN IF EXISTS entity_id" in source

    def test_downgrade_order_index_before_constraint_before_column(self) -> None:
        """Downgrade drops index before constraint before column (correct dependency order)."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        idx_pos = source.find("ix_contacts_entity_id")
        fk_pos = source.find("contacts_entity_id_fkey")
        col_pos = source.find("DROP COLUMN")
        assert idx_pos < fk_pos < col_pos, (
            "Downgrade should drop index, then FK constraint, then column"
        )
