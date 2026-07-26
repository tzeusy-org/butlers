"""Tests for core_192's safe ActivityWatch browser-domain evidence column."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_192_activitywatch_browser_domain.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_192", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_sql(method_name: str) -> list[str]:
    migration = _load_migration()
    calls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = calls.append
    with patch.object(migration, "op", mock_op):
        getattr(migration, method_name)()
    return calls


def test_revision_chain() -> None:
    migration = _load_migration()
    assert migration.revision == "core_192"
    assert migration.down_revision == "core_191"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_adds_only_safe_browser_domain_column() -> None:
    sql = "\n".join(_collect_sql("upgrade"))
    assert "ALTER TABLE IF EXISTS connectors.activitywatch_events" in sql
    assert "ADD COLUMN IF NOT EXISTS browser_domain TEXT" in sql
    assert "raw URLs" in sql
    assert "raw_payload" in sql


def test_downgrade_removes_browser_domain_column() -> None:
    sql = "\n".join(_collect_sql("downgrade"))
    assert "ALTER TABLE IF EXISTS connectors.activitywatch_events" in sql
    assert "DROP COLUMN IF EXISTS browser_domain" in sql
