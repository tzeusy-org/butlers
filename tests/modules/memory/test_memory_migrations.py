"""Unit tests for the memory module baseline migration file."""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = MEMORY_MODULE_PATH / "migrations"
BASELINE_FILE = MIGRATIONS_DIR / "001_memory_baseline.py"


def _load_migration(filename: str = "001_memory_baseline.py"):
    """Load a migration module by filename."""
    filepath = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), filepath)
    assert spec is not None, f"Could not load spec for {filepath}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMemoryBaselineMigration:
    """Validate memory chain is represented by one target-state baseline revision."""

    def test_baseline_file_exists(self) -> None:
        assert BASELINE_FILE.exists(), f"Expected migration at {BASELINE_FILE}"

    def test_only_one_migration_file_exists(self) -> None:
        migration_files = sorted(
            p.name
            for p in MIGRATIONS_DIR.iterdir()
            if p.suffix == ".py" and p.name != "__init__.py"
        )
        assert migration_files == ["001_memory_baseline.py"]

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "mem_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("memory",)
        assert mod.depends_on is None

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration()
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))

    def test_upgrade_creates_required_tables(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for table in ("episodes", "facts", "rules", "memory_links"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in source

    def test_upgrade_episodes_has_target_state_columns(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        required = (
            "consolidated BOOLEAN",
            "consolidation_status VARCHAR(20)",
            "retry_count INTEGER",
            "last_error TEXT",
            "reference_count INTEGER",
            "last_referenced_at TIMESTAMPTZ",
        )
        for snippet in required:
            assert snippet in source, f"Missing episodes column snippet: {snippet}"

    def test_upgrade_facts_has_required_columns(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        required = (
            "subject TEXT NOT NULL",
            "predicate TEXT NOT NULL",
            "supersedes_id UUID REFERENCES facts(id)",
            "validity TEXT NOT NULL DEFAULT 'active'",
            "scope TEXT NOT NULL DEFAULT 'global'",
            "last_confirmed_at TIMESTAMPTZ",
            "tags JSONB",
        )
        for snippet in required:
            assert snippet in source, f"Missing facts column snippet: {snippet}"

    def test_upgrade_rules_has_required_columns(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        required = (
            "maturity TEXT NOT NULL DEFAULT 'candidate'",
            "effectiveness_score FLOAT NOT NULL DEFAULT 0.0",
            "applied_count INTEGER NOT NULL DEFAULT 0",
            "success_count INTEGER NOT NULL DEFAULT 0",
            "harmful_count INTEGER NOT NULL DEFAULT 0",
            "last_confirmed_at TIMESTAMPTZ",
            "reference_count INTEGER NOT NULL DEFAULT 0",
            "last_referenced_at TIMESTAMPTZ",
        )
        for snippet in required:
            assert snippet in source, f"Missing rules column snippet: {snippet}"

    def test_upgrade_memory_links_constraint(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "chk_memory_links_relation" in source
        for relation in ("derived_from", "supports", "contradicts", "supersedes", "related_to"):
            assert relation in source

    def test_upgrade_creates_runtime_indexes(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for index_name in (
            "idx_episodes_butler_created",
            "idx_episodes_expires",
            "idx_episodes_unconsolidated",
            "idx_episodes_search",
            "idx_episodes_embedding",
            "idx_facts_scope_validity",
            "idx_facts_subject_predicate",
            "idx_facts_search",
            "idx_facts_tags",
            "idx_facts_embedding",
            "idx_rules_scope_maturity",
            "idx_rules_search",
            "idx_rules_embedding",
            "idx_memory_links_target",
        ):
            assert index_name in source, f"Missing index: {index_name}"

    def test_upgrade_enables_extensions(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE EXTENSION IF NOT EXISTS vector" in source
        assert 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' in source

    def test_downgrade_drops_tables(self) -> None:
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS memory_links" in source
        assert "DROP TABLE IF EXISTS rules" in source
        assert "DROP TABLE IF EXISTS facts" in source
        assert "DROP TABLE IF EXISTS episodes" in source
