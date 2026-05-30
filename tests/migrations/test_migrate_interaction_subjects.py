"""Tests for rel_018 migrate_interaction_subjects migration.

Covers:
1. Migration file existence and revision chain (unit — no DB required).
2. Upgrade SQL shape: rewrites contact: → entity: subjects via contacts join.
3. Downgrade SQL shape: reverts entity: → contact: via facts.entity_id column.
4. Facts-table guard: upgrade/downgrade are no-ops when the facts table is absent.
5. NULL entity_id handling: rows with no entity_id link are left unchanged.
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

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "018_migrate_interaction_subjects.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("rel_018", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Chain and file tests
# ---------------------------------------------------------------------------


class TestMigrationChain:
    """Revision chain and module-level contract tests."""

    def test_migration_file_exists(self) -> None:
        """018_migrate_interaction_subjects.py exists at the expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """revision is rel_018."""
        mod = _load_migration()
        assert mod.revision == "rel_018"

    def test_down_revision(self) -> None:
        """down_revision points to rel_017."""
        mod = _load_migration()
        assert mod.down_revision == "rel_017"

    def test_branch_labels_none(self) -> None:
        """Non-root migrations must not declare branch_labels."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """No cross-chain dependency declared."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is a callable."""
        mod = _load_migration()
        assert callable(getattr(mod, "upgrade", None))

    def test_downgrade_callable(self) -> None:
        """downgrade() is a callable."""
        mod = _load_migration()
        assert callable(getattr(mod, "downgrade", None))


# ---------------------------------------------------------------------------
# Facts-table guard tests
# ---------------------------------------------------------------------------


class TestFactsTableGuard:
    """Upgrade and downgrade are no-ops when the facts table is absent."""

    def _make_conn_no_facts(self):
        mock_conn = MagicMock()
        result = MagicMock()
        result.scalar.return_value = None  # to_regclass returns None → table absent
        mock_conn.execute.return_value = result
        return mock_conn

    def test_upgrade_no_op_when_facts_absent(self) -> None:
        """upgrade() exits after the guard when facts table does not exist."""
        mod = _load_migration()
        mock_conn = self._make_conn_no_facts()
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        # Only the to_regclass check should have been executed.
        assert mock_conn.execute.call_count == 1

    def test_downgrade_no_op_when_facts_absent(self) -> None:
        """downgrade() exits after the guard when facts table does not exist."""
        mod = _load_migration()
        mock_conn = self._make_conn_no_facts()
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.downgrade()

        assert mock_conn.execute.call_count == 1


# ---------------------------------------------------------------------------
# Upgrade SQL shape tests
# ---------------------------------------------------------------------------


