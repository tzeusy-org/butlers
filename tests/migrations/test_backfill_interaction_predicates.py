"""Tests for rel_012 backfill_interaction_predicates migration.

Covers:
1. Migration file existence and revision chain (unit — no DB required).
2. SQL shape: upgrade emits the expected WHERE/SET clauses.
3. Idempotency: the WHERE clause excludes already-migrated rows on a second run.
4. Fallback path: rows with NULL/empty metadata type produce 'interaction_other'.
5. Typed extraction: rows with metadata type value produce 'interaction_{type}'.
6. Facts-table guard: upgrade/downgrade are no-ops when the facts table is absent.
7. Downgrade SQL shape: restores interaction_* to 'interaction'.
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
    / "012_backfill_interaction_predicates.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("rel_012", _MIGRATION_PATH)
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
        """012_backfill_interaction_predicates.py exists at the expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """revision is rel_012."""
        mod = _load_migration()
        assert mod.revision == "rel_012"

    def test_down_revision(self) -> None:
        """down_revision points to rel_011 (the interaction-index migration)."""
        mod = _load_migration()
        assert mod.down_revision == "rel_011"

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
# SQL shape tests
# ---------------------------------------------------------------------------


class TestUpgradeSQLShape:
    """Verify that upgrade() emits SQL matching the spec."""

    def _collect_upgrade_sqls(self) -> list[str]:
        mod = _load_migration()
        sqls: list[str] = []

        mock_conn = MagicMock()
        call_count = [0]

        def _execute(stmt):
            call_count[0] += 1
            sql = str(stmt)
            sqls.append(sql)
            result = MagicMock()
            if call_count[0] == 1:
                # First call is the to_regclass guard
                result.scalar.return_value = "facts"
            result.rowcount = 0
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        # Skip the first call (to_regclass guard), return the rest
        return sqls[1:]

    def test_upgrade_emits_two_update_statements(self) -> None:
        """upgrade() emits exactly 2 UPDATE statements (typed + fallback)."""
        sqls = self._collect_upgrade_sqls()
        assert len(sqls) == 2, f"Expected 2 UPDATE statements, got {len(sqls)}: {sqls}"

    def test_upgrade_step1_contains_typed_predicate_concatenation(self) -> None:
        """Step 1 UPDATE sets predicate = 'interaction_' || (metadata->>'type')."""
        sqls = self._collect_upgrade_sqls()
        step1 = sqls[0]
        assert "interaction_" in step1
        assert "metadata->>'type'" in step1

    def test_upgrade_step1_where_filters_interaction_predicate(self) -> None:
        """Step 1 UPDATE WHERE clause filters on predicate = 'interaction'."""
        sqls = self._collect_upgrade_sqls()
        step1 = sqls[0]
        upper = step1.upper()
        assert "WHERE" in upper
        assert "predicate" in step1.lower()
        assert "interaction" in step1

    def test_upgrade_step1_excludes_null_and_empty_type(self) -> None:
        """Step 1 WHERE clause requires metadata->>'type' IS NOT NULL and != ''."""
        sqls = self._collect_upgrade_sqls()
        step1 = sqls[0]
        assert "IS NOT NULL" in step1.upper()
        assert "!= ''" in step1

    def test_upgrade_step1_scoped_to_relationship(self) -> None:
        """Step 1 UPDATE is scoped to scope = 'relationship'."""
        sqls = self._collect_upgrade_sqls()
        step1 = sqls[0]
        assert "relationship" in step1

    def test_upgrade_step2_sets_interaction_other(self) -> None:
        """Step 2 UPDATE sets predicate = 'interaction_other' for NULL/empty type."""
        sqls = self._collect_upgrade_sqls()
        step2 = sqls[1]
        assert "interaction_other" in step2

    def test_upgrade_step2_where_still_filters_interaction_predicate(self) -> None:
        """Step 2 WHERE clause still filters on predicate = 'interaction' (not yet migrated rows)."""
        sqls = self._collect_upgrade_sqls()
        step2 = sqls[1]
        upper = step2.upper()
        assert "WHERE" in upper
        assert "interaction" in step2

    def test_upgrade_step2_scoped_to_relationship(self) -> None:
        """Step 2 UPDATE is also scoped to scope = 'relationship'."""
        sqls = self._collect_upgrade_sqls()
        step2 = sqls[1]
        assert "relationship" in step2


class TestDowngradeSQLShape:
    """Verify that downgrade() emits SQL that reverts the predicate shape."""

    def _collect_downgrade_sqls(self) -> list[str]:
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
            result.rowcount = 0
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.downgrade()

        return sqls[1:]

    def test_downgrade_emits_one_update(self) -> None:
        """downgrade() emits exactly one UPDATE statement."""
        sqls = self._collect_downgrade_sqls()
        assert len(sqls) == 1, f"Expected 1 UPDATE in downgrade, got {len(sqls)}"

    def test_downgrade_restores_to_singular_interaction(self) -> None:
        """Downgrade UPDATE sets predicate = 'interaction'."""
        sqls = self._collect_downgrade_sqls()
        sql = sqls[0]
        assert "predicate = 'interaction'" in sql or "predicate='interaction'" in sql.replace(
            " ", ""
        )

    def test_downgrade_uses_like_interaction_wildcard(self) -> None:
        """Downgrade WHERE clause uses LIKE 'interaction_%' to catch all typed variants."""
        sqls = self._collect_downgrade_sqls()
        sql = sqls[0]
        assert "interaction_%" in sql or "LIKE" in sql.upper()

    def test_downgrade_scoped_to_relationship(self) -> None:
        """Downgrade UPDATE is scoped to scope = 'relationship'."""
        sqls = self._collect_downgrade_sqls()
        assert "relationship" in sqls[0]


