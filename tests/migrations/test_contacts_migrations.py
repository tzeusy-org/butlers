"""Tests for consolidated contacts sync migration (contacts_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "contacts" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_contacts_sync.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("contacts_001_sync", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "contacts_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("contacts",)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_contacts_source_accounts(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS contacts_source_accounts" in src
        for col in ("provider", "account_id", "subject_email", "connected_at", "last_success_at"):
            assert col in src

    def test_creates_contacts_sync_state(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS contacts_sync_state" in src
        for col in (
            "sync_cursor",
            "cursor_issued_at",
            "last_full_sync_at",
            "last_incremental_sync_at",
        ):
            assert col in src

    def test_creates_contacts_source_links_and_fk_guard(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS contacts_source_links" in src
        assert "contacts_source_links_local_contact_id_fkey" in src
        assert "to_regclass(format('%I.contacts', current_schema()))" in src


class TestDowngradeSQL:
    def test_drops_tables_and_indexes(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS contacts_source_links" in src
        assert "DROP TABLE IF EXISTS contacts_sync_state" in src
        assert "DROP TABLE IF EXISTS contacts_source_accounts" in src
