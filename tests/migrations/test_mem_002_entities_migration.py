"""Tests for current mem_002 seed predicates migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "002_seed_predicates.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_002_seed_predicates", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "mem_002"
        assert mod.down_revision == "mem_001"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQL:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.upgrade) + "\n" + inspect.getsource(mod._insert_predicate)

    def test_inserts_into_predicate_registry(self) -> None:
        src = self._src()
        assert "INSERT INTO predicate_registry" in src
        assert "ON CONFLICT (name) DO NOTHING" in src

    def test_contains_cross_domain_predicates(self) -> None:
        src = self._src()
        for name in (
            "measurement_weight",
            "transaction_debit",
            "interaction",
            "preference",
        ):
            assert name in src


class TestDowngradeSQL:
    def test_downgrade_removes_seeded_names(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DELETE FROM predicate_registry" in src
