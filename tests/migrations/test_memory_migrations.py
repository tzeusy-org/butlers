"""Tests for the memory module baseline migration file."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_memory_baseline.py"


def _load_migration(
    filename: str = "001_memory_baseline.py",
    module_name: str = "memory_baseline_migration",
):
    """Load a migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists() -> None:
    """The baseline migration file exists on disk."""
    assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"


def test_init_file_exists() -> None:
    """The __init__.py file exists in the migrations directory."""
    init_file = MIGRATION_DIR / "__init__.py"
    assert init_file.exists(), f"__init__.py not found at {init_file}"


def test_revision_identifiers() -> None:
    """The baseline migration has correct revision metadata."""
    mod = _load_migration()
    assert mod.revision == "mem_001"
    assert mod.down_revision is None
    assert mod.branch_labels == ("memory",)
    assert mod.depends_on is None


def test_upgrade_and_downgrade_exist() -> None:
    """The migration declares upgrade()/downgrade() callables."""
    mod = _load_migration()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_upgrade_creates_all_memory_tables() -> None:
    """Upgrade SQL creates the full target-state table set."""
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade)
    for table in ("episodes", "facts", "rules", "memory_links"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in source


def test_upgrade_contains_consolidation_columns_and_index() -> None:
    """Episodes table has consolidation metadata from the final target state."""
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade)
    assert "consolidation_status VARCHAR(20)" in source
    assert "retry_count INTEGER" in source
    assert "last_error TEXT" in source
    assert "idx_episodes_unconsolidated" in source
    assert "WHERE consolidation_status = 'pending'" in source


def test_upgrade_contains_runtime_indexes() -> None:
    """Upgrade SQL creates all required query/vector indexes."""
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade)
    expected_indexes = (
        "idx_episodes_butler_created",
        "idx_episodes_expires",
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
    )
    for index_name in expected_indexes:
        assert index_name in source, f"Missing index: {index_name}"


def test_upgrade_contains_memory_links_constraint() -> None:
    """Upgrade SQL enforces valid relation values for memory links."""
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade)
    assert "chk_memory_links_relation" in source
    for relation in ("derived_from", "supports", "contradicts", "supersedes", "related_to"):
        assert relation in source


def test_upgrade_enables_required_extensions() -> None:
    """Upgrade SQL enables vector + uuid-ossp extensions."""
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in source
    assert "uuid-ossp" in source


def test_downgrade_drops_memory_tables() -> None:
    """Downgrade removes memory tables in reverse dependency order."""
    mod = _load_migration()
    source = inspect.getsource(mod.downgrade)
    assert "DROP TABLE IF EXISTS memory_links" in source
    assert "DROP TABLE IF EXISTS rules" in source
    assert "DROP TABLE IF EXISTS facts" in source
    assert "DROP TABLE IF EXISTS episodes" in source
