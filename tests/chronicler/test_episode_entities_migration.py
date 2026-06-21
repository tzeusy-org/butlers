"""Smoke tests for chronicler_014 episode_entities migration (bu-t0130).

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape:
   - CREATE TABLE episode_entities with composite PK and CHECK constraint.
   - CREATE INDEX episode_entities_entity_idx.
   - CREATE OR REPLACE VIEW v_episodes_corrected includes participant_entity_ids.
   - COALESCE aggregation with role-precedence ORDER BY and FILTER clause.
   - '{}'::uuid[] empty-array fallback (never NULL).
3. downgrade() SQL shape:
   - Restores v_episodes_corrected without participant_entity_ids.
   - Drops the index and table (IF EXISTS), in dependency-safe order.
4. Chain includes 014_episode_entities.py via _resolve_chain_dir.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "roster" / "chronicler" / "migrations"
)
_MIGRATION_FILE = "014_episode_entities.py"
_EXPECTED_REVISION = "chronicler_014"
_EXPECTED_DOWN_REVISION = "chronicler_013"


def _load_migration():
    """Import the migration module by file path."""
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_014", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sqls() -> list[str]:
    """Run upgrade() with op mocked; return SQL strings passed to op.execute."""
    mod = _load_migration()
    calls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = calls.append
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return calls


def _collect_downgrade_sqls() -> list[str]:
    """Run downgrade() with op mocked; return SQL strings passed to op.execute."""
    mod = _load_migration()
    calls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = calls.append
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    return calls


# ---------------------------------------------------------------------------
# Fixtures — module-scoped to avoid repeated importlib loads
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migration_mod():
    """Loaded migration module."""
    return _load_migration()


@pytest.fixture(scope="module")
def upgrade_sqls() -> list[str]:
    """SQL statements emitted by upgrade()."""
    return _collect_upgrade_sqls()


@pytest.fixture(scope="module")
def downgrade_sqls() -> list[str]:
    """SQL statements emitted by downgrade()."""
    return _collect_downgrade_sqls()


# ---------------------------------------------------------------------------
# 1. File structure and revision chain
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """File-level and revision-chain contract tests."""

    def test_revision_chain_links_onto_013(self, migration_mod) -> None:
        """chronicler_014 chains onto chronicler_013 with callable up/down and no
        branch/cross-chain metadata (revision-chain integrity)."""
        assert migration_mod.revision == _EXPECTED_REVISION
        assert migration_mod.down_revision == _EXPECTED_DOWN_REVISION
        assert migration_mod.branch_labels is None
        assert migration_mod.depends_on is None
        assert callable(getattr(migration_mod, "upgrade", None))
        assert callable(getattr(migration_mod, "downgrade", None))

    def test_chronicler_chain_includes_014(self) -> None:
        """Migration chain discovery must pick up 014_episode_entities.py."""
        from butlers.migrations import _resolve_chain_dir

        chain_dir = _resolve_chain_dir("chronicler")
        assert chain_dir is not None, "Chronicler chain directory not found"
        files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
        assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"


# ---------------------------------------------------------------------------
# 2. upgrade() SQL shape
# ---------------------------------------------------------------------------


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() matches the spec (design D1)."""

    def test_creates_episode_entities_table_with_constraints(self, upgrade_sqls: list[str]) -> None:
        """CREATE TABLE episode_entities: episodes(id) ON DELETE CASCADE FK, composite
        PK (episode_id, entity_id), role CHECK (owner/organizer/participant), and NO
        FK to public.entities (entity_id is bare — chronicler isolation convention)."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities statement in upgrade SQL"
        stmt = create_stmts[0]
        assert "REFERENCES episodes(id)" in stmt
        assert "ON DELETE CASCADE" in stmt
        assert "PRIMARY KEY" in stmt
        assert "episode_id" in stmt and "entity_id" in stmt
        assert "CHECK" in stmt
        assert "owner" in stmt and "organizer" in stmt and "participant" in stmt
        # Exactly one REFERENCES clause — entity_id must NOT carry a public.entities FK.
        assert stmt.count("REFERENCES") == 1, (
            "entity_id must NOT have a FK to public.entities; "
            "only episode_id should carry a REFERENCES clause"
        )

    def test_creates_episode_entities_entity_idx(self, upgrade_sqls: list[str]) -> None:
        """CREATE INDEX episode_entities_entity_idx ON episode_entities covering entity_id."""
        idx_stmts = [
            s for s in upgrade_sqls if "CREATE INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert idx_stmts, "No CREATE INDEX episode_entities_entity_idx in upgrade SQL"
        assert "episode_entities" in idx_stmts[0]
        assert "entity_id" in idx_stmts[0]

    def test_view_participant_aggregation_shape(self, upgrade_sqls: list[str]) -> None:
        """v_episodes_corrected.participant_entity_ids is the ONLY guard of the view shape:
        COALESCE(array_agg(... ORDER BY <role-precedence CASE>) FILTER (WHERE entity_id
        IS NOT NULL), '{}'::uuid[]) over a LEFT JOIN to episode_entities so episodes with
        no participants return '{}' (never NULL) and owner>organizer>participant order."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No CREATE OR REPLACE VIEW v_episodes_corrected in upgrade SQL"
        stmt = view_stmts[0]
        assert "participant_entity_ids" in stmt
        assert "array_agg" in stmt.lower()
        assert "COALESCE" in stmt
        assert "FILTER" in stmt and "IS NOT NULL" in stmt
        assert "'{}'::uuid[]" in stmt
        # Role-precedence: CASE WHEN 'owner' THEN 0, 'organizer' THEN 1, ELSE 2
        assert "CASE" in stmt and "'owner'" in stmt and "'organizer'" in stmt
        assert "LEFT JOIN" in stmt and "episode_entities" in stmt

    def test_view_preserves_existing_columns(self, upgrade_sqls: list[str]) -> None:
        """The new view must retain all pre-existing columns including entity_id."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        for col in (
            "e.id",
            "e.source_name",
            "e.source_ref",
            "e.episode_type",
            "e.entity_id",
            "e.created_at",
            "e.updated_at",
            "correction_note",
        ):
            assert col in stmt, f"View is missing expected column reference: {col!r}"


# ---------------------------------------------------------------------------
# 3. downgrade() SQL shape
# ---------------------------------------------------------------------------


class TestDowngradeSQLShape:
    """Verify the SQL emitted by downgrade() correctly reverses the schema change."""

    def test_downgrade_restores_013_view_shape(self, downgrade_sqls: list[str]) -> None:
        """downgrade() DROPs then recreates v_episodes_corrected WITHOUT
        participant_entity_ids but WITH entity_id (chronicler_013 shape); the DROP must
        precede CREATE because PostgreSQL CREATE OR REPLACE cannot remove columns."""
        view_stmts = [
            s for s in downgrade_sqls if "CREATE VIEW" in s and "v_episodes_corrected" in s
        ]
        assert view_stmts, "No CREATE VIEW v_episodes_corrected in downgrade SQL"
        stmt = view_stmts[0]
        assert "participant_entity_ids" not in stmt
        assert "entity_id" in stmt
        drop_view_idx = next(
            (
                i
                for i, s in enumerate(downgrade_sqls)
                if "DROP VIEW" in s and "v_episodes_corrected" in s
            ),
            None,
        )
        create_view_idx = next(
            i
            for i, s in enumerate(downgrade_sqls)
            if "CREATE VIEW" in s and "v_episodes_corrected" in s
        )
        assert drop_view_idx is not None, "downgrade() must DROP VIEW before recreating it"
        assert drop_view_idx < create_view_idx

    def test_downgrade_drops_index_and_table_safely(self, downgrade_sqls: list[str]) -> None:
        """downgrade() DROPs the index and table (both IF EXISTS), and recreates the view
        BEFORE the table drop so the view's dependency on episode_entities is not broken."""
        drop_idx_stmts = [
            s for s in downgrade_sqls if "DROP INDEX" in s and "episode_entities_entity_idx" in s
        ]
        drop_tbl_stmts = [
            s for s in downgrade_sqls if "DROP TABLE" in s and "episode_entities" in s
        ]
        assert drop_idx_stmts, "No DROP INDEX episode_entities_entity_idx in downgrade SQL"
        assert drop_tbl_stmts, "No DROP TABLE episode_entities in downgrade SQL"
        assert "IF EXISTS" in drop_idx_stmts[0]
        assert "IF EXISTS" in drop_tbl_stmts[0]
        create_view_idx = next(
            i
            for i, s in enumerate(downgrade_sqls)
            if "CREATE VIEW" in s and "v_episodes_corrected" in s
        )
        drop_tbl_idx = next(
            i for i, s in enumerate(downgrade_sqls) if "DROP TABLE" in s and "episode_entities" in s
        )
        assert create_view_idx < drop_tbl_idx, (
            "v_episodes_corrected must be recreated BEFORE DROP TABLE episode_entities"
        )
