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
    """Revision chain contract test."""

    def test_revision_chain(self) -> None:
        """rel_018 -> rel_017, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "rel_018"
        assert mod.down_revision == "rel_017"
        assert mod.branch_labels is None
        assert mod.depends_on is None


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

    def test_upgrade_rewrites_contact_to_entity_via_contacts_join(self) -> None:
        """upgrade() COUNTs contact:%% interaction subjects then UPDATEs subject to
        'entity:' || entity_id via a public.contacts join, skipping NULL entity_id."""
        sqls = self._collect_upgrade_sqls()
        assert len(sqls) == 3, f"Expected COUNT, UPDATE, COUNT after guard, got {len(sqls)}"
        count_sql, update_sql, skip_sql = sqls
        assert "contact:%" in count_sql
        assert "interaction_%" in count_sql
        assert "entity:" in update_sql
        assert "entity_id" in update_sql
        assert "contacts" in update_sql.lower()
        assert "IS NOT NULL" in update_sql.upper()  # NULL entity_id orphans skipped
        assert "relationship" in update_sql
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

    def test_downgrade_rewrites_entity_to_contact_via_facts_entity_id(self) -> None:
        """downgrade() COUNTs entity:%% interaction subjects then UPDATEs subject to
        'contact:' || contact_id via facts.entity_id (no subject parsing), scoped."""
        sqls = self._collect_downgrade_sqls()
        assert len(sqls) == 3, f"Expected COUNT, UPDATE, COUNT, got {len(sqls)}"
        count_sql, update_sql, _ = sqls
        assert "entity:%" in count_sql
        assert "interaction_%" in count_sql
        assert "contact:" in update_sql
        assert "entity_id" in update_sql
        assert "contacts" in update_sql.lower()
        assert "relationship" in update_sql
