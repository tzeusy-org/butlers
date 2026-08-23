"""Tests for core_073 model-catalog timeout and runtime_config reduction migration."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_073_model_catalog_session_timeout.py"
)


def test_upgrade_sql_moves_timeout_to_model_catalog_and_reduces_runtime_config():
    source = _MIGRATION_PATH.read_text()
    assert "ALTER TABLE public.model_catalog" in source
    assert "ADD COLUMN IF NOT EXISTS session_timeout_s INT NOT NULL DEFAULT 1800" in source
    assert "ALTER TABLE IF EXISTS runtime_config" in source
    assert "DROP COLUMN IF EXISTS model" in source
    assert "DROP COLUMN IF EXISTS runtime_type" in source
    assert "DROP COLUMN IF EXISTS args" in source
    assert "DROP COLUMN IF EXISTS session_timeout_s" in source


def test_downgrade_sql_restores_runtime_config_legacy_columns():
    source = _MIGRATION_PATH.read_text()
    assert "ADD COLUMN IF NOT EXISTS model TEXT" in source
    assert "ADD COLUMN IF NOT EXISTS runtime_type TEXT NOT NULL DEFAULT 'codex'" in source
    assert "ADD COLUMN IF NOT EXISTS args JSONB NOT NULL DEFAULT '[]'::jsonb" in source
    assert "ADD COLUMN IF NOT EXISTS session_timeout_s INT NOT NULL DEFAULT 900" in source
    assert "DROP COLUMN IF EXISTS session_timeout_s" in source
