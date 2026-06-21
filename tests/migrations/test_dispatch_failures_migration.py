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


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_099"
    assert mod.down_revision == "core_098"


def test_upgrade_creates_dispatch_failures_schema():
    """Table columns, FK to model_catalog (CASCADE), and lookup index are all present."""
    source = _MIGRATION_PATH.read_text()
    for needle in (
        "public.dispatch_failures",
        "catalog_entry_id",
        "ts",
        "error_code",
        "error_message",
        "butler",
        "session_id",
        # FK to model_catalog with cascade
        "public.model_catalog",
        "ON DELETE CASCADE",
        # lookup index
        "idx_dispatch_failures_catalog_entry_ts",
    ):
        assert needle in source, f"Missing schema object: {needle}"


def test_downgrade_drops_table_and_index():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS public.dispatch_failures" in source
    assert "DROP INDEX IF EXISTS public.idx_dispatch_failures_catalog_entry_ts" in source
