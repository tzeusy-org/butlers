"""Unit tests for consolidated switchboard routing migration (sw_003)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "switchboard" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "003_switchboard_routing.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_003_switchboard_routing", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "sw_003"
        assert mod.down_revision == "sw_002"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_ingestion_rules_table_with_constraints(self) -> None:
        src = self._src()
        assert "CREATE TABLE ingestion_rules" in src
        assert "ingestion_rules_scope_check" in src
        assert "ingestion_rules_connector_action_check" in src
        assert "ingestion_rules_priority_check" in src

    def test_creates_ingestion_rules_indexes(self) -> None:
        src = self._src()
        assert "ix_ingestion_rules_scope_active" in src
        assert "ix_ingestion_rules_global_active" in src

    def test_migrates_triage_rules_into_ingestion_rules(self) -> None:
        src = self._src()
        assert "INSERT INTO ingestion_rules" in src
        assert "FROM triage_rules" in src
        assert "'global'" in src

    def test_creates_source_filter_tables(self) -> None:
        src = self._src()
        assert "CREATE TABLE source_filters" in src
        assert "CREATE TABLE connector_source_filters" in src


class TestDowngradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().downgrade)

    def test_drops_ingestion_rules_indexes_then_table(self) -> None:
        src = self._src()
        idx_scope = src.index("DROP INDEX IF EXISTS ix_ingestion_rules_scope_active")
        idx_global = src.index("DROP INDEX IF EXISTS ix_ingestion_rules_global_active")
        table = src.index("DROP TABLE IF EXISTS ingestion_rules")
        assert idx_scope < table
        assert idx_global < table

    def test_drops_source_filter_tables(self) -> None:
        src = self._src()
        assert "DROP TABLE IF EXISTS connector_source_filters" in src
        assert "DROP TABLE IF EXISTS source_filters" in src
