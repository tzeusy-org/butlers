"""Tests for core_061 runtime_config migration.

Verifies the historical creation migration still matches the original table
shape before later reduction migrations.
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
    / "core_061_runtime_config.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("core_061", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    """core_061 revises core_060."""
    mod = _load_migration()
    assert mod.revision == "core_061"
    assert mod.down_revision == "core_060"


def test_migration_file_exists():
    """The migration file exists at the expected path."""
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


def test_migration_sql_contains_expected_columns():
    """The historical upgrade SQL includes the original runtime_config columns."""
    source = _MIGRATION_PATH.read_text()
    expected_columns = [
        "butler_name      TEXT PRIMARY KEY",
        "core_groups      TEXT[]",
        "model            TEXT",
        "runtime_type     TEXT NOT NULL DEFAULT 'codex'",
        "args             JSONB NOT NULL DEFAULT '[]'::jsonb",
        "max_concurrent   INT NOT NULL DEFAULT 3",
        "max_queued       INT NOT NULL DEFAULT 10",
        "session_timeout_s INT NOT NULL DEFAULT 900",
        "seeded_at        TIMESTAMPTZ NOT NULL DEFAULT now()",
        "updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()",
    ]
    for col in expected_columns:
        assert col in source, f"Missing column definition: {col}"


def test_migration_downgrade_drops_table():
    """The downgrade SQL drops the runtime_config table."""
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS runtime_config" in source
