"""core_325 (bu-opskk2): a chain that finds the lease columns takes no table lock.

``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` takes ACCESS EXCLUSIVE even when the columns exist,
and the lock lives to the end of the chain's transaction. A second per-schema chain that held it
while waiting on core_346's advisory lock deadlocked against the first chain's UPDATE of the same
table (merge_group runs 45117955888, 45103185660). No Docker needed: the module's ``op`` is faked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_325_filtered_events_replay_lease.py"
)


def _upgrade_statements(columns_exist: bool) -> list[str]:
    spec = importlib.util.spec_from_file_location("core_325_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    fake_op.get_bind.return_value.exec_driver_sql.return_value.scalar.return_value = columns_exist
    with patch.object(migration, "op", fake_op):
        migration.upgrade()
    return statements


def test_a_chain_that_finds_the_columns_issues_no_alter_table() -> None:
    statements = _upgrade_statements(columns_exist=True)
    assert not any("ALTER TABLE" in statement for statement in statements)


def test_the_first_chain_still_adds_the_columns() -> None:
    statements = _upgrade_statements(columns_exist=False)
    assert any("ADD COLUMN IF NOT EXISTS claim_token" in s for s in statements)
