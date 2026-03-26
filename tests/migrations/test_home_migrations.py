"""Tests for consolidated home migration (home_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
HOME_MIGRATIONS_DIR = ROSTER_DIR / "home" / "migrations"
MIGRATION_FILE = HOME_MIGRATIONS_DIR / "001_home_tables.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("home_001_tables", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "home_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("home",)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_maintenance_items_table(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS maintenance_items" in src
        assert "interval_days" in src
        assert "ix_maintenance_items_next_due_at" in src

    def test_creates_home_assistant_tables(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS ha_entity_snapshot" in src
        assert "CREATE TABLE IF NOT EXISTS ha_command_log" in src

    def test_seeds_ha_state_predicate(self) -> None:
        src = self._src()
        assert "INSERT INTO predicate_registry" in src
        assert "ha_state" in src

    def test_module_declares_threshold_seed_constants(self) -> None:
        mod = _load_migration()
        assert hasattr(mod, "_THRESHOLD_SEEDS")
        assert len(mod._THRESHOLD_SEEDS) == 5


class TestDowngradeSQL:
    def test_drops_home_tables(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS maintenance_items" in src
        assert "DROP TABLE IF EXISTS ha_command_log" in src
        assert "DROP TABLE IF EXISTS ha_entity_snapshot" in src
