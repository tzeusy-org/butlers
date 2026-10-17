"""Smoke tests for core_089_butler_logs migration.

Verifies:
- The migration file exists at the expected path.
- Revision chain is correct (core_089 revises core_088).
- The upgrade SQL contains the expected table shape.
- Indexes are included in the upgrade DDL.
- Downgrade SQL drops the table.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_089_butler_logs.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_089", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_sql_contains_expected_columns():
    source = _MIGRATION_PATH.read_text()
    expected_fragments = [
        "id         BIGSERIAL PRIMARY KEY",
        "ts         TIMESTAMPTZ NOT NULL DEFAULT now()",
        "level      VARCHAR NOT NULL",
        "CHECK (level IN ('DEBUG','INFO','WARN','ERROR'))",
        "msg        TEXT NOT NULL",
        "source     VARCHAR",
        "request_id UUID",
        "metadata   JSONB",
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    ]
    for fragment in expected_fragments:
        assert fragment in source, f"Missing DDL fragment: {fragment!r}"


def test_migration_sql_contains_indexes():
    source = _MIGRATION_PATH.read_text()
    assert "butler_logs_ts" in source
    assert "butler_logs_level" in source


def test_migration_downgrade_drops_table():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS" in source
    assert "butler_logs" in source


def test_migration_covers_all_butler_schemas():
    mod = _load_migration()
    expected_schemas = {
        "education",
        "finance",
        "general",
        "health",
        "home",
        "lifestyle",
        "messenger",
        "qa",
        "relationship",
        "switchboard",
        "travel",
    }
    assert set(mod._BUTLER_SCHEMAS) == expected_schemas


def test_forward_log_repair_uses_only_configured_own_target(monkeypatch) -> None:
    """Actual new producer scopes SQL; this recorder is not PostgreSQL evidence."""
    # REQ-core-telemetry-016
    from types import SimpleNamespace
    from unittest.mock import Mock

    path = _MIGRATION_PATH.with_name("core_266_owned_butler_logs.py")
    spec = importlib.util.spec_from_file_location("core_266", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "core_265"
    config = Mock()
    monkeypatch.setattr(mod.op, "get_context", lambda: SimpleNamespace(config=config))
    sql = []
    monkeypatch.setattr(mod.op, "execute", sql.append)
    for target in (None, "", "public"):
        config.get_main_option.return_value = target
        mod.upgrade()
        assert sql == []
    config.get_main_option.return_value = "concierge"
    mod.upgrade()
    assert len(sql) == 6
    assert 'CREATE TABLE IF NOT EXISTS "concierge".butler_logs' in sql[1]
    assert "pg_advisory_xact_lock" in sql[0]
    assert "Incompatible owning structured-log columns" in sql[2]
    assert "Incompatible owning structured-log indexes" in sql[-1]
    assert not any("public.butler_logs" in statement for statement in sql)
    before = list(sql)
    mod.downgrade()
    assert sql == before
    config.get_main_option.return_value = "concierge; SELECT 1"
    with pytest.raises(ValueError, match="Invalid structured-log owning"):
        mod.upgrade()
    assert sql == before
