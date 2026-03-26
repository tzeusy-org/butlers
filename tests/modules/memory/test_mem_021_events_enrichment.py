"""Tests for consolidated memory_events enrichment + embedding_versions in mem_001."""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

MIGRATION_DIR = MEMORY_MODULE_PATH / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_memory_schema.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_001_memory_schema", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMem001FileAndRevision:
    def test_migration_file_exists(self) -> None:
        assert MIGRATION_FILE.exists()

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "mem_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("memory",)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMemoryEventsColumns:
    def test_memory_events_enrichment_columns_in_upgrade(self) -> None:
        source = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS memory_events" in source
        assert "request_id TEXT" in source
        assert "memory_type TEXT" in source
        assert "memory_id UUID" in source
        assert "actor_butler TEXT" in source
        assert "idx_memory_events_actor_butler_type" in source
        assert "actor_butler IS NOT NULL" in source

    def test_downgrade_drops_memory_events(self) -> None:
        source = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS memory_events CASCADE" in source


class TestEmbeddingVersions:
    def test_upgrade_creates_embedding_versions_table_and_seed(self) -> None:
        source = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS embedding_versions" in source
        assert "uq_embedding_versions_model" in source
        assert "all-MiniLM-L6-v2" in source
        assert "ON CONFLICT (model_name) DO NOTHING" in source

    def test_downgrade_drops_embedding_versions(self) -> None:
        source = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS embedding_versions CASCADE" in source
