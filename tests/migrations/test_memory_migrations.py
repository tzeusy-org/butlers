"""Tests for Memory Butler migration files."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_create_episodes.py"


def _load_migration(filename: str = "001_create_episodes.py", module_name: str = "migration_001"):
    """Load a migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 001_create_episodes ──────────────────────────────────────────────


def test_migration_file_exists():
    """The 001_create_episodes migration file exists on disk."""
    assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"


def test_init_file_exists():
    """The __init__.py file exists in the migrations directory."""
    init_file = MIGRATION_DIR / "__init__.py"
    assert init_file.exists(), f"__init__.py not found at {init_file}"


def test_branch_labels():
    """The migration declares the 'memory' branch label."""
    mod = _load_migration()
    assert hasattr(mod, "branch_labels")
    assert mod.branch_labels == ("memory",)


def test_revision_identifiers():
    """The migration has correct revision identifiers."""
    mod = _load_migration()
    assert mod.revision == "mem_001"
    assert mod.down_revision is None
    assert mod.depends_on is None


def test_upgrade_function_exists():
    """The migration has an upgrade() function."""
    mod = _load_migration()
    assert hasattr(mod, "upgrade")
    assert callable(mod.upgrade)


def test_downgrade_function_exists():
    """The migration has a downgrade() function."""
    mod = _load_migration()
    assert hasattr(mod, "downgrade")
    assert callable(mod.downgrade)


# ── 003_create_rules ─────────────────────────────────────────────────


class TestRulesMigration:
    """Tests for the 003_create_rules migration."""

    @staticmethod
    def _load():
        return _load_migration("003_create_rules.py", "migration_003")

    def test_migration_file_exists(self):
        """The 003_create_rules migration file exists on disk."""
        path = MIGRATION_DIR / "003_create_rules.py"
        assert path.exists(), f"Migration file not found at {path}"

    def test_revision_identifiers(self):
        """The migration has correct revision identifiers."""
        mod = self._load()
        assert mod.revision == "mem_003"
        assert mod.down_revision == "mem_002"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_function_exists(self):
        """The migration has an upgrade() function."""
        mod = self._load()
        assert hasattr(mod, "upgrade")
        assert callable(mod.upgrade)

    def test_downgrade_function_exists(self):
        """The migration has a downgrade() function."""
        mod = self._load()
        assert hasattr(mod, "downgrade")
        assert callable(mod.downgrade)

    def test_upgrade_creates_rules_table(self):
        """The upgrade SQL contains CREATE TABLE rules."""
        mod = self._load()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "rules" in source

    def test_upgrade_has_required_columns(self):
        """The upgrade SQL declares all required columns."""
        mod = self._load()
        source = inspect.getsource(mod.upgrade)
        required_columns = [
            "id UUID",
            "content TEXT",
            "embedding vector(384)",
            "search_vector tsvector",
            "scope TEXT",
            "maturity TEXT",
            "confidence FLOAT",
            "decay_rate FLOAT",
            "permanence TEXT",
            "effectiveness_score FLOAT",
            "applied_count INTEGER",
            "success_count INTEGER",
            "harmful_count INTEGER",
            "source_episode_id UUID",
            "source_butler TEXT",
            "created_at TIMESTAMPTZ",
            "last_applied_at TIMESTAMPTZ",
            "last_evaluated_at TIMESTAMPTZ",
            "tags JSONB",
            "metadata JSONB",
        ]
        for col in required_columns:
            assert col in source, f"Missing column: {col}"

    def test_upgrade_has_scope_maturity_index(self):
        """The upgrade SQL creates a composite index on scope + maturity."""
        mod = self._load()
        source = inspect.getsource(mod.upgrade)
        assert "idx_rules_scope_maturity" in source
        assert "(scope, maturity)" in source

    def test_upgrade_has_gin_search_index(self):
        """The upgrade SQL creates a GIN index on search_vector."""
        mod = self._load()
        source = inspect.getsource(mod.upgrade)
        assert "idx_rules_search" in source
        assert "USING gin(search_vector)" in source

    def test_upgrade_has_episode_fk(self):
        """The upgrade SQL references episodes table for source_episode_id."""
        mod = self._load()
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES episodes(id)" in source

    def test_downgrade_drops_rules(self):
        """The downgrade SQL drops the rules table."""
        mod = self._load()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "rules" in source


# ── 004_create_memory_links ──────────────────────────────────────────────

MEMORY_LINKS_FILE = MIGRATION_DIR / "004_create_memory_links.py"


def _load_memory_links():
    return _load_migration("004_create_memory_links.py", "migration_004")


def test_004_file_exists():
    """The 004_create_memory_links migration file exists on disk."""
    assert MEMORY_LINKS_FILE.exists(), f"Migration file not found at {MEMORY_LINKS_FILE}"


def test_004_revision_identifiers():
    """Migration 004 has correct revision chain."""
    mod = _load_memory_links()
    assert mod.revision == "mem_004"
    assert mod.down_revision == "mem_003"
    assert mod.depends_on is None


def test_004_upgrade_function_exists():
    """Migration 004 has an upgrade() function."""
    mod = _load_memory_links()
    assert hasattr(mod, "upgrade")
    assert callable(mod.upgrade)


def test_004_downgrade_function_exists():
    """Migration 004 has a downgrade() function."""
    mod = _load_memory_links()
    assert hasattr(mod, "downgrade")
    assert callable(mod.downgrade)


def test_004_upgrade_creates_memory_links_table():
    """The upgrade SQL contains the memory_links table definition."""
    mod = _load_memory_links()
    source = inspect.getsource(mod.upgrade)
    assert "memory_links" in source
    assert "source_type" in source
    assert "source_id" in source
    assert "target_type" in source
    assert "target_id" in source
    assert "relation" in source
    assert "created_at" in source
    assert "PRIMARY KEY" in source


def test_004_upgrade_has_check_constraint():
    """The upgrade SQL contains a CHECK constraint for valid relation values."""
    mod = _load_memory_links()
    source = inspect.getsource(mod.upgrade)
    assert "CHECK" in source
    for relation in ("derived_from", "supports", "contradicts", "supersedes", "related_to"):
        assert relation in source, f"Missing relation value: {relation}"


def test_004_upgrade_has_target_index():
    """The upgrade SQL creates an index on (target_type, target_id)."""
    mod = _load_memory_links()
    source = inspect.getsource(mod.upgrade)
    assert "idx_memory_links_target" in source
    assert "target_type" in source
    assert "target_id" in source


def test_004_downgrade_drops_table():
    """The downgrade SQL drops the memory_links table."""
    mod = _load_memory_links()
    source = inspect.getsource(mod.downgrade)
    assert "DROP TABLE" in source
    assert "memory_links" in source
