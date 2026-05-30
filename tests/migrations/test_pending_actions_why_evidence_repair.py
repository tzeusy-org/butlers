"""Tests for pending_actions why/evidence schema repair."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_105_repair_pending_actions_why_evidence.py"
)
_APPROVALS_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "modules"
    / "approvals"
    / "migrations"
    / "001_approvals_tables.py"
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def execute(self, _sql):
        return _Rows([("relationship",)])


def _load_core_migration():
    spec = importlib.util.spec_from_file_location("core_105", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_core_repair_migration_revision_chain():
    mod = _load_core_migration()
    assert mod.revision == "core_105"
    assert mod.down_revision == "core_104"


def test_core_repair_migration_adds_pending_action_columns():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "pending_actions" in source
    assert "ADD COLUMN IF NOT EXISTS why TEXT" in source
    assert "ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb" in source
    assert "n.nspname NOT IN ('pg_catalog', 'information_schema')" in source
    assert "DROP COLUMN" not in source


def test_core_repair_migration_upgrade_repairs_discovered_schema(monkeypatch):
    mod = _load_core_migration()
    executed_sql = []
    monkeypatch.setattr(mod.op, "get_bind", lambda: _Connection())
    monkeypatch.setattr(mod.op, "execute", lambda sql: executed_sql.append(sql))

    mod.upgrade()

    assert len(executed_sql) == 1
    assert 'ALTER TABLE "relationship".pending_actions' in executed_sql[0]
    assert "ADD COLUMN IF NOT EXISTS why TEXT" in executed_sql[0]
    assert "ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb" in executed_sql[0]


def test_core_repair_migration_downgrade_is_noop(monkeypatch):
    mod = _load_core_migration()
    executed_sql = []
    monkeypatch.setattr(mod.op, "execute", lambda sql: executed_sql.append(sql))

    mod.downgrade()

    assert executed_sql == []


def test_approvals_base_table_creates_pending_action_columns():
    source = _APPROVALS_MIGRATION_PATH.read_text()
    assert "why TEXT" in source
    assert "evidence JSONB NOT NULL DEFAULT '[]'::jsonb" in source
