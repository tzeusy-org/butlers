"""Tests for sw_013 Gmail replay_safe=FALSE data-fix migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. SQL shape: upgrade() emits an idempotent UPDATE targeting connector_type='gmail'.
3. SQL shape: downgrade() emits a matching UPDATE restoring replay_safe=TRUE.
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
    / "switchboard"
    / "migrations"
    / "013_gmail_replay_safe_false.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("sw_013", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_execute_calls(fn_name: str) -> list[str]:
    """Run upgrade() or downgrade() with op.execute mocked; return SQL strings."""
    mod = _load_migration()
    calls_collected: list[str] = []

    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)

    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()

    return calls_collected


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain contract test."""

    def test_revision_chain(self) -> None:
        """sw_013 -> sw_012, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "sw_013"
        assert mod.down_revision == "sw_012"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() matches the spec requirements."""

    def test_upgrade_sets_replay_safe_false_scoped_to_gmail(self) -> None:
        """upgrade() emits one UPDATE on connector_registry SET replay_safe=FALSE for gmail."""
        sqls = _collect_execute_calls("upgrade")
        assert len(sqls) == 1
        sql = sqls[0]
        assert sql.strip().upper().startswith("UPDATE")
        assert "connector_registry" in sql
        upper = sql.upper()
        assert "REPLAY_SAFE" in upper and "FALSE" in upper
        assert "gmail" in sql.lower()

    def test_upgrade_idempotent_via_where_replay_safe_true(self) -> None:
        """upgrade() restricts to rows still TRUE so re-running is a no-op."""
        sqls = _collect_execute_calls("upgrade")
        upper = sqls[0].upper()
        assert "REPLAY_SAFE" in upper and "TRUE" in upper


class TestDowngradeSQLShape:
    """Verify the SQL emitted by downgrade() correctly inverts the upgrade."""

    def test_downgrade_restores_replay_safe_true_scoped_to_gmail(self) -> None:
        """downgrade() emits one UPDATE on connector_registry SET replay_safe=TRUE for gmail."""
        sqls = _collect_execute_calls("downgrade")
        assert len(sqls) == 1
        sql = sqls[0]
        assert sql.strip().upper().startswith("UPDATE")
        assert "connector_registry" in sql
        upper = sql.upper()
        assert "REPLAY_SAFE" in upper and "TRUE" in upper
        assert "gmail" in sql.lower()
