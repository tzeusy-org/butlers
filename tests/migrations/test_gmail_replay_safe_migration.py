"""Tests for sw_013 Gmail replay_safe=FALSE data-fix migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. SQL shape: upgrade() emits an idempotent UPDATE targeting connector_type='gmail'.
3. SQL shape: downgrade() emits a matching UPDATE restoring replay_safe=TRUE.
4. REPLAY_UNSAFE_CONNECTOR_TYPES constant includes 'gmail'.
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
    """File-level and revision-chain contract tests."""

    def test_migration_file_exists(self) -> None:
        """013_gmail_replay_safe_false.py exists at expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """Revision is sw_013."""
        mod = _load_migration()
        assert mod.revision == "sw_013"

    def test_down_revision(self) -> None:
        """down_revision points to sw_012 (the replay_safe column migration)."""
        mod = _load_migration()
        assert mod.down_revision == "sw_012"

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

    def test_replay_unsafe_connector_types_includes_gmail(self) -> None:
        """REPLAY_UNSAFE_CONNECTOR_TYPES includes 'gmail'."""
        mod = _load_migration()
        assert "gmail" in mod.REPLAY_UNSAFE_CONNECTOR_TYPES


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() matches the spec requirements."""

    def test_upgrade_emits_one_statement(self) -> None:
        """upgrade() emits exactly one SQL statement."""
        sqls = _collect_execute_calls("upgrade")
        assert len(sqls) == 1, f"Expected 1 SQL statement, got {len(sqls)}"

    def test_upgrade_is_update(self) -> None:
        """upgrade() emits an UPDATE statement."""
        sqls = _collect_execute_calls("upgrade")
        assert sqls[0].strip().upper().startswith("UPDATE"), f"Expected UPDATE, got: {sqls[0][:50]}"

    def test_upgrade_targets_connector_registry(self) -> None:
        """upgrade() UPDATE targets connector_registry."""
        sqls = _collect_execute_calls("upgrade")
        assert "connector_registry" in sqls[0]

    def test_upgrade_sets_replay_safe_false(self) -> None:
        """upgrade() sets replay_safe = FALSE."""
        sqls = _collect_execute_calls("upgrade")
        upper = sqls[0].upper()
        assert "REPLAY_SAFE" in upper and "FALSE" in upper, (
            f"upgrade() must SET replay_safe = FALSE:\n{sqls[0]}"
        )

    def test_upgrade_scoped_to_gmail(self) -> None:
        """upgrade() WHERE clause includes connector_type = 'gmail'."""
        sqls = _collect_execute_calls("upgrade")
        assert "'gmail'" in sqls[0].lower() or "gmail" in sqls[0].lower(), (
            f"upgrade() must filter by connector_type = 'gmail':\n{sqls[0]}"
        )

    def test_upgrade_is_idempotent_via_where_replay_safe_true(self) -> None:
        """upgrade() WHERE clause guards with replay_safe = TRUE for idempotency."""
        sqls = _collect_execute_calls("upgrade")
        upper = sqls[0].upper()
        # Must restrict to rows still TRUE so re-running is a no-op
        assert "REPLAY_SAFE" in upper and "TRUE" in upper, (
            f"upgrade() must restrict to replay_safe = TRUE for idempotency:\n{sqls[0]}"
        )


class TestDowngradeSQLShape:
    """Verify the SQL emitted by downgrade() correctly inverts the upgrade."""

    def test_downgrade_emits_one_statement(self) -> None:
        """downgrade() emits exactly one SQL statement."""
        sqls = _collect_execute_calls("downgrade")
        assert len(sqls) == 1, f"Expected 1 SQL statement, got {len(sqls)}"

    def test_downgrade_is_update(self) -> None:
        """downgrade() emits an UPDATE statement."""
        sqls = _collect_execute_calls("downgrade")
        assert sqls[0].strip().upper().startswith("UPDATE"), f"Expected UPDATE, got: {sqls[0][:50]}"

    def test_downgrade_targets_connector_registry(self) -> None:
        """downgrade() UPDATE targets connector_registry."""
        sqls = _collect_execute_calls("downgrade")
        assert "connector_registry" in sqls[0]

    def test_downgrade_restores_replay_safe_true(self) -> None:
        """downgrade() sets replay_safe = TRUE."""
        sqls = _collect_execute_calls("downgrade")
        upper = sqls[0].upper()
        assert "REPLAY_SAFE" in upper and "TRUE" in upper, (
            f"downgrade() must SET replay_safe = TRUE:\n{sqls[0]}"
        )

    def test_downgrade_scoped_to_gmail(self) -> None:
        """downgrade() WHERE clause includes connector_type = 'gmail'."""
        sqls = _collect_execute_calls("downgrade")
        assert "gmail" in sqls[0].lower(), (
            f"downgrade() must filter by connector_type = 'gmail':\n{sqls[0]}"
        )
