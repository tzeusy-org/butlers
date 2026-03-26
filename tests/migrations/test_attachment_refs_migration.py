"""Tests for consolidated switchboard email migration (sw_004)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "switchboard" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "004_switchboard_email.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_004_switchboard_email", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_ids(self) -> None:
        mod = _load_migration()
        assert mod.revision == "sw_004"
        assert mod.down_revision == "sw_003"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestAttachmentRefsAndBackfillSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_attachment_refs_table(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS attachment_refs" in src
        assert "PRIMARY KEY (message_id, attachment_id)" in src
        assert "fetched BOOLEAN NOT NULL DEFAULT FALSE" in src
        assert "blob_ref TEXT NULL" in src

    def test_creates_attachment_refs_indexes(self) -> None:
        src = self._src()
        assert "ix_attachment_refs_fetched_created_at" in src
        assert "ix_attachment_refs_media_type_created_at" in src

    def test_creates_backfill_jobs_table(self) -> None:
        src = self._src()
        assert "CREATE TABLE backfill_jobs" in src
        assert "backfill_jobs_status_check" in src
        assert "idx_backfill_jobs_status" in src
        assert "idx_backfill_jobs_connector" in src

    def test_creates_prune_function(self) -> None:
        src = self._src()
        assert "CREATE OR REPLACE FUNCTION switchboard_prune_email_metadata_refs" in src
        assert "INTERVAL '90 days'" in src
        assert "DELETE FROM email_metadata_refs" in src


class TestDowngradeSQL:
    def test_drops_prune_function_and_tables(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP FUNCTION IF EXISTS switchboard_prune_email_metadata_refs" in src
        assert "DROP TABLE IF EXISTS backfill_jobs" in src
        assert "DROP TABLE IF EXISTS attachment_refs" in src
        assert "DROP TABLE IF EXISTS email_metadata_refs" in src
