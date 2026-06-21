"""Tests for rel_012 backfill_interaction_predicates migration.

Covers (condensed):
1. Revision chain (rel_012 -> rel_011).
2. SQL shape: typed + fallback UPDATE, idempotency WHERE predicate='interaction'.
3. Facts-table guard: upgrade/downgrade are no-ops when the facts table is absent.
4. Downgrade restores interaction_* to 'interaction'.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "012_backfill_interaction_predicates.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("rel_012", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_sqls(fn_name: str) -> list[str]:
    """Run upgrade()/downgrade() with facts present; return SQL after the to_regclass guard."""
    mod = _load_migration()
    sqls: list[str] = []
    call_count = [0]
    mock_conn = MagicMock()

    def _execute(stmt):
        call_count[0] += 1
        sqls.append(str(stmt))
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar.return_value = "facts"
        result.rowcount = 0
        return result

    mock_conn.execute.side_effect = _execute
    mock_op = MagicMock()
    mock_op.get_bind.return_value = mock_conn
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return sqls[1:]


class TestMigrationChain:
    def test_revision_chain(self) -> None:
        """revision rel_012 -> down_revision rel_011, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "rel_012"
        assert mod.down_revision == "rel_011"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQLShape:
    def test_upgrade_emits_typed_then_fallback(self) -> None:
        """upgrade() emits exactly 2 UPDATEs: typed interaction_<type> then interaction_other."""
        sqls = _collect_sqls("upgrade")
        assert len(sqls) == 2, f"Expected 2 UPDATE statements, got {len(sqls)}: {sqls}"
        step1, step2 = sqls
        # typed: predicate = 'interaction_' || metadata->>'type', scoped to relationship
        assert "interaction_" in step1
        assert "metadata->>'type'" in step1
        assert "IS NOT NULL" in step1.upper()
        assert "!= ''" in step1
        assert "relationship" in step1
        # fallback for NULL/empty type
        assert "interaction_other" in step2
        assert "relationship" in step2

    @pytest.mark.parametrize("idx", [0, 1])
    def test_upgrade_steps_filter_unmigrated_interaction_rows(self, idx: int) -> None:
        """Both UPDATEs filter predicate='interaction' (not LIKE), so re-runs are no-ops."""
        sqls = _collect_sqls("upgrade")
        sql_lower = sqls[idx].lower()
        assert "where" in sql_lower
        assert "predicate = 'interaction'" in sql_lower or (
            "predicate" in sql_lower and "interaction" in sql_lower and "%" not in sql_lower
        ), f"Step must filter predicate='interaction' (not LIKE), got:\n{sqls[idx]}"


class TestDowngradeSQLShape:
    def test_downgrade_restores_singular_interaction(self) -> None:
        """downgrade() emits one UPDATE restoring interaction_* -> 'interaction' via LIKE, scoped."""
        sqls = _collect_sqls("downgrade")
        assert len(sqls) == 1, f"Expected 1 UPDATE in downgrade, got {len(sqls)}"
        sql = sqls[0]
        assert "predicate = 'interaction'" in sql or "predicate='interaction'" in sql.replace(
            " ", ""
        )
        assert "interaction_%" in sql or "LIKE" in sql.upper()
        assert "relationship" in sql


class TestFactsTableGuard:
    """When the facts table does not exist, upgrade/downgrade must be no-ops (guard call only)."""

    def _run_with_no_facts(self, fn_name: str) -> int:
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

    @pytest.mark.parametrize("fn_name", ["upgrade", "downgrade"])
    def test_no_op_when_facts_absent(self, fn_name: str) -> None:
        """Only the to_regclass guard fires when facts table is absent; no UPDATE."""
        assert self._run_with_no_facts(fn_name) == 1
