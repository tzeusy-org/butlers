"""Tests for core_076 calendar_event column additions and calendar_event_entities migration.

Unit tests verify the migration file structure, revision chain, and SQL content.
Integration tests are covered by the existing test_core_calendar_tables_and_constraints
test in tests/config/test_migrations.py which runs the full chain including this migration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_076_calendar_event_columns_and_entities.py"
)

_REL_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "007_reminders_to_calendar_events.py"
)


def _load_core_migration():
    spec = importlib.util.spec_from_file_location("core_076", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_rel_migration():
    spec = importlib.util.spec_from_file_location("rel_007", _REL_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Core migration: file structure and revision chain
# ---------------------------------------------------------------------------


def test_core_migration_file_exists():
    assert _CORE_MIGRATION_PATH.exists(), f"Migration file not found: {_CORE_MIGRATION_PATH}"


def test_core_migration_revision_chain():
    mod = _load_core_migration()
    assert mod.revision == "core_076"
    assert mod.down_revision == "core_075"


def test_core_migration_adds_source_butler_column():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "source_butler" in source
    assert "NOT NULL" in source
    assert "DEFAULT 'unknown'" in source


def test_core_migration_adds_source_session_id_column():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "source_session_id" in source


def test_core_migration_adds_body_column():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "body TEXT" in source


def test_core_migration_drops_source_butler_default():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "ALTER COLUMN source_butler DROP DEFAULT" in source


def test_core_migration_creates_calendar_event_entities_table():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "calendar_event_entities" in source
    assert "event_id" in source
    assert "entity_id" in source
    # FK to calendar_events
    assert "REFERENCES calendar_events(id)" in source
    # FK to public.entities
    assert "REFERENCES public.entities(id)" in source
    # Cascades
    assert "ON DELETE CASCADE" in source


def test_core_migration_creates_entity_index():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "idx_calendar_event_entities_entity" in source
    assert "ON calendar_event_entities (entity_id)" in source


def test_core_migration_creates_source_butler_index():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "ix_calendar_events_source_butler" in source
    assert "ON calendar_events (source_butler)" in source


def test_core_migration_backfills_title_from_metadata():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "metadata->>'title'" in source
    assert "metadata->>'display_title'" in source
    assert "'(untitled)'" in source


def test_core_migration_downgrade_drops_columns_and_table():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS calendar_event_entities" in source
    assert "DROP COLUMN IF EXISTS body" in source
    assert "DROP COLUMN IF EXISTS source_session_id" in source
    assert "DROP COLUMN IF EXISTS source_butler" in source


# ---------------------------------------------------------------------------
# Relationship migration: file structure and revision chain
# ---------------------------------------------------------------------------


def test_rel_migration_file_exists():
    assert _REL_MIGRATION_PATH.exists(), f"Migration file not found: {_REL_MIGRATION_PATH}"


def test_rel_migration_revision_chain():
    mod = _load_rel_migration()
    assert mod.revision == "rel_007"
    assert mod.down_revision == "rel_006"


def test_rel_migration_inserts_into_calendar_events():
    source = _REL_MIGRATION_PATH.read_text()
    assert "INSERT INTO calendar_events" in source
    assert "source_butler" in source
    # Reminders get a 15-minute duration
    assert "interval '15 minutes'" in source


def test_rel_migration_inserts_internal_reminders_source():
    source = _REL_MIGRATION_PATH.read_text()
    assert "internal_reminders" in source
    assert "calendar_sources" in source


def test_rel_migration_populates_junction_table():
    source = _REL_MIGRATION_PATH.read_text()
    assert "calendar_event_entities" in source
    assert "entity_id" in source


def test_rel_migration_rrule_mapping():
    source = _REL_MIGRATION_PATH.read_text()
    assert "RRULE:FREQ=YEARLY" in source
    assert "RRULE:FREQ=MONTHLY" in source


def test_rel_migration_deletes_reminder_facts():
    source = _REL_MIGRATION_PATH.read_text()
    assert "DELETE FROM facts" in source
    assert "predicate = 'reminder'" in source


def test_rel_migration_renames_reminders_table():
    source = _REL_MIGRATION_PATH.read_text()
    assert "_reminders_backup" in source
    assert "RENAME TO _reminders_backup" in source


def test_rel_migration_entity_resolution_from_subject():
    source = _REL_MIGRATION_PATH.read_text()
    # The subject parser looks for contact:{id}:reminder:{uuid}
    assert "contact_id" in source
    assert "public.contacts" in source


def test_rel_migration_idempotent_insert():
    source = _REL_MIGRATION_PATH.read_text()
    assert "ON CONFLICT" in source
    assert "DO NOTHING" in source


def test_rel_migration_downgrade_restores_reminders_table_name():
    source = _REL_MIGRATION_PATH.read_text()
    assert "ALTER TABLE _reminders_backup RENAME TO reminders" in source
