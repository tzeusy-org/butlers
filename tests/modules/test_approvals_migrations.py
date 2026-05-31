"""Tests for approvals module migration schema drift repair."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "butlers" / "modules" / "approvals" / "migrations"
)


def _load_migration(filename: str):
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_sqls(mod, fn_name: str = "upgrade") -> list[str]:
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return sqls


def test_initial_approvals_migration_creates_pending_actions_dossier_columns() -> None:
    mod = _load_migration("001_approvals_tables.py")
    sqls = _collect_sqls(mod)

    pending_actions_sql = next(
        sql for sql in sqls if "CREATE TABLE IF NOT EXISTS pending_actions" in sql
    )
    normalized = " ".join(pending_actions_sql.lower().split())

    assert normalized.count("why text") == 1
    assert normalized.count("evidence jsonb not null default '[]'::jsonb") == 1


def test_approvals_repair_migration_adds_missing_dossier_columns() -> None:
    mod = _load_migration("002_pending_actions_why_evidence.py")
    sqls = _collect_sqls(mod)
    normalized = " ".join(sqls[0].lower().split())

    assert mod.revision == "approvals_002"
    assert mod.down_revision == "approvals_001"
    assert "alter table pending_actions" in normalized
    assert "add column if not exists why text" in normalized
    assert "add column if not exists evidence jsonb not null default '[]'::jsonb" in normalized


def test_approvals_repair_migration_downgrade_is_noop() -> None:
    mod = _load_migration("002_pending_actions_why_evidence.py")

    assert _collect_sqls(mod, "downgrade") == []
