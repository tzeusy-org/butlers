"""Static regression coverage for the durable calendar force-sync queue migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_194_calendar_force_sync_queue.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_194_calendar_force_sync_queue", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed_sql(function_name: str) -> str:
    module = _load_migration()
    op = MagicMock()
    with patch.object(module, "op", op):
        getattr(module, function_name)()
    return "\n".join(str(call.args[0]) for call in op.execute.call_args_list)


def test_revision_chain_and_calendar_schema_guard() -> None:
    module = _load_migration()

    assert module.revision == "core_194"
    assert module.down_revision == "core_193"
    assert module.branch_labels is None
    assert module.depends_on is None

    sql = _executed_sql("upgrade")
    assert "to_regclass('calendar_action_log') IS NULL" in sql
    assert "'pending', 'running', 'applied', 'failed', 'noop'" in sql
    assert "ix_calendar_action_log_force_sync_pending" in sql
    assert "ix_calendar_action_log_force_sync_running" in sql
    assert "action_type = 'calendar_force_sync' AND action_status = 'pending'" in sql


def test_downgrade_requeues_interrupted_commands_before_restoring_old_constraint() -> None:
    sql = _executed_sql("downgrade")

    assert "SET action_status = 'pending'" in sql
    assert "WHERE action_status = 'running'" in sql
    assert "DROP INDEX IF EXISTS ix_calendar_action_log_force_sync_pending" in sql
    assert "DROP INDEX IF EXISTS ix_calendar_action_log_force_sync_running" in sql
    assert "'pending', 'applied', 'failed', 'noop'" in sql
