"""Regression tests for the heartbeat-only QA connector-state view."""

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
    / "026_qa_connector_view_heartbeat_rows.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_026", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture_sql(module, direction: str) -> str:
    mock_op = MagicMock()
    with patch.object(module, "op", mock_op):
        getattr(module, direction)()
    mock_op.execute.assert_called_once()
    return mock_op.execute.call_args.args[0]


def test_revision_extends_switchboard_chain() -> None:
    migration = _load_migration()

    assert migration.revision == "sw_026"
    assert migration.down_revision == "sw_025"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_excludes_rows_without_a_heartbeat_instance() -> None:
    sql = _capture_sql(_load_migration(), "upgrade")

    assert "CREATE OR REPLACE VIEW public.v_qa_connector_state" in sql
    assert "instance_id IS NOT NULL" in sql
    assert "deleted_at IS NULL" in sql
    assert "archived_at IS NULL" in sql


def test_downgrade_restores_prior_view_definition() -> None:
    sql = _capture_sql(_load_migration(), "downgrade")

    assert "CREATE OR REPLACE VIEW public.v_qa_connector_state" in sql
    assert "instance_id IS NOT NULL" not in sql
    assert "deleted_at IS NULL" in sql
    assert "archived_at IS NULL" in sql
