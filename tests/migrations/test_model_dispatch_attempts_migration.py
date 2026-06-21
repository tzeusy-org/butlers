"""Tests for core_104 model_dispatch_attempts migration."""

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
    / "core_104_model_dispatch_attempts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_104", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_104"
    assert mod.down_revision == "core_103"


def test_upgrade_creates_model_dispatch_attempts_table():
    source = _MIGRATION_PATH.read_text()
    assert "public.model_dispatch_attempts" in source
    assert "BIGSERIAL" in source
    assert "catalog_entry_id" in source
    assert "UUID" in source
    assert "NOT NULL" in source
    assert "ts" in source
    assert "TIMESTAMPTZ" in source
    assert "butler" in source
    assert "outcome" in source
    assert "attempt_index" in source
    assert "logical_session_id" in source
    assert "tool_call_count" in source
    assert "failure_reason" in source


def test_upgrade_creates_indexes():
    source = _MIGRATION_PATH.read_text()
    assert "idx_model_dispatch_attempts_catalog_ts" in source
    assert "idx_model_dispatch_attempts_session" in source
    assert "idx_model_dispatch_attempts_logical_session" in source


def test_upgrade_has_fk_to_model_catalog():
    source = _MIGRATION_PATH.read_text()
    assert "public.model_catalog" in source
    assert "ON DELETE CASCADE" in source


def test_downgrade_drops_table_and_indexes():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS public.model_dispatch_attempts" in source
    assert "DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_catalog_ts" in source
    assert "DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_session" in source
    assert "DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_logical_session" in source


def test_upgrade_grants_to_runtime_roles():
    source = _MIGRATION_PATH.read_text()
    assert "butler_general_rw" in source
    assert "connector_writer" in source
    assert "SELECT, INSERT" in source
