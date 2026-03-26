"""Unit tests for consolidated ingestion-events and memory-catalog core migrations."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CORE_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
CORE_001 = CORE_DIR / "core_001_foundation.py"
CORE_009 = CORE_DIR / "core_009_memory_catalog.py"


def _load(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCore001IngestionEvents:
    def test_revision_metadata(self) -> None:
        mod = _load(CORE_001, "core_001")
        assert mod.revision == "core_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("core",)
        assert mod.depends_on is None

    def test_upgrade_contains_ingestion_events_schema(self) -> None:
        mod = _load(CORE_001, "core_001")
        src = inspect.getsource(mod._create_ingestion_events)
        assert "CREATE TABLE IF NOT EXISTS public.ingestion_events" in src
        assert "dedupe_key" in src
        assert "ix_ingestion_events_received_at" in src
        assert "ix_ingestion_events_source_channel" in src
        assert "ix_ingestion_events_status" in src

    def test_sessions_include_ingestion_event_linkage(self) -> None:
        mod = _load(CORE_001, "core_001")
        src = inspect.getsource(mod._create_core_tables)
        assert "request_id TEXT NOT NULL" in src
        assert "ingestion_event_id UUID REFERENCES public.ingestion_events(id)" in src
        assert "idx_sessions_request_id" in src
        assert "ix_sessions_ingestion_event_id" in src


class TestCore009MemoryCatalogSpecColumns:
    def test_revision_metadata(self) -> None:
        mod = _load(CORE_009, "core_009")
        assert mod.revision == "core_009"
        assert mod.down_revision == "core_008"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_creates_memory_catalog_with_spec_columns(self) -> None:
        src = inspect.getsource(_load(CORE_009, "core_009").upgrade)
        assert "CREATE TABLE IF NOT EXISTS public.memory_catalog" in src
        for col in (
            "title",
            "predicate",
            "scope",
            "valid_at",
            "invalid_at",
            "confidence",
            "importance",
            "retention_class",
            "sensitivity",
            "object_entity_id",
        ):
            assert col in src

    def test_upgrade_creates_spec_indexes(self) -> None:
        src = inspect.getsource(_load(CORE_009, "core_009").upgrade)
        assert "idx_memory_catalog_tenant_scope_predicate" in src
        assert "idx_memory_catalog_object_entity_id" in src
        assert "idx_memory_catalog_sensitivity" in src

    def test_downgrade_drops_table(self) -> None:
        src = inspect.getsource(_load(CORE_009, "core_009").downgrade)
        assert "DROP TABLE IF EXISTS public.memory_catalog CASCADE" in src
