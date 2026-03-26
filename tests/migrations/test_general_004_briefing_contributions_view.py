"""Tests for current general migration chain (gen_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "general" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_general_tables.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("gen_001_general_tables", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "gen_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("general",)
        assert mod.depends_on is None


class TestUpgradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_collections_and_entities(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS collections" in src
        assert "CREATE TABLE IF NOT EXISTS entities" in src

    def test_entities_fk_and_indexes(self) -> None:
        src = self._src()
        assert "REFERENCES collections(id) ON DELETE CASCADE" in src
        assert "idx_entities_data_gin" in src
        assert "idx_entities_collection_id" in src


class TestDowngradeSQL:
    def test_drops_entities_then_collections(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS entities" in src
        assert "DROP TABLE IF EXISTS collections" in src
