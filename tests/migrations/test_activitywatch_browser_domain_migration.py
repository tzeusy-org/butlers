"""Tests for core_192's safe ActivityWatch browser-domain evidence column."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_192_activitywatch_browser_domain.py"
)


def _collect_sql(method_name: str) -> list[str]:
    migration = load_migration(_MIGRATION_PATH, "core_192")
    calls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = calls.append
    with patch.object(migration, "op", mock_op):
        getattr(migration, method_name)()
    return calls


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
