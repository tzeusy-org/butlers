"""Static contract checks for the Google Health cursor archival migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "roster"
        / "switchboard"
        / "migrations"
        / "026_archive_google_health_cursor_rows.py"
    )
    spec = importlib.util.spec_from_file_location("sw_026_google_health_cursor_archive", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_archives_google_health_resource_cursor_shape(monkeypatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.revision == "sw_026"
    assert migration.down_revision == "sw_025"
    assert len(statements) == 1
    statement = statements[0]
    assert "connector_type = 'google_health'" in statement
    assert "archived_at = now()" in statement
    assert "[[:xdigit:]]{8}" in statement
    assert "spo2" in statement


def test_downgrade_does_not_unarchive_operator_state(monkeypatch) -> None:
    migration = _load_migration()
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)

    assert migration.downgrade() is None
    execute.assert_not_called()
