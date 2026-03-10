"""Tests for mem_021: memory_events enrichment and embedding_versions migration."""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

MIGRATION_DIR = MEMORY_MODULE_PATH / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "021_events_enrichment.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_021_events_enrichment", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMem021FileAndRevision:
    """Verify migration file existence and revision metadata."""

    def test_migration_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "mem_021"
        assert mod.down_revision == "mem_020"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMem021MemoryEventsColumns:
    """Verify memory_events enrichment columns are added in upgrade."""

    def test_upgrade_adds_request_id_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "request_id" in source

    def test_upgrade_adds_memory_type_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "memory_type" in source

    def test_upgrade_adds_memory_id_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "memory_id" in source

    def test_upgrade_adds_actor_butler_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "actor_butler" in source

    def test_upgrade_targets_memory_events_table(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "memory_events" in source

    def test_upgrade_creates_actor_butler_index(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_memory_events_actor_butler_type" in source
        assert "actor_butler IS NOT NULL" in source

    def test_downgrade_drops_all_enrichment_columns(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "request_id" in source
        assert "memory_type" in source
        assert "memory_id" in source
        assert "actor_butler" in source

    def test_downgrade_drops_actor_butler_index(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_memory_events_actor_butler_type" in source


class TestMem021EmbeddingVersions:
    """Verify embedding_versions table creation and seed data."""

    def test_upgrade_creates_embedding_versions_table(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS embedding_versions" in source

    def test_embedding_versions_has_model_name_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "model_name" in source

    def test_embedding_versions_has_dimension_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "dimension" in source

    def test_embedding_versions_has_active_column(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "active" in source

    def test_embedding_versions_has_unique_constraint_on_model_name(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_embedding_versions_model" in source

    def test_upgrade_seeds_all_minilm_l6_v2(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "all-MiniLM-L6-v2" in source

    def test_seed_row_has_dimension_384(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "384" in source

    def test_seed_uses_on_conflict_do_nothing(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ON CONFLICT" in source
        assert "DO NOTHING" in source

    def test_downgrade_drops_embedding_versions(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS embedding_versions" in source


class TestMem021MigrationChainContains021:
    """Verify migration chain includes mem_021."""

    def test_migration_chain_includes_021(self) -> None:
        migration_files = sorted(
            p.name for p in MIGRATION_DIR.iterdir() if p.suffix == ".py" and p.name != "__init__.py"
        )
        assert "021_events_enrichment.py" in migration_files
