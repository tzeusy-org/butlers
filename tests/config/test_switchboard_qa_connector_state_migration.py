"""Regression coverage for the QA connector-state view migration."""

from __future__ import annotations

from pathlib import Path

from butlers.testing.migration import load_migration

_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "028_qa_connector_state_checkpoint_rows.py"
)


def test_upgrade_excludes_checkpoint_only_rows(monkeypatch) -> None:
    migration = load_migration(_MIGRATION_PATH, "sw_028_checkpoint_rows")
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
    migration = load_migration(_MIGRATION_PATH, "sw_028_checkpoint_rows")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()

    assert len(statements) == 1
    assert "CREATE OR REPLACE VIEW public.v_qa_connector_state" in statements[0]
    assert "instance_id" not in statements[0]
    assert "checkpoint_cursor" not in statements[0]
