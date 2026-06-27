"""Tests for the mailbox module migration chain (actioned_at column)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "butlers" / "modules" / "mailbox" / "migrations"
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


def test_initial_mailbox_migration_creates_actioned_at_column() -> None:
    mod = _load_migration("001_create_mailbox_table.py")
    sqls = _collect_sqls(mod)

    create_sql = next(sql for sql in sqls if "CREATE TABLE IF NOT EXISTS mailbox" in sql)
    normalized = " ".join(create_sql.lower().split())

    assert normalized.count("actioned_at timestamptz") == 1


def test_mailbox_repair_migration_adds_missing_actioned_at_column() -> None:
    mod = _load_migration("002_add_actioned_at.py")
    sqls = _collect_sqls(mod)
    normalized = " ".join(sqls[0].lower().split())

    assert mod.revision == "mailbox_002"
    assert mod.down_revision == "mailbox_001"
    assert "alter table mailbox" in normalized
    assert "add column if not exists actioned_at timestamptz" in normalized


def test_mailbox_repair_migration_downgrade_is_noop() -> None:
    mod = _load_migration("002_add_actioned_at.py")

    assert _collect_sqls(mod, "downgrade") == []
