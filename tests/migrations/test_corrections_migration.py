"""Tests for corrections table in consolidated core foundation migration (core_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_001_foundation.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_001_foundation", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"

    def test_revision_metadata(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_001"
        assert mod.down_revision is None


class TestCorrectionsSchema:
    def _src(self) -> str:
        return inspect.getsource(_load_migration()._create_core_tables)

    def test_creates_corrections_table(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS corrections" in src
        for col in (
            "correction_type",
            "target_session_id",
            "correcting_session_id",
            "description",
            "status",
            "summary",
            "original_data_snapshot",
            "correction_details",
            "created_at",
        ):
            assert col in src

    def test_expected_indexes_present(self) -> None:
        src = self._src()
        assert "idx_corrections_target_session_id" in src
        assert "idx_corrections_correcting_session_id_created_at" in src
        assert "idx_corrections_target_session_id_created_at" in src
        assert "idx_corrections_correction_type" in src


class TestDowngrade:
    def test_drops_corrections_table_and_indexes(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP INDEX IF EXISTS idx_corrections_correction_type" in src
        assert "DROP TABLE IF EXISTS corrections" in src
