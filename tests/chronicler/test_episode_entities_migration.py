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
   - Drops the index.
   - Drops the table.
4. Migration ordered after 013_* in the chronicler migrations directory.
5. Chain includes 014_episode_entities.py via _resolve_chain_dir.
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

    def test_migration_file_exists(self) -> None:
        """014_episode_entities.py must exist in the chronicler migrations directory."""
        assert (_MIGRATIONS_DIR / _MIGRATION_FILE).exists(), (
            f"Migration file not found: {_MIGRATIONS_DIR / _MIGRATION_FILE}"
        )

    def test_revision_id(self, migration_mod) -> None:
        """Revision must be chronicler_014."""
        assert migration_mod.revision == _EXPECTED_REVISION

    def test_down_revision_points_to_013(self, migration_mod) -> None:
        """down_revision must chain directly onto chronicler_013."""
        assert migration_mod.down_revision == _EXPECTED_DOWN_REVISION

    def test_branch_labels_none(self, migration_mod) -> None:
        """Non-root migrations must not declare branch_labels."""
        assert migration_mod.branch_labels is None

    def test_depends_on_none(self, migration_mod) -> None:
        """No cross-chain dependency declared."""
        assert migration_mod.depends_on is None

    def test_upgrade_callable(self, migration_mod) -> None:
        """upgrade() must be callable."""
        assert callable(getattr(migration_mod, "upgrade", None))

    def test_downgrade_callable(self, migration_mod) -> None:
        """downgrade() must be callable."""
        assert callable(getattr(migration_mod, "downgrade", None))

    def test_migration_ordered_after_013(self) -> None:
        """014_* must sort after 013_* in the migrations directory."""
        files = sorted(f.name for f in _MIGRATIONS_DIR.glob("[0-9]*.py"))
        idx_013 = next((i for i, f in enumerate(files) if f.startswith("013_")), None)
        idx_014 = next((i for i, f in enumerate(files) if f.startswith("014_")), None)
        assert idx_013 is not None, "013_* migration not found"
        assert idx_014 is not None, "014_* migration not found"
        assert idx_014 > idx_013, "014_* must sort after 013_*"

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

    # ── CREATE TABLE ────────────────────────────────────────────────────────

    def test_creates_episode_entities_table(self, upgrade_sqls: list[str]) -> None:
        """upgrade() emits a CREATE TABLE for episode_entities."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities statement in upgrade SQL"

    def test_episode_entities_has_episode_id_fk(self, upgrade_sqls: list[str]) -> None:
        """episode_id must reference episodes(id) ON DELETE CASCADE."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities"
        stmt = create_stmts[0]
        assert "REFERENCES episodes(id)" in stmt, "episode_id must reference episodes(id)"
        assert "ON DELETE CASCADE" in stmt, "episode_id FK must be ON DELETE CASCADE"

    def test_episode_entities_composite_pk(self, upgrade_sqls: list[str]) -> None:
        """PRIMARY KEY must be (episode_id, entity_id)."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities"
        stmt = create_stmts[0]
        assert "PRIMARY KEY" in stmt, "episode_entities must have a PRIMARY KEY"
        assert "episode_id" in stmt and "entity_id" in stmt, (
            "PRIMARY KEY must reference both episode_id and entity_id"
        )

    def test_episode_entities_role_check_constraint(self, upgrade_sqls: list[str]) -> None:
        """role column must have CHECK (role IN ('owner', 'organizer', 'participant'))."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities"
        stmt = create_stmts[0]
        assert "CHECK" in stmt, "episode_entities.role must have a CHECK constraint"
        assert "owner" in stmt, "CHECK constraint must include 'owner'"
        assert "organizer" in stmt, "CHECK constraint must include 'organizer'"
        assert "participant" in stmt, "CHECK constraint must include 'participant'"

    def test_episode_entities_no_entity_id_fk_to_public(self, upgrade_sqls: list[str]) -> None:
        """entity_id must NOT carry a FK to public.entities (matches chronicler convention)."""
        create_stmts = [s for s in upgrade_sqls if "CREATE TABLE" in s and "episode_entities" in s]
        assert create_stmts, "No CREATE TABLE episode_entities"
        stmt = create_stmts[0]
        # There must be exactly one REFERENCES clause — the episodes FK.
        # If entity_id had a public.entities FK, there would be more than one.
        assert stmt.count("REFERENCES") == 1, (
            "entity_id must NOT have a FK to public.entities; "
            "only episode_id should carry a REFERENCES clause"
        )

    # ── CREATE INDEX ────────────────────────────────────────────────────────

    def test_creates_episode_entities_entity_idx(self, upgrade_sqls: list[str]) -> None:
        """upgrade() emits a CREATE INDEX episode_entities_entity_idx."""
        idx_stmts = [
            s for s in upgrade_sqls if "CREATE INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert idx_stmts, "No CREATE INDEX episode_entities_entity_idx in upgrade SQL"

    def test_index_on_episode_entities_table(self, upgrade_sqls: list[str]) -> None:
        """The index must target the episode_entities table."""
        idx_stmts = [
            s for s in upgrade_sqls if "CREATE INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert idx_stmts, "No CREATE INDEX episode_entities_entity_idx"
        assert "episode_entities" in idx_stmts[0], "Index must be ON episode_entities"

    def test_index_covers_entity_id(self, upgrade_sqls: list[str]) -> None:
        """The index must include entity_id (entity-first look-up)."""
        idx_stmts = [
            s for s in upgrade_sqls if "CREATE INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert idx_stmts, "No CREATE INDEX episode_entities_entity_idx"
        assert "entity_id" in idx_stmts[0], "Index must include entity_id"

    # ── CREATE OR REPLACE VIEW ──────────────────────────────────────────────

    def test_recreates_v_episodes_corrected(self, upgrade_sqls: list[str]) -> None:
        """upgrade() emits CREATE OR REPLACE VIEW v_episodes_corrected."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No CREATE OR REPLACE VIEW v_episodes_corrected in upgrade SQL"

    def test_view_includes_participant_entity_ids_column(self, upgrade_sqls: list[str]) -> None:
        """The new view definition must include participant_entity_ids."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        assert "participant_entity_ids" in view_stmts[0], (
            "v_episodes_corrected must expose participant_entity_ids column"
        )

    def test_view_uses_array_agg_with_coalesce(self, upgrade_sqls: list[str]) -> None:
        """The aggregation uses COALESCE(array_agg(...) FILTER (...), '{}'::uuid[])."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        assert "array_agg" in stmt.lower(), "View must use array_agg for participant_entity_ids"
        assert "COALESCE" in stmt, "View must wrap array_agg in COALESCE for NULL safety"

    def test_view_aggregation_has_filter_clause(self, upgrade_sqls: list[str]) -> None:
        """array_agg must use FILTER (WHERE entity_id IS NOT NULL) to exclude NULLs."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        assert "FILTER" in stmt, "array_agg must have a FILTER clause"
        assert "IS NOT NULL" in stmt, "FILTER must exclude NULL entity_ids"

    def test_view_aggregation_empty_array_fallback(self, upgrade_sqls: list[str]) -> None:
        """COALESCE fallback must be '{}'::uuid[] (never NULL for empty episodes)."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        assert "'{}'::uuid[]" in stmt, (
            "COALESCE fallback must be '{}'::uuid[] to guarantee non-NULL result"
        )

    def test_view_aggregation_has_role_precedence_order(self, upgrade_sqls: list[str]) -> None:
        """ORDER BY in array_agg must encode role-precedence (owner=0, organizer=1, else 2)."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        # Role-precedence: CASE WHEN 'owner' THEN 0, 'organizer' THEN 1, ELSE 2
        assert "CASE" in stmt, "ORDER BY must use a CASE expression for role precedence"
        assert "'owner'" in stmt, "Role precedence CASE must handle 'owner'"
        assert "'organizer'" in stmt, "Role precedence CASE must handle 'organizer'"

    def test_view_left_joins_episode_entities(self, upgrade_sqls: list[str]) -> None:
        """The view must LEFT JOIN episode_entities so episodes with no rows return {}."""
        view_stmts = [s for s in upgrade_sqls if "v_episodes_corrected" in s and "VIEW" in s]
        assert view_stmts, "No view statement"
        stmt = view_stmts[0]
        assert "LEFT JOIN" in stmt, "View must use LEFT JOIN to episode_entities"
        assert "episode_entities" in stmt, "View must reference the episode_entities table"

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

    def test_restores_v_episodes_corrected_without_participant_entity_ids(
        self, downgrade_sqls: list[str]
    ) -> None:
        """downgrade() recreates v_episodes_corrected without participant_entity_ids."""
        view_stmts = [
            s for s in downgrade_sqls if "CREATE VIEW" in s and "v_episodes_corrected" in s
        ]
        assert view_stmts, "No CREATE VIEW v_episodes_corrected in downgrade SQL"
        stmt = view_stmts[0]
        assert "participant_entity_ids" not in stmt, (
            "Downgrade view must NOT include participant_entity_ids"
        )

    def test_downgrade_drops_view_before_recreating(self, downgrade_sqls: list[str]) -> None:
        """downgrade() must DROP VIEW before recreating it.

        PostgreSQL's CREATE OR REPLACE VIEW cannot remove columns, so a
        preceding DROP VIEW is required to correctly restore the 013 shape.
        """
        drop_view_stmts = [
            s for s in downgrade_sqls if "DROP VIEW" in s and "v_episodes_corrected" in s
        ]
        assert drop_view_stmts, (
            "downgrade() must emit DROP VIEW IF EXISTS v_episodes_corrected before CREATE VIEW; "
            "CREATE OR REPLACE VIEW cannot remove columns in PostgreSQL"
        )
        drop_view_idx = next(
            i
            for i, s in enumerate(downgrade_sqls)
            if "DROP VIEW" in s and "v_episodes_corrected" in s
        )
        create_view_idx = next(
            (
                i
                for i, s in enumerate(downgrade_sqls)
                if "CREATE VIEW" in s and "v_episodes_corrected" in s
            ),
            None,
        )
        assert create_view_idx is not None, "No CREATE VIEW v_episodes_corrected in downgrade SQL"
        assert drop_view_idx < create_view_idx, (
            "DROP VIEW must come before CREATE VIEW in downgrade"
        )

    def test_drops_episode_entities_index(self, downgrade_sqls: list[str]) -> None:
        """downgrade() emits DROP INDEX for episode_entities_entity_idx."""
        drop_idx_stmts = [
            s for s in downgrade_sqls if "DROP INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert drop_idx_stmts, "No DROP INDEX episode_entities_entity_idx in downgrade SQL"

    def test_drops_episode_entities_table(self, downgrade_sqls: list[str]) -> None:
        """downgrade() emits DROP TABLE for episode_entities."""
        drop_tbl_stmts = [
            s for s in downgrade_sqls if "DROP TABLE" in s and "episode_entities" in s
        ]
        assert drop_tbl_stmts, "No DROP TABLE episode_entities in downgrade SQL"

    def test_downgrade_view_restored_before_table_drop(self, downgrade_sqls: list[str]) -> None:
        """View is dropped+recreated before the table drop to avoid breaking view dependencies."""
        create_view_idx = next(
            (
                i
                for i, s in enumerate(downgrade_sqls)
                if "CREATE VIEW" in s and "v_episodes_corrected" in s
            ),
            None,
        )
        drop_tbl_idx = next(
            (
                i
                for i, s in enumerate(downgrade_sqls)
                if "DROP TABLE" in s and "episode_entities" in s
            ),
            None,
        )
        assert create_view_idx is not None, "No CREATE VIEW v_episodes_corrected in downgrade SQL"
        assert drop_tbl_idx is not None, "No DROP TABLE episode_entities in downgrade SQL"
        assert create_view_idx < drop_tbl_idx, (
            "v_episodes_corrected must be recreated BEFORE DROP TABLE episode_entities"
        )

    def test_downgrade_restores_entity_id_in_view(self, downgrade_sqls: list[str]) -> None:
        """The restored view must still include the entity_id column (chronicler_013 shape)."""
        view_stmts = [
            s for s in downgrade_sqls if "CREATE VIEW" in s and "v_episodes_corrected" in s
        ]
        assert view_stmts, "No CREATE VIEW v_episodes_corrected statement in downgrade SQL"
        assert "entity_id" in view_stmts[0], (
            "Downgraded v_episodes_corrected must retain entity_id (chronicler_013 shape)"
        )

    def test_drop_index_uses_if_exists(self, downgrade_sqls: list[str]) -> None:
        """DROP INDEX must use IF EXISTS for safety."""
        drop_idx_stmts = [
            s for s in downgrade_sqls if "DROP INDEX" in s and "episode_entities_entity_idx" in s
        ]
        assert drop_idx_stmts, "No DROP INDEX episode_entities_entity_idx in downgrade SQL"
        assert "IF EXISTS" in drop_idx_stmts[0], "DROP INDEX must include IF EXISTS guard"

    def test_drop_table_uses_if_exists(self, downgrade_sqls: list[str]) -> None:
        """DROP TABLE must use IF EXISTS for safety."""
        drop_tbl_stmts = [
            s for s in downgrade_sqls if "DROP TABLE" in s and "episode_entities" in s
        ]
        assert drop_tbl_stmts, "No DROP TABLE episode_entities in downgrade SQL"
        assert "IF EXISTS" in drop_tbl_stmts[0], "DROP TABLE must include IF EXISTS guard"
