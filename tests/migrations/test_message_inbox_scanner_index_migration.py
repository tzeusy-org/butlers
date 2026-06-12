"""Tests for sw_014 message_inbox scanner recovery index migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "014_message_inbox_scanner_processing_index.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_014", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_execute_calls(fn_name: str) -> list[str]:
    mod = _load_migration()
    calls_collected: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return calls_collected


def test_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "sw_014"
    assert mod.down_revision == "sw_013"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_upgrade_adds_processing_updated_at_partial_index() -> None:
    sqls = _collect_execute_calls("upgrade")
    assert len(sqls) == 1
    sql = sqls[0].lower()
    assert "create index if not exists ix_message_inbox_processing_updated_at" in sql
    assert "on message_inbox" in sql
    assert "updated_at" in sql
    assert "received_at" in sql
    assert "where lifecycle_state = 'processing'" in sql


def test_downgrade_drops_processing_updated_at_index() -> None:
    sqls = _collect_execute_calls("downgrade")
    assert len(sqls) == 1
    assert "drop index if exists ix_message_inbox_processing_updated_at" in sqls[0].lower()
