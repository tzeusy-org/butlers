"""Tests for the core_041_user_context Alembic migration.

Covers:
  - File layout and module loadability
  - Revision chain (core_041 revises core_040)
  - shared.user_context table columns and constraints
  - UNIQUE constraint on (signal_type, set_by_butler)
  - CHECK constraint on confidence (0.0–1.0)
  - Partial index on signal_type WHERE superseded_at IS NULL AND expires_at > now()
  - Downgrade removes all artifacts
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_041_user_context.py"


def _load_migration():
    """Dynamically load the core_041 migration module."""
    spec = importlib.util.spec_from_file_location("core_041_user_context", MIGRATION_FILE)
    assert spec is not None, f"Cannot locate migration at {MIGRATION_FILE}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """core_041_user_context.py exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"

    def test_migration_file_loadable(self) -> None:
        """core_041_user_context.py can be imported without errors."""
        mod = _load_migration()
        assert mod is not None


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is 'core_041'."""
        mod = _load_migration()
        assert mod.revision == "core_041"

    def test_down_revision(self) -> None:
        """Migration revises core_040."""
        mod = _load_migration()
        assert mod.down_revision == "core_040"

    def test_branch_labels_none(self) -> None:
        """Migration has no branch label (belongs to linear core chain)."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        """downgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# shared.user_context table — columns
# ---------------------------------------------------------------------------


class TestUserContextTable:
    def test_creates_user_context_table(self) -> None:
        """upgrade() creates the shared.user_context table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS shared.user_context" in source

    def test_has_id_column(self) -> None:
        """user_context has a UUID primary key 'id'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "id" in source
        assert "UUID PRIMARY KEY" in source

    @pytest.mark.parametrize(
        "column",
        [
            "signal_type",
            "value",
            "set_by_butler",
            "set_at",
            "expires_at",
            "confidence",
            "metadata",
            "superseded_at",
        ],
    )
    def test_has_required_column(self, column: str) -> None:
        """user_context has all columns specified in the context-bus spec."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert column in source, f"Column '{column}' missing from migration"

    def test_signal_type_not_null(self) -> None:
        """signal_type is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "signal_type   TEXT NOT NULL" in source

    def test_set_by_butler_not_null(self) -> None:
        """set_by_butler is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "set_by_butler TEXT NOT NULL" in source

    def test_set_at_not_null_default_now(self) -> None:
        """set_at is TIMESTAMPTZ NOT NULL with DEFAULT now()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "set_at" in source
        assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in source

    def test_expires_at_not_null(self) -> None:
        """expires_at is TIMESTAMPTZ NOT NULL (no default — caller must supply)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "expires_at    TIMESTAMPTZ NOT NULL" in source

    def test_confidence_real_not_null_default(self) -> None:
        """confidence is REAL NOT NULL with DEFAULT 1.0."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "REAL NOT NULL DEFAULT 1.0" in source

    def test_metadata_jsonb(self) -> None:
        """metadata is JSONB (nullable)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "metadata      JSONB" in source

    def test_superseded_at_nullable(self) -> None:
        """superseded_at is TIMESTAMPTZ without NOT NULL (nullable)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "superseded_at TIMESTAMPTZ" in source

    def test_ensures_shared_schema(self) -> None:
        """upgrade() creates the shared schema with IF NOT EXISTS guard."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE SCHEMA IF NOT EXISTS shared" in source


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    def test_unique_constraint_on_signal_type_set_by_butler(self) -> None:
        """upgrade() defines UNIQUE (signal_type, set_by_butler)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "UNIQUE (signal_type, set_by_butler)" in source

    def test_unique_constraint_named(self) -> None:
        """UNIQUE constraint has an explicit name for easier debugging."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "uq_user_context_signal_butler" in source

    def test_check_constraint_confidence_lower_bound(self) -> None:
        """CHECK constraint includes confidence >= 0.0."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "confidence >= 0.0" in source

    def test_check_constraint_confidence_upper_bound(self) -> None:
        """CHECK constraint includes confidence <= 1.0."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "confidence <= 1.0" in source


# ---------------------------------------------------------------------------
# Partial index
# ---------------------------------------------------------------------------


class TestPartialIndex:
    def test_partial_index_name(self) -> None:
        """upgrade() creates idx_user_context_active_signals."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_user_context_active_signals" in source

    def test_partial_index_on_signal_type(self) -> None:
        """Partial index is on the signal_type column."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ON shared.user_context (signal_type)" in source

    def test_partial_index_where_superseded_at_is_null(self) -> None:
        """Partial index WHERE clause includes superseded_at IS NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "superseded_at IS NULL" in source

    def test_partial_index_where_expires_at_gt_now(self) -> None:
        """Partial index WHERE clause includes expires_at > now()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "expires_at > now()" in source

    def test_partial_index_uses_if_not_exists(self) -> None:
        """Partial index creation uses CREATE INDEX IF NOT EXISTS."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        pos_idx = source.find("idx_user_context_active_signals")
        assert pos_idx != -1, "Partial index name not found in upgrade()"
        before_idx = source[:pos_idx]
        assert "CREATE INDEX IF NOT EXISTS" in before_idx, (
            "Partial index not preceded by CREATE INDEX IF NOT EXISTS"
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_drops_user_context_table(self) -> None:
        """downgrade() drops shared.user_context."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS shared.user_context" in source

    def test_drops_partial_index(self) -> None:
        """downgrade() drops idx_user_context_active_signals."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_user_context_active_signals" in source

    def test_drops_index_before_table(self) -> None:
        """downgrade() drops the index before the table (correct order)."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        pos_idx = source.find("DROP INDEX")
        pos_table = source.find("DROP TABLE")
        assert pos_idx < pos_table, "Index must be dropped before the table in downgrade()"
