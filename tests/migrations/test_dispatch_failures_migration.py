"""Tests for core_099 dispatch_failures migration."""

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
    / "core_099_dispatch_failures.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_099", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_099"
    assert mod.down_revision == "core_098"


def test_upgrade_creates_dispatch_failures_table():
    source = _MIGRATION_PATH.read_text()
    assert "public.dispatch_failures" in source
    assert "BIGSERIAL" in source
    assert "catalog_entry_id" in source
    assert "UUID" in source
    assert "NOT NULL" in source
    assert "ts" in source
    assert "TIMESTAMPTZ" in source
    assert "error_code" in source
    assert "error_message" in source
    assert "butler" in source
    assert "session_id" in source


def test_upgrade_creates_index():
    source = _MIGRATION_PATH.read_text()
    assert "idx_dispatch_failures_catalog_entry_ts" in source


def test_upgrade_has_fk_to_model_catalog():
    source = _MIGRATION_PATH.read_text()
    assert "public.model_catalog" in source
    assert "ON DELETE CASCADE" in source


def test_downgrade_drops_table_and_index():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS public.dispatch_failures" in source
    assert "DROP INDEX IF EXISTS public.idx_dispatch_failures_catalog_entry_ts" in source
