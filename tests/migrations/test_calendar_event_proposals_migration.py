"""Unit tests for core_136 calendar_event_proposals migration.

Verifies the revision chain and required schema objects. Integration round-trip
coverage is provided by test_core_migration_smoke_downgrade_upgrade_round_trip in
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


def test_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_136"
    assert mod.down_revision == "core_135"
    assert mod.branch_labels is None


@pytest.mark.parametrize(
    "needle",
    [
        # table + pk
        "calendar_event_proposals",
        "UUID PRIMARY KEY",
        "gen_random_uuid()",
        "butler_name",
        # event-shaped payload columns
        "title",
        "start_at",
        "end_at",
        "source_event_id",
        "source_snippet",
        "confidence",
        "entity_ids",
        "UUID[]",
        # status CHECK vocab
        "pending",
        "accepted",
        "dismissed",
        "CHECK",
        # FK + timestamps
        "REFERENCES calendar_events(id)",
        "ON DELETE SET NULL",
        "created_at",
        "updated_at",
    ],
)
def test_table_required_schema_objects(needle: str):
    """core_136 creates the proposals table with documented columns/constraints."""
    assert needle in _MIGRATION_PATH.read_text()


def test_indexes_created():
    source = _MIGRATION_PATH.read_text()
    # partial unique on source_event_id (handles NULL rows)
    assert "uq_calendar_event_proposals_source_event_id" in source
    assert "UNIQUE INDEX" in source
    assert "source_event_id IS NOT NULL" in source
    # status index
    assert "ix_calendar_event_proposals_status" in source


def test_downgrade_drops_indexes_then_table():
    source = _MIGRATION_PATH.read_text()
    assert "DROP INDEX IF EXISTS ix_calendar_event_proposals_status" in source
    assert "DROP INDEX IF EXISTS uq_calendar_event_proposals_source_event_id" in source
    assert "DROP TABLE IF EXISTS" in source
    # Indexes must be dropped before the table
    table_drop_pos = source.index("DROP TABLE IF EXISTS")
    assert source.index("ix_calendar_event_proposals_status") < table_drop_pos
    assert source.index("uq_calendar_event_proposals_source_event_id") < table_drop_pos