class TestUpgradeSQLShape:
    """Verify that upgrade() emits the expected SQL."""

    def _collect_upgrade_sqls(self, total_contact_count: int = 5) -> list[str]:
        """Run upgrade() with mocked connection and collect SQL strings."""
        mod = _load_migration()
        sqls: list[str] = []
        call_count = [0]

        mock_conn = MagicMock()

        def _execute(stmt):
            call_count[0] += 1
            sql = str(stmt)
            sqls.append(sql)
            result = MagicMock()
            if call_count[0] == 1:
                # to_regclass guard — facts table exists
                result.scalar.return_value = "facts"
            elif call_count[0] == 2:
                # COUNT of contact: interaction subjects
                result.scalar.return_value = total_contact_count
            else:
                # UPDATE rowcount / COUNT for remaining
                result.rowcount = total_contact_count
                result.scalar.return_value = 0  # skipped count = 0
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        # Skip first call (to_regclass guard).
        return sqls[1:]

    def test_upgrade_emits_count_then_update_then_skipped_count(self) -> None:
        """upgrade() emits a COUNT query, one UPDATE, then a follow-up COUNT."""
        sqls = self._collect_upgrade_sqls()
        assert len(sqls) == 3, f"Expected 3 statements after guard, got {len(sqls)}: {sqls}"

    def test_upgrade_count_query_uses_contact_prefix(self) -> None:
        """First SQL checks for interaction facts with contact: subjects."""
        sqls = self._collect_upgrade_sqls()
        count_sql = sqls[0]
        assert "contact:%" in count_sql
        assert "interaction_%" in count_sql

    def test_upgrade_update_rewrites_subject_to_entity_prefix(self) -> None:
        """UPDATE sets subject to 'entity:' || entity_id."""
        sqls = self._collect_upgrade_sqls()
        update_sql = sqls[1]
        assert "entity:" in update_sql
        assert "entity_id" in update_sql

    def test_upgrade_update_reads_from_contacts_table(self) -> None:
        """UPDATE joins public.contacts to map contact_id → entity_id."""
        sqls = self._collect_upgrade_sqls()
        update_sql = sqls[1]
        assert "contacts" in update_sql.lower()

    def test_upgrade_update_filters_null_entity_id(self) -> None:
        """UPDATE WHERE clause requires c.entity_id IS NOT NULL (skip orphans)."""
        sqls = self._collect_upgrade_sqls()
        update_sql = sqls[1]
        assert "IS NOT NULL" in update_sql.upper()

    def test_upgrade_update_scoped_to_relationship(self) -> None:
        """UPDATE is scoped to scope = 'relationship'."""
        sqls = self._collect_upgrade_sqls()
        update_sql = sqls[1]
        assert "relationship" in update_sql

    def test_upgrade_skip_count_still_uses_contact_prefix(self) -> None:
        """Final COUNT verifies any remaining unmigrated rows use contact: prefix."""
        sqls = self._collect_upgrade_sqls()
        skip_sql = sqls[2]
        assert "contact:%" in skip_sql

    def test_upgrade_no_op_when_zero_contact_subjects(self) -> None:
        """upgrade() exits early (no UPDATE) when there are no contact: subjects."""
        mod = _load_migration()
        sqls: list[str] = []
        call_count = [0]

        mock_conn = MagicMock()

        def _execute(stmt):
            call_count[0] += 1
            sql = str(stmt)
            sqls.append(sql)
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar.return_value = "facts"  # table exists
            else:
                result.scalar.return_value = 0  # zero rows to migrate
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        # Only guard + COUNT; no UPDATE.
        assert call_count[0] == 2, f"Expected 2 calls (guard + count), got {call_count[0]}"


# ---------------------------------------------------------------------------
# Downgrade SQL shape tests
# ---------------------------------------------------------------------------


class TestDowngradeSQLShape:
    """Verify that downgrade() emits the expected SQL."""

    def _collect_downgrade_sqls(self, total_entity_count: int = 5) -> list[str]:
        """Run downgrade() with mocked connection and collect SQL strings."""
        mod = _load_migration()
        sqls: list[str] = []
        call_count = [0]

        mock_conn = MagicMock()

        def _execute(stmt):
            call_count[0] += 1
            sql = str(stmt)
            sqls.append(sql)
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar.return_value = "facts"
            elif call_count[0] == 2:
                result.scalar.return_value = total_entity_count
            else:
                result.rowcount = total_entity_count
                result.scalar.return_value = 0
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.downgrade()

        return sqls[1:]

    def test_downgrade_emits_count_then_update_then_remaining_count(self) -> None:
        """downgrade() emits a COUNT, one UPDATE, then a final COUNT."""
        sqls = self._collect_downgrade_sqls()
        assert len(sqls) == 3, f"Expected 3 statements, got {len(sqls)}: {sqls}"

    def test_downgrade_count_query_uses_entity_prefix(self) -> None:
        """First SQL checks for interaction facts with entity: subjects."""
        sqls = self._collect_downgrade_sqls()
        count_sql = sqls[0]
        assert "entity:%" in count_sql
        assert "interaction_%" in count_sql

    def test_downgrade_update_rewrites_subject_to_contact_prefix(self) -> None:
        """Downgrade UPDATE sets subject to 'contact:' || contact_id."""
        sqls = self._collect_downgrade_sqls()
        update_sql = sqls[1]
        assert "contact:" in update_sql

    def test_downgrade_update_uses_facts_entity_id_column(self) -> None:
        """Downgrade UPDATE joins contacts via facts.entity_id — no subject parsing."""
        sqls = self._collect_downgrade_sqls()
        update_sql = sqls[1]
        assert "entity_id" in update_sql
        assert "contacts" in update_sql.lower()

    def test_downgrade_update_scoped_to_relationship(self) -> None:
        """Downgrade UPDATE is scoped to scope = 'relationship'."""
        sqls = self._collect_downgrade_sqls()
        update_sql = sqls[1]
        assert "relationship" in update_sql