# ---------------------------------------------------------------------------
# Facts-table guard tests
# ---------------------------------------------------------------------------


class TestFactsTableGuard:
    """When the facts table does not exist, upgrade/downgrade must be no-ops."""

    def _run_with_no_facts(self, fn_name: str) -> int:
        """Run upgrade() or downgrade() with facts absent; return conn.execute call count."""
        mod = _load_migration()
        mock_conn = MagicMock()
        result = MagicMock()
        result.scalar.return_value = None  # facts table absent
        mock_conn.execute.return_value = result

        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            getattr(mod, fn_name)()

        return mock_conn.execute.call_count

    def test_upgrade_no_op_when_facts_absent(self) -> None:
        """upgrade() makes only the to_regclass guard call when facts table is absent."""
        call_count = self._run_with_no_facts("upgrade")
        # Only the to_regclass check should fire; no UPDATE calls
        assert call_count == 1, (
            f"Expected exactly 1 execute call (guard) when facts absent, got {call_count}"
        )

    def test_downgrade_no_op_when_facts_absent(self) -> None:
        """downgrade() makes only the to_regclass guard call when facts table is absent."""
        call_count = self._run_with_no_facts("downgrade")
        assert call_count == 1, (
            f"Expected exactly 1 execute call (guard) when facts absent, got {call_count}"
        )


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Step 2 acts as a catch-all, so running upgrade twice is safe (no rows remain)."""

    def test_upgrade_step2_only_targets_predicate_interaction(self) -> None:
        """Step 2's WHERE still filters predicate='interaction', so rows already migrated
        to 'interaction_{type}' or 'interaction_other' are excluded on a second run."""
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
            result.rowcount = 0
            return result

        mock_conn.execute.side_effect = _execute
        mock_op = MagicMock()
        mock_op.get_bind.return_value = mock_conn

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        # Both step1 and step2 filter on predicate = 'interaction', not predicate LIKE 'interaction_%'
        # Rows already migrated to interaction_call / interaction_other are unaffected
        for sql in sqls[1:]:
            # The WHERE clause must target the original 'interaction' predicate value,
            # not the already-migrated 'interaction_*' variants
            sql_lower = sql.lower()
            assert "predicate = 'interaction'" in sql_lower or (
                "predicate" in sql_lower and "interaction" in sql_lower and "%" not in sql_lower
            ), f"Expected step to filter on predicate='interaction' (not LIKE), got:\n{sql}"

    def test_second_upgrade_executes_same_sql_shape(self) -> None:
        """A second upgrade() call emits the same SQL shape (rowcount=0 is safe)."""
        mod = _load_migration()

        for _run in range(2):
            sqls: list[str] = []
            call_count = [0]
            mock_conn = MagicMock()

            def _execute(stmt, _cc=call_count, _sq=sqls):
                _cc[0] += 1
                _sq.append(str(stmt))
                result = MagicMock()
                if _cc[0] == 1:
                    result.scalar.return_value = "facts"
                result.rowcount = 0
                return result

            mock_conn.execute.side_effect = _execute
            mock_op = MagicMock()
            mock_op.get_bind.return_value = mock_conn

            with patch.object(mod, "op", mock_op):
                mod.upgrade()  # Must not raise regardless of rowcount

        # Verify that the second run still emitted the expected guard and update statements
        assert len(sqls) == 3


# ---------------------------------------------------------------------------
# Predicate transformation tests (mock-based row simulation)
# ---------------------------------------------------------------------------


class TestPredicateTransformation:
    """Verify the SQL logic correctly models the typed and fallback paths."""

    def test_typed_row_sql_produces_interaction_type(self) -> None:
        """Step 1 SQL contains concatenation producing 'interaction_' || type value."""
        source = _MIGRATION_PATH.read_text()

        # The source must concatenate 'interaction_' with the type from metadata
        assert "interaction_" in source
        assert "||" in source  # SQL string concatenation operator

    def test_fallback_row_sql_sets_interaction_other(self) -> None:
        """Step 2 SQL contains the literal 'interaction_other' fallback value."""
        source = _MIGRATION_PATH.read_text()
        assert "interaction_other" in source

    def test_step1_type_extraction_from_metadata_jsonb(self) -> None:
        """Step 1 extracts type via metadata->>'type' (JSONB text extraction)."""
        source = _MIGRATION_PATH.read_text()
        assert "metadata->>'type'" in source

    def test_step1_excludes_empty_string_type(self) -> None:
        """Step 1 WHERE clause guards against empty string type (not just NULL)."""
        source = _MIGRATION_PATH.read_text()
        # The migration guards: AND metadata->>'type' != ''
        assert "!= ''" in source

    def test_step2_where_scope_relationship(self) -> None:
        """Step 2 WHERE includes scope = 'relationship' to avoid touching other scopes."""
        source = _MIGRATION_PATH.read_text()
        # Count occurrences of 'relationship' in the source; at minimum step1 + step2 + downgrade
        occurrences = source.count("'relationship'")
        assert occurrences >= 2, (
            f"Expected 'relationship' to appear in multiple UPDATE statements, found {occurrences}"
        )
