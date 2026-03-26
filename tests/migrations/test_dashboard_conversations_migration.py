"""Tests for consolidated dashboard migration (core_006)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_006_dashboard.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_006_dashboard", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRevisionMetadata:
    def test_file_exists(self) -> None:
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"

    def test_revision_ids(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_006"
        assert mod.down_revision == "core_005"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQL:
    def _src(self) -> str:
        return inspect.getsource(_load_migration().upgrade)

    def test_creates_dashboard_conversations_and_messages(self) -> None:
        src = self._src()
        assert "CREATE TABLE IF NOT EXISTS public.dashboard_conversations" in src
        assert "CREATE TABLE IF NOT EXISTS public.dashboard_messages" in src

    def test_conversations_columns(self) -> None:
        src = self._src()
        for col in (
            "butler_name",
            "title",
            "status",
            "message_count",
            "total_input_tokens",
            "total_output_tokens",
            "total_duration_ms",
        ):
            assert col in src

    def test_messages_fk_and_jsonb(self) -> None:
        src = self._src()
        assert "REFERENCES public.dashboard_conversations(id)" in src
        assert "ON DELETE CASCADE" in src
        assert "tool_calls JSONB" in src

    def test_composite_indexes_present(self) -> None:
        src = self._src()
        assert "idx_dashboard_conversations_butler_status_updated" in src
        assert "idx_dashboard_conversations_butler_updated" in src
        assert "idx_dashboard_messages_conversation_created" in src


class TestDowngradeSQL:
    def test_drops_indexes_and_tables(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DROP INDEX IF EXISTS public.idx_dashboard_messages_conversation_created" in src
        assert "DROP TABLE IF EXISTS public.dashboard_messages" in src
        assert "DROP TABLE IF EXISTS public.dashboard_conversations" in src
