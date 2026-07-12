"""Tests for core_167 model_catalog last_verified_error migration."""

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
    / "core_167_model_catalog_verify_error.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_167", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_167"
    assert mod.down_revision == "core_166"


def test_upgrade_adds_last_verified_error_column():
    source = _MIGRATION_PATH.read_text()
    assert "ALTER TABLE public.model_catalog" in source
    assert "ADD COLUMN IF NOT EXISTS last_verified_error TEXT" in source


def test_downgrade_drops_last_verified_error_column():
    source = _MIGRATION_PATH.read_text()
    assert "DROP COLUMN IF EXISTS last_verified_error" in source
