"""Tests for the core_040_corrections Alembic migration.

Covers:
  - File layout and module loadability
  - Revision chain (core_040 revises core_039)
  - corrections table columns and constraints
  - Indexes on target_session_id, correcting_session_id, created_at, correction_type
  - Downgrade removes all artifacts
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_040_corrections.py"


def _load_migration():
    """Dynamically load the core_040 migration module."""
    spec = importlib.util.spec_from_file_location("core_040_corrections", MIGRATION_FILE)
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
        """core_040_corrections.py exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"

    def test_migration_file_loadable(self) -> None:
        """core_040_corrections.py can be imported without errors."""
        mod = _load_migration()
        assert mod is not None


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is 'core_040'."""
        mod = _load_migration()
        assert mod.revision == "core_040"

    def test_down_revision(self) -> None:
        """Migration revises core_039."""
        mod = _load_migration()
        assert mod.down_revision == "core_039"

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
# corrections table — columns
# ---------------------------------------------------------------------------


class TestCorrectionsTable:
    def test_creates_corrections_table(self) -> None:
        """upgrade() creates the corrections table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS corrections" in source

    def test_has_id_column(self) -> None:
        """corrections has UUID primary key 'id'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "id UUID PRIMARY KEY" in source

    @pytest.mark.parametrize(
        "column",
        [
            "correction_type",
            "target_session_id",
            "correcting_session_id",
            "description",
            "status",
            "summary",
            "original_data_snapshot",
            "correction_details",
            "created_at",
        ],
    )
    def test_has_required_column(self, column: str) -> None:
        """corrections has all columns expected by corrections.py."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert column in source, f"Column '{column}' missing from migration"

    def test_correction_type_check_constraint(self) -> None:
        """correction_type has a CHECK constraint covering all four types."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for value in ("data_correction", "memory_deletion", "misroute", "action_reversal"):
            assert value in source, f"correction_type value '{value}' missing from CHECK constraint"

    def test_status_check_constraint(self) -> None:
        """status has a CHECK constraint covering all three valid values."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for value in ("applied", "partially_applied", "failed"):
            assert value in source, f"status value '{value}' missing from CHECK constraint"

    def test_target_session_id_fk(self) -> None:
        """target_session_id has a FK reference to sessions(id)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES sessions(id)" in source

    def test_created_at_default_now(self) -> None:
        """created_at defaults to now()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DEFAULT now()" in source

    def test_original_data_snapshot_jsonb(self) -> None:
        """original_data_snapshot is JSONB."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "original_data_snapshot JSONB" in source

    def test_correction_details_jsonb(self) -> None:
        """correction_details is JSONB."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "correction_details JSONB" in source


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


class TestIndexes:
    def test_index_on_target_session_id(self) -> None:
        """upgrade() creates an index on target_session_id."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_corrections_target_session_id" in source
        assert "ON corrections (target_session_id)" in source

    def test_index_on_correcting_session_id(self) -> None:
        """upgrade() creates an index on correcting_session_id (rate-limit path)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_corrections_correcting_session_id" in source
        assert "ON corrections (correcting_session_id)" in source

    def test_index_on_created_at(self) -> None:
        """upgrade() creates an index on created_at."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_corrections_created_at" in source
        assert "ON corrections (created_at)" in source

    def test_index_on_correction_type(self) -> None:
        """upgrade() creates an index on correction_type."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_corrections_correction_type" in source
        assert "ON corrections (correction_type)" in source

    def test_all_indexes_use_if_not_exists(self) -> None:
        """All index creation statements use IF NOT EXISTS."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Each CREATE INDEX must be guarded
        idx_names = [
            "idx_corrections_target_session_id",
            "idx_corrections_correcting_session_id",
            "idx_corrections_created_at",
            "idx_corrections_correction_type",
        ]
        for idx in idx_names:
            # Verify IF NOT EXISTS appears before the index name in the source
            pos_guard = source.find("CREATE INDEX IF NOT EXISTS")
            pos_idx = source.find(idx)
            assert pos_guard < pos_idx, f"Index '{idx}' not preceded by CREATE INDEX IF NOT EXISTS"


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_drops_corrections_table(self) -> None:
        """downgrade() drops the corrections table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS corrections" in source

    def test_drops_target_session_index(self) -> None:
        """downgrade() drops the target_session_id index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_corrections_target_session_id" in source

    def test_drops_correcting_session_index(self) -> None:
        """downgrade() drops the correcting_session_id index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_corrections_correcting_session_id" in source

    def test_drops_created_at_index(self) -> None:
        """downgrade() drops the created_at index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_corrections_created_at" in source

    def test_drops_correction_type_index(self) -> None:
        """downgrade() drops the correction_type index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_corrections_correction_type" in source

    def test_drops_indexes_before_table(self) -> None:
        """downgrade() drops indexes before the table (correct dependency order)."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        pos_idx = source.find("DROP INDEX")
        pos_table = source.find("DROP TABLE")
        assert pos_idx < pos_table, "Indexes must be dropped before the table in downgrade()"
