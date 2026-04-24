"""Tests for core_078 google_accounts metadata + last_token_refresh_at migration.

Unit tests verify the migration file structure, revision chain, SQL content,
and idempotency contract. Full chain integrity is covered by
tests/config/test_migration_contract.py::test_all_migration_chains_integrity,
which picks up this migration automatically.

bu-k5l35.1.4 — part of the Google Health enablement layer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_078_google_accounts_metadata_token_refresh.py"
)


def _load_core_migration():
    spec = importlib.util.spec_from_file_location("core_078", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File structure and revision chain
# ---------------------------------------------------------------------------


def test_core_migration_file_exists():
    assert _CORE_MIGRATION_PATH.exists(), f"Migration file not found: {_CORE_MIGRATION_PATH}"


def test_core_migration_revision_chain():
    mod = _load_core_migration()
    assert mod.revision == "core_078"
    assert mod.down_revision == "core_077"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_core_migration_has_upgrade_and_downgrade():
    mod = _load_core_migration()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# SQL content
# ---------------------------------------------------------------------------


def test_upgrade_adds_metadata_column_idempotently():
    """metadata JSONB column is added with IF NOT EXISTS (idempotent)."""
    source = _CORE_MIGRATION_PATH.read_text()
    assert "metadata JSONB" in source
    assert "ADD COLUMN IF NOT EXISTS metadata" in source
    assert "'{}'::jsonb" in source


def test_upgrade_adds_last_token_refresh_at_column_idempotently():
    """last_token_refresh_at TIMESTAMPTZ column is added with IF NOT EXISTS."""
    source = _CORE_MIGRATION_PATH.read_text()
    assert "last_token_refresh_at TIMESTAMPTZ" in source
    assert "ADD COLUMN IF NOT EXISTS last_token_refresh_at" in source


def test_upgrade_enforces_metadata_not_null():
    """metadata column is enforced NOT NULL after backfilling legacy NULL rows."""
    source = _CORE_MIGRATION_PATH.read_text()
    # The spec calls for NOT NULL DEFAULT '{}'::jsonb semantics.
    assert "ALTER COLUMN metadata SET NOT NULL" in source
    # Backfill NULL rows first so the NOT NULL constraint never fails.
    assert "WHERE metadata IS NULL" in source


def test_upgrade_is_guarded_on_table_existence():
    """Migration is a no-op when public.google_accounts does not yet exist."""
    source = _CORE_MIGRATION_PATH.read_text()
    assert "to_regclass('public.google_accounts')" in source


def test_downgrade_drops_both_columns_idempotently():
    """downgrade() drops the two new columns with IF EXISTS (idempotent)."""
    source = _CORE_MIGRATION_PATH.read_text()
    assert "DROP COLUMN IF EXISTS last_token_refresh_at" in source
    assert "DROP COLUMN IF EXISTS metadata" in source


def test_migration_targets_public_schema():
    """Columns are added to public.google_accounts, not a butler schema."""
    source = _CORE_MIGRATION_PATH.read_text()
    assert "public.google_accounts" in source


def test_migration_does_not_drop_or_rename_existing_columns():
    """Strictly additive: no DROP COLUMN or RENAME COLUMN in upgrade()."""
    mod = _load_core_migration()

    # Introspect only the upgrade() source for destructive keywords — the
    # downgrade() legitimately uses DROP COLUMN.
    import inspect

    upgrade_src = inspect.getsource(mod.upgrade)
    assert "DROP COLUMN" not in upgrade_src
    assert "RENAME COLUMN" not in upgrade_src
