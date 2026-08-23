"""Tests for core_167 model_catalog last_verified_error migration."""

from __future__ import annotations

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


def test_upgrade_adds_last_verified_error_column():
    source = _MIGRATION_PATH.read_text()
    assert "ALTER TABLE public.model_catalog" in source
    assert "ADD COLUMN IF NOT EXISTS last_verified_error TEXT" in source


def test_downgrade_drops_last_verified_error_column():
    source = _MIGRATION_PATH.read_text()
    assert "DROP COLUMN IF EXISTS last_verified_error" in source
