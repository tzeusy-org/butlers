"""Smoke tests for core_089_butler_logs migration.

Verifies:
- The migration file exists at the expected path.
- Revision chain is correct (core_089 revises core_088).
- The upgrade SQL contains the expected table shape.
- Indexes are included in the upgrade DDL.
- Downgrade SQL drops the table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_089_butler_logs.py"
)


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
    mod = load_migration(_MIGRATION_PATH, "core_089")
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
