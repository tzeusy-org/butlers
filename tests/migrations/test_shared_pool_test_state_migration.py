"""Tests for core_117 shared-pool test-state columns migration (bu-urcwx).

Covers:
- Revision chain integrity (core_117 → core_116)
- All four test-state columns added to public.butler_secrets
- Idempotent DDL (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
- Downgrade drops all four columns with IF EXISTS
- ensure_secrets_schema DDL parity: fresh databases get the same columns
  without running the alembic chain
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from butlers.credential_store import _SECRETS_TABLE_DDL, _SECRETS_TEST_STATE_DDL

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_117_shared_pool_test_state_columns.py"
)

_TEST_STATE_COLUMNS = (
    "last_verified",
    "last_test_ok",
    "last_test_code",
    "last_test_message",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_117", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_117"
    assert mod.down_revision == "core_116"


def test_migration_targets_public_butler_secrets():
    source = _MIGRATION_PATH.read_text()
    assert "public.butler_secrets" in source


def test_migration_ddl_is_idempotent():
    source = _MIGRATION_PATH.read_text()
    assert "CREATE TABLE IF NOT EXISTS" in source
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "DROP COLUMN IF EXISTS" in source


def test_migration_column_set_matches_core_106():
    """core_117 must apply the exact column set core_106 gave per-butler schemas."""
    mod = _load_migration()
    assert tuple(col for col, _ in mod._TEST_STATE_COLUMNS) == _TEST_STATE_COLUMNS


def test_ensure_secrets_schema_ddl_includes_test_state_columns():
    """Fresh DBs created by ensure_secrets_schema must match the migrated shape:
    the CREATE DDL carries the columns, and the convergence ALTER adds them to
    pre-existing tables (idempotently)."""
    for col in _TEST_STATE_COLUMNS:
        assert col in _SECRETS_TABLE_DDL, f"'{col}' missing from _SECRETS_TABLE_DDL"
        assert col in _SECRETS_TEST_STATE_DDL, f"'{col}' missing from _SECRETS_TEST_STATE_DDL"
    assert "ADD COLUMN IF NOT EXISTS" in _SECRETS_TEST_STATE_DDL
