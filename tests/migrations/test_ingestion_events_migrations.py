"""Tests for ingestion events migrations (core_019, core_020, core_021)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"


def _load_migration(filename: str, module_name: str):
    filepath = VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# core_019 — shared.ingestion_events table
# ---------------------------------------------------------------------------


class TestCore019RevisionMetadata:
    def test_revision(self) -> None:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        assert mod.revision == "core_019"

    def test_down_revision(self) -> None:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        assert mod.down_revision == "core_018"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        assert mod.depends_on is None

    def test_upgrade_downgrade_callable(self) -> None:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestCore019IngestionEventsTable:
    def _src(self) -> str:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        return inspect.getsource(mod.upgrade)

    def test_creates_shared_ingestion_events(self) -> None:
        assert "shared.ingestion_events" in self._src()

    def test_create_if_not_exists(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS shared.ingestion_events" in self._src()

    def test_pk_column_id(self) -> None:
        assert "id" in self._src()
        assert "UUID PRIMARY KEY" in self._src()

    def test_received_at_not_null_default_now(self) -> None:
        src = self._src()
        assert "received_at" in src
        assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in src

    def test_required_text_columns_present(self) -> None:
        src = self._src()
        for col in (
            "source_channel",
            "source_provider",
            "source_endpoint_identity",
            "external_event_id",
            "dedupe_key",
            "dedupe_strategy",
            "ingestion_tier",
            "policy_tier",
        ):
            assert col in src, f"Missing required column: {col}"

    def test_nullable_columns_present(self) -> None:
        src = self._src()
        for col in (
            "source_sender_identity",
            "source_thread_identity",
            "triage_decision",
            "triage_target",
        ):
            assert col in src, f"Missing nullable column: {col}"

    def test_dedupe_key_unique_constraint(self) -> None:
        src = self._src()
        assert "UNIQUE (dedupe_key)" in src or "uq_ingestion_events_dedupe_key" in src

    def test_received_at_index_created(self) -> None:
        src = self._src()
        assert "ix_ingestion_events_received_at" in src

    def test_source_channel_index_created(self) -> None:
        src = self._src()
        assert "ix_ingestion_events_source_channel" in src


class TestCore019Downgrade:
    def _src(self) -> str:
        mod = _load_migration("core_019_create_ingestion_events.py", "core_019")
        return inspect.getsource(mod.downgrade)

    def test_drops_table(self) -> None:
        assert "DROP TABLE IF EXISTS shared.ingestion_events" in self._src()

    def test_drops_indexes_before_table(self) -> None:
        src = self._src()
        idx_pos = src.find("DROP INDEX IF EXISTS ix_ingestion_events_received_at")
        table_pos = src.find("DROP TABLE IF EXISTS shared.ingestion_events")
        assert idx_pos < table_pos


# ---------------------------------------------------------------------------
# core_020 — sessions.ingestion_event_id FK column
# ---------------------------------------------------------------------------


class TestCore020RevisionMetadata:
    def test_revision(self) -> None:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        assert mod.revision == "core_020"

    def test_down_revision(self) -> None:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        assert mod.down_revision == "core_019"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        assert mod.branch_labels is None

    def test_upgrade_downgrade_callable(self) -> None:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestCore020SessionsIngestionEventId:
    def _src(self) -> str:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        return inspect.getsource(mod.upgrade)

    def test_adds_column_if_not_exists(self) -> None:
        assert "ADD COLUMN IF NOT EXISTS ingestion_event_id" in self._src()

    def test_column_type_is_uuid(self) -> None:
        assert "ingestion_event_id UUID" in self._src()

    def test_references_shared_ingestion_events(self) -> None:
        assert "REFERENCES shared.ingestion_events(id)" in self._src()

    def test_column_is_nullable(self) -> None:
        # Nullable = no NOT NULL constraint on the column
        src = self._src()
        col_start = src.find("ingestion_event_id UUID")
        col_end = src.find("\n", col_start)
        col_def = src[col_start:col_end]
        assert "NOT NULL" not in col_def

    def test_partial_index_on_ingestion_event_id(self) -> None:
        src = self._src()
        assert "ix_sessions_ingestion_event_id" in src
        assert "WHERE ingestion_event_id IS NOT NULL" in src


class TestCore020Downgrade:
    def _src(self) -> str:
        mod = _load_migration("core_020_sessions_add_ingestion_event_id.py", "core_020")
        return inspect.getsource(mod.downgrade)

    def test_drops_column(self) -> None:
        assert "DROP COLUMN IF EXISTS ingestion_event_id" in self._src()

    def test_drops_index_before_column(self) -> None:
        src = self._src()
        idx_pos = src.find("DROP INDEX IF EXISTS ix_sessions_ingestion_event_id")
        col_pos = src.find("DROP COLUMN IF EXISTS ingestion_event_id")
        assert idx_pos < col_pos


# ---------------------------------------------------------------------------
# core_021 — sessions.request_id NOT NULL
# ---------------------------------------------------------------------------


class TestCore021RevisionMetadata:
    def test_revision(self) -> None:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        assert mod.revision == "core_021"

    def test_down_revision(self) -> None:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        assert mod.down_revision == "core_020"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        assert mod.branch_labels is None

    def test_upgrade_downgrade_callable(self) -> None:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestCore021RequestIdNotNull:
    def _src(self) -> str:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        return inspect.getsource(mod.upgrade)

    def test_backfills_null_rows(self) -> None:
        src = self._src()
        assert "UPDATE sessions" in src
        assert "WHERE request_id IS NULL" in src

    def test_backfill_uses_gen_random_uuid(self) -> None:
        assert "gen_random_uuid()" in self._src()

    def test_sets_not_null_constraint(self) -> None:
        assert "SET NOT NULL" in self._src()

    def test_backfill_runs_before_constraint(self) -> None:
        src = self._src()
        backfill_pos = src.find("WHERE request_id IS NULL")
        not_null_pos = src.find("SET NOT NULL")
        assert backfill_pos < not_null_pos


class TestCore021Downgrade:
    def _src(self) -> str:
        mod = _load_migration("core_021_sessions_request_id_not_null.py", "core_021")
        return inspect.getsource(mod.downgrade)

    def test_drops_not_null_constraint(self) -> None:
        assert "DROP NOT NULL" in self._src()

    def test_targets_request_id(self) -> None:
        assert "request_id" in self._src()


# ---------------------------------------------------------------------------
# core_024 — memory_catalog spec columns
# ---------------------------------------------------------------------------


class TestCore024RevisionMetadata:
    def test_revision(self) -> None:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        assert mod.revision == "core_024"

    def test_down_revision(self) -> None:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        assert mod.down_revision == "core_023"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        assert mod.depends_on is None

    def test_upgrade_downgrade_callable(self) -> None:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestCore024MemoryCatalogSpecColumns:
    def _upgrade_src(self) -> str:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        return inspect.getsource(mod.upgrade)

    def _downgrade_src(self) -> str:
        mod = _load_migration("core_024_memory_catalog_spec_columns.py", "core_024")
        return inspect.getsource(mod.downgrade)

    def test_adds_title_column(self) -> None:
        assert "title" in self._upgrade_src()
        assert "ADD COLUMN IF NOT EXISTS" in self._upgrade_src()

    def test_adds_predicate_column(self) -> None:
        assert "predicate" in self._upgrade_src()

    def test_adds_scope_column(self) -> None:
        assert "scope" in self._upgrade_src()

    def test_adds_valid_at_timestamptz(self) -> None:
        src = self._upgrade_src()
        assert "valid_at" in src
        assert "TIMESTAMPTZ" in src

    def test_adds_invalid_at_timestamptz(self) -> None:
        src = self._upgrade_src()
        assert "invalid_at" in src
        assert "TIMESTAMPTZ" in src

    def test_adds_confidence_double_precision(self) -> None:
        src = self._upgrade_src()
        assert "confidence" in src
        assert "DOUBLE PRECISION" in src

    def test_adds_importance_double_precision(self) -> None:
        src = self._upgrade_src()
        assert "importance" in src
        assert "DOUBLE PRECISION" in src

    def test_adds_retention_class_text(self) -> None:
        assert "retention_class" in self._upgrade_src()

    def test_adds_sensitivity_text(self) -> None:
        assert "sensitivity" in self._upgrade_src()

    def test_adds_object_entity_id_with_fk(self) -> None:
        src = self._upgrade_src()
        assert "object_entity_id" in src
        assert "shared.entities" in src

    def test_add_columns_uses_if_not_exists(self) -> None:
        """Ensures migration is idempotent — uses ADD COLUMN IF NOT EXISTS."""
        assert "ADD COLUMN IF NOT EXISTS" in self._upgrade_src()

    def test_scope_predicate_index_created(self) -> None:
        src = self._upgrade_src()
        assert "idx_memory_catalog_tenant_scope_predicate" in src

    def test_object_entity_id_index_created(self) -> None:
        assert "idx_memory_catalog_object_entity_id" in self._upgrade_src()

    def test_sensitivity_index_created(self) -> None:
        assert "idx_memory_catalog_sensitivity" in self._upgrade_src()

    def test_downgrade_drops_all_new_columns(self) -> None:
        src = self._downgrade_src()
        for col in (
            "object_entity_id",
            "sensitivity",
            "retention_class",
            "importance",
            "confidence",
            "invalid_at",
            "valid_at",
            "scope",
            "predicate",
            "title",
        ):
            assert col in src, f"Downgrade must drop column: {col}"

    def test_downgrade_drops_indexes_before_columns(self) -> None:
        src = self._downgrade_src()
        idx_pos = src.find("DROP INDEX IF EXISTS")
        col_pos = src.find("DROP COLUMN IF EXISTS")
        assert idx_pos < col_pos, "Indexes should be dropped before columns"
