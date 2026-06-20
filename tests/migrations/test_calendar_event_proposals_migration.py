"""Unit tests for core_136 calendar_event_proposals migration.

Verifies the migration file structure, revision chain, and SQL content.
Integration round-trip coverage is provided by
test_core_migration_smoke_downgrade_upgrade_round_trip in
tests/config/test_migrations.py (always exercises the latest core head).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_136_calendar_event_proposals.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_136", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


def test_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_136"
    assert mod.down_revision == "core_135"


def test_no_branch_labels():
    mod = _load_migration()
    assert mod.branch_labels is None


def test_creates_calendar_event_proposals_table():
    source = _MIGRATION_PATH.read_text()
    assert "calendar_event_proposals" in source
    assert "CREATE TABLE IF NOT EXISTS" in source


def test_table_has_uuid_pk():
    source = _MIGRATION_PATH.read_text()
    assert "id" in source
    assert "UUID PRIMARY KEY" in source
    assert "gen_random_uuid()" in source


def test_table_has_butler_name():
    source = _MIGRATION_PATH.read_text()
    assert "butler_name" in source
    assert "NOT NULL" in source


def test_table_has_event_shaped_payload():
    source = _MIGRATION_PATH.read_text()
    for col in ("title", "start_at", "end_at", "description", "location", "timezone"):
        assert col in source, f"Missing event-payload column: {col}"


def test_table_has_source_event_id():
    source = _MIGRATION_PATH.read_text()
    assert "source_event_id" in source


def test_table_has_source_snippet():
    source = _MIGRATION_PATH.read_text()
    assert "source_snippet" in source


def test_table_has_confidence():
    source = _MIGRATION_PATH.read_text()
    assert "confidence" in source


def test_table_has_entity_ids():
    source = _MIGRATION_PATH.read_text()
    assert "entity_ids" in source
    assert "UUID[]" in source


def test_table_has_status_with_check():
    source = _MIGRATION_PATH.read_text()
    assert "status" in source
    assert "pending" in source
    assert "accepted" in source
    assert "dismissed" in source
    assert "CHECK" in source


def test_table_has_accepted_event_id():
    source = _MIGRATION_PATH.read_text()
    assert "accepted_event_id" in source
    assert "REFERENCES calendar_events(id)" in source
    assert "ON DELETE SET NULL" in source


def test_table_has_timestamps():
    source = _MIGRATION_PATH.read_text()
    assert "created_at" in source
    assert "updated_at" in source
    assert "TIMESTAMPTZ" in source


def test_unique_index_on_source_event_id():
    source = _MIGRATION_PATH.read_text()
    assert "uq_calendar_event_proposals_source_event_id" in source
    assert "UNIQUE INDEX" in source
    # Partial unique (WHERE source_event_id IS NOT NULL) handles NULL rows
    assert "source_event_id IS NOT NULL" in source


def test_status_index():
    source = _MIGRATION_PATH.read_text()
    assert "ix_calendar_event_proposals_status" in source
    assert "ON" in source and "status" in source


def test_downgrade_drops_indexes_then_table():
    source = _MIGRATION_PATH.read_text()
    assert "DROP INDEX IF EXISTS ix_calendar_event_proposals_status" in source
    assert "DROP INDEX IF EXISTS uq_calendar_event_proposals_source_event_id" in source
    assert "DROP TABLE IF EXISTS" in source
    assert "calendar_event_proposals" in source
    # Indexes must be dropped before the table
    status_idx_pos = source.index("ix_calendar_event_proposals_status")
    source_idx_pos = source.index("uq_calendar_event_proposals_source_event_id")
    table_drop_pos = source.index("DROP TABLE IF EXISTS")
    assert status_idx_pos < table_drop_pos, "Status index drop must precede table drop"
    assert source_idx_pos < table_drop_pos, "Source event index drop must precede table drop"
