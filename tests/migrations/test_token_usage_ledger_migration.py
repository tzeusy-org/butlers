"""Tests for consolidated model/token migration (core_004)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_004_model_and_tokens.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_004_model_and_tokens", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_004"
        assert mod.down_revision == "core_003"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestTokenUsageLedgerDDL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_token_usage_ledger_partitioned(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS public.token_usage_ledger" in src
        assert "PARTITION BY RANGE (recorded_at)" in src
        assert "catalog_entry_id UUID NOT NULL" in src

    def test_creates_ledger_index(self) -> None:
        src = self._src()
        assert "CREATE INDEX IF NOT EXISTS idx_ledger_entry_time" in src

    def test_creates_token_limits(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS public.token_limits" in src
        assert "catalog_entry_id UUID NOT NULL UNIQUE" in src
        assert "limit_24h" in src
        assert "limit_30d" in src

    def test_handles_pg_partman_and_fallback_paths(self) -> None:
        src = self._src()
        assert "if _pg_partman_available()" in src
        assert "partman.create_parent" in src
        assert "_FALLBACK_PARTITION_COUNT" in src


class TestDowngrade:
    def test_drops_ledger_and_limits(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP TABLE IF EXISTS public.token_limits" in src
        assert "DROP TABLE IF EXISTS public.token_usage_ledger CASCADE" in src
