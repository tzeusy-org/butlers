"""Regression coverage for the QA connector-state view migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "roster"
        / "switchboard"
        / "migrations"
        / "028_qa_connector_state_checkpoint_rows.py"
    )
    spec = importlib.util.spec_from_file_location("sw_028_checkpoint_rows", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_excludes_checkpoint_only_rows(monkeypatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert len(statements) == 1
    statement = statements[0]
    assert "CREATE OR REPLACE VIEW public.v_qa_connector_state" in statement
    assert "instance_id IS NULL" in statement
    assert "last_heartbeat_at IS NULL" in statement
    assert "checkpoint_cursor IS NOT NULL" in statement
    assert "DROP VIEW" not in statement


def test_downgrade_restores_previous_view(monkeypatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()

    assert len(statements) == 1
    assert "CREATE OR REPLACE VIEW public.v_qa_connector_state" in statements[0]
    assert "instance_id" not in statements[0]
    assert "checkpoint_cursor" not in statements[0]
