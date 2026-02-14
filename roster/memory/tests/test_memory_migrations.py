"""Unit tests for Memory Butler migration files."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src"
    / "butlers"
    / "modules"
    / "memory"
    / "migrations"
)


def _load_migration(filename: str):
    """Load a migration module by filename."""
    filepath = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), filepath)
    assert spec is not None, f"Could not load spec for {filepath}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 001_create_episodes ──────────────────────────────────────────────────


class TestEpisodesMigration:
    """Tests for 001_create_episodes migration."""

    def test_migration_file_exists(self) -> None:
        filepath = MIGRATIONS_DIR / "001_create_episodes.py"
        assert filepath.exists(), f"Expected migration at {filepath}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        assert mod.revision == "mem_001"
        assert mod.down_revision is None

    def test_branch_labels(self) -> None:
        """001 is the branch root, so it should declare the 'memory' branch."""
        mod = _load_migration("001_create_episodes.py")
        assert mod.branch_labels == ("memory",)

    def test_depends_on(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        assert mod.depends_on is None

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))

    def test_upgrade_enables_vector_extension(self) -> None:
        """Upgrade should enable pgvector before creating the table."""
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE EXTENSION IF NOT EXISTS vector" in source

    def test_upgrade_creates_episodes_table(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "episodes" in source

    def test_upgrade_has_required_columns(self) -> None:
        """Episodes table must have all required columns."""
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        required = [
            "id UUID",
            "butler TEXT",
            "session_id UUID",
            "content TEXT",
            "embedding vector(384)",
            "search_vector tsvector",
            "importance FLOAT",
            "reference_count INTEGER",
            "consolidated BOOLEAN",
            "created_at TIMESTAMPTZ",
            "last_referenced_at TIMESTAMPTZ",
            "expires_at TIMESTAMPTZ",
            "metadata JSONB",
        ]
        for col in required:
            assert col in source, f"Missing column: {col}"

    def test_upgrade_has_butler_created_index(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_episodes_butler_created" in source
        assert "(butler, created_at DESC)" in source

    def test_upgrade_has_expires_partial_index(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_episodes_expires" in source
        assert "WHERE expires_at IS NOT NULL" in source

    def test_upgrade_has_unconsolidated_partial_index(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_episodes_unconsolidated" in source
        assert "WHERE NOT consolidated" in source

    def test_upgrade_has_gin_search_index(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_episodes_search" in source
        assert "USING gin(search_vector)" in source

    def test_upgrade_has_default_expiry_7_days(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.upgrade)
        assert "interval '7 days'" in source

    def test_downgrade_drops_episodes(self) -> None:
        mod = _load_migration("001_create_episodes.py")
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "episodes" in source


# ── 002_create_facts ─────────────────────────────────────────────────────


class TestFactsMigration:
    """Tests for 002_create_facts migration."""

    def test_migration_file_exists(self) -> None:
        """The migration file should exist on disk."""
        filepath = MIGRATIONS_DIR / "002_create_facts.py"
        assert filepath.exists(), f"Expected migration at {filepath}"

    def test_revision_identifiers(self) -> None:
        """The migration should have correct Alembic revision metadata."""
        mod = _load_migration("002_create_facts.py")
        assert mod.revision == "mem_002"
        assert mod.down_revision == "mem_001"

    def test_branch_labels(self) -> None:
        """002 is not the branch root, so branch_labels should be None."""
        mod = _load_migration("002_create_facts.py")
        assert mod.branch_labels is None

    def test_depends_on(self) -> None:
        """depends_on should be None (chaining is via down_revision)."""
        mod = _load_migration("002_create_facts.py")
        assert mod.depends_on is None

    def test_has_upgrade_function(self) -> None:
        """The migration must define an upgrade() callable."""
        mod = _load_migration("002_create_facts.py")
        assert callable(getattr(mod, "upgrade", None))

    def test_has_downgrade_function(self) -> None:
        """The migration must define a downgrade() callable."""
        mod = _load_migration("002_create_facts.py")
        assert callable(getattr(mod, "downgrade", None))

    def test_init_file_exists(self) -> None:
        """The migrations package should have an __init__.py."""
        init_path = MIGRATIONS_DIR / "__init__.py"
        assert init_path.exists(), f"Expected __init__.py at {init_path}"

    def test_upgrade_creates_facts_table(self) -> None:
        """Upgrade should create the facts table."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "facts" in source

    def test_upgrade_has_required_columns(self) -> None:
        """Facts table must have all required columns with correct types."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        required = [
            "id UUID",
            "subject TEXT",
            "predicate TEXT",
            "content TEXT",
            "embedding vector(384)",
            "search_vector tsvector",
            "importance FLOAT",
            "confidence FLOAT",
            "decay_rate FLOAT",
            "permanence TEXT",
            "source_butler TEXT",
            "source_episode_id UUID",
            "supersedes_id UUID",
            "validity TEXT",
            "scope TEXT",
            "reference_count INTEGER",
            "created_at TIMESTAMPTZ",
            "last_referenced_at TIMESTAMPTZ",
            "last_confirmed_at TIMESTAMPTZ",
            "tags JSONB",
            "metadata JSONB",
        ]
        for col in required:
            assert col in source, f"Missing column: {col}"

    def test_upgrade_has_episode_fk(self) -> None:
        """source_episode_id should reference episodes(id)."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES episodes(id)" in source

    def test_upgrade_has_self_referential_fk(self) -> None:
        """supersedes_id should reference facts(id) for version chains."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES facts(id)" in source

    def test_upgrade_has_scope_validity_index(self) -> None:
        """Partial index on scope+validity for active facts."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_scope_validity" in source
        assert "WHERE validity = 'active'" in source

    def test_upgrade_has_subject_predicate_index(self) -> None:
        """Composite index on subject+predicate for lookups."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_subject_predicate" in source
        assert "(subject, predicate)" in source

    def test_upgrade_has_gin_search_index(self) -> None:
        """GIN index on search_vector for full-text search."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_search" in source
        assert "USING gin(search_vector)" in source

    def test_upgrade_has_gin_tags_index(self) -> None:
        """GIN index on tags for JSONB containment queries."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_tags" in source
        assert "USING gin(tags)" in source

    def test_downgrade_drops_facts(self) -> None:
        """Downgrade should drop the facts table."""
        mod = _load_migration("002_create_facts.py")
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "facts" in source


class TestRulesMigration:
    """Tests for 003_create_rules migration."""

    def test_migration_file_exists(self) -> None:
        """The migration file should exist on disk."""
        filepath = MIGRATIONS_DIR / "003_create_rules.py"
        assert filepath.exists(), f"Expected migration at {filepath}"

    def test_revision_identifiers(self) -> None:
        """The migration should have correct Alembic revision metadata."""
        mod = _load_migration("003_create_rules.py")
        assert mod.revision == "mem_003"
        assert mod.down_revision == "mem_002"

    def test_branch_labels(self) -> None:
        """003 is not the branch root, so branch_labels should be None."""
        mod = _load_migration("003_create_rules.py")
        assert mod.branch_labels is None

    def test_depends_on(self) -> None:
        """depends_on should be None (chaining is via down_revision)."""
        mod = _load_migration("003_create_rules.py")
        assert mod.depends_on is None

    def test_has_upgrade_function(self) -> None:
        """The migration must define an upgrade() callable."""
        mod = _load_migration("003_create_rules.py")
        assert callable(getattr(mod, "upgrade", None))

    def test_has_downgrade_function(self) -> None:
        """The migration must define a downgrade() callable."""
        mod = _load_migration("003_create_rules.py")
        assert callable(getattr(mod, "downgrade", None))

    def test_upgrade_creates_rules_table(self) -> None:
        mod = _load_migration("003_create_rules.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "rules" in source

    def test_upgrade_has_scope_maturity_index(self) -> None:
        mod = _load_migration("003_create_rules.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_rules_scope_maturity" in source
        assert "(scope, maturity)" in source

    def test_upgrade_has_gin_search_index(self) -> None:
        mod = _load_migration("003_create_rules.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_rules_search" in source
        assert "USING gin(search_vector)" in source

    def test_upgrade_has_episode_fk(self) -> None:
        mod = _load_migration("003_create_rules.py")
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES episodes(id)" in source

    def test_downgrade_drops_rules(self) -> None:
        mod = _load_migration("003_create_rules.py")
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "rules" in source


# ── 004_create_memory_links ──────────────────────────────────────────────


class TestMemoryLinksMigration:
    """Tests for 004_create_memory_links migration."""

    def test_migration_file_exists(self) -> None:
        filepath = MIGRATIONS_DIR / "004_create_memory_links.py"
        assert filepath.exists(), f"Expected migration at {filepath}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        assert mod.revision == "mem_004"
        assert mod.down_revision == "mem_003"

    def test_branch_labels(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        assert mod.branch_labels is None

    def test_depends_on(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        assert mod.depends_on is None

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))

    def test_upgrade_creates_memory_links_table(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE" in source
        assert "memory_links" in source

    def test_upgrade_has_required_columns(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.upgrade)
        for col in (
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation",
            "created_at",
        ):
            assert col in source, f"Missing column: {col}"

    def test_upgrade_has_composite_primary_key(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.upgrade)
        assert "PRIMARY KEY (source_type, source_id, target_type, target_id)" in source

    def test_upgrade_has_check_constraint_for_relations(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.upgrade)
        assert "CHECK" in source
        for relation in ("derived_from", "supports", "contradicts", "supersedes", "related_to"):
            assert relation in source, f"Missing relation: {relation}"

    def test_upgrade_has_target_index(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_memory_links_target" in source

    def test_downgrade_drops_memory_links(self) -> None:
        mod = _load_migration("004_create_memory_links.py")
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE" in source
        assert "memory_links" in source


class TestVectorIndexesMigration:
    """Tests for 005_add_vector_indexes migration."""

    def test_migration_file_exists(self) -> None:
        """The migration file should exist on disk."""
        filepath = MIGRATIONS_DIR / "005_add_vector_indexes.py"
        assert filepath.exists(), f"Expected migration at {filepath}"

    def test_revision_identifiers(self) -> None:
        """The migration should have correct Alembic revision metadata."""
        mod = _load_migration("005_add_vector_indexes.py")
        assert mod.revision == "mem_005"
        assert mod.down_revision == "mem_004"

    def test_branch_labels(self) -> None:
        """005 is not the branch root, so branch_labels should be None."""
        mod = _load_migration("005_add_vector_indexes.py")
        assert mod.branch_labels is None

    def test_depends_on(self) -> None:
        """depends_on should be None (chaining is via down_revision)."""
        mod = _load_migration("005_add_vector_indexes.py")
        assert mod.depends_on is None

    def test_has_upgrade_function(self) -> None:
        """The migration must define an upgrade() callable."""
        mod = _load_migration("005_add_vector_indexes.py")
        assert callable(getattr(mod, "upgrade", None))

    def test_has_downgrade_function(self) -> None:
        """The migration must define a downgrade() callable."""
        mod = _load_migration("005_add_vector_indexes.py")
        assert callable(getattr(mod, "downgrade", None))

    def test_upgrade_creates_vector_extension(self) -> None:
        """Upgrade should enable the vector extension."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert "CREATE EXTENSION IF NOT EXISTS vector" in source

    def test_upgrade_creates_uuid_ossp_extension(self) -> None:
        """Upgrade should enable the uuid-ossp extension."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert "uuid-ossp" in source

    def test_upgrade_creates_episodes_ivfflat_index(self) -> None:
        """Upgrade should create IVFFlat index on episodes.embedding."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_episodes_embedding" in source
        assert "ivfflat" in source
        assert "vector_cosine_ops" in source

    def test_upgrade_creates_facts_ivfflat_index(self) -> None:
        """Upgrade should create IVFFlat index on facts.embedding."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_embedding" in source

    def test_upgrade_creates_rules_ivfflat_index(self) -> None:
        """Upgrade should create IVFFlat index on rules.embedding."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert "idx_rules_embedding" in source

    def test_upgrade_uses_20_lists(self) -> None:
        """All IVFFlat indexes should use 20 lists."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.upgrade)
        assert source.count("lists = 20") == 3

    def test_downgrade_drops_indexes(self) -> None:
        """Downgrade should drop all three IVFFlat indexes."""
        mod = _load_migration("005_add_vector_indexes.py")
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_episodes_embedding" in source
        assert "DROP INDEX IF EXISTS idx_facts_embedding" in source
        assert "DROP INDEX IF EXISTS idx_rules_embedding" in source
