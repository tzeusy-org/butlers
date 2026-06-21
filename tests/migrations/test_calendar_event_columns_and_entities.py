"""Tests for core_076 calendar_event column additions and calendar_event_entities migration.

Unit tests verify the migration revision chain and required schema objects.
Integration is covered by test_core_calendar_tables_and_constraints in
tests/config/test_migrations.py which runs the full chain including this migration.
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


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_core_migration_revision_chain():
    mod = _load(_CORE_MIGRATION_PATH, "core_076")
    assert mod.revision == "core_076"
    assert mod.down_revision == "core_075"


def test_rel_migration_revision_chain():
    mod = _load(_REL_MIGRATION_PATH, "rel_007")
    assert mod.revision == "rel_007"
    assert mod.down_revision == "rel_006"


@pytest.mark.parametrize(
    "needle",
    [
        # additive columns
        "source_butler",
        "source_session_id",
        "body TEXT",
        "DROP DEFAULT",
        # junction table + FKs
        "calendar_event_entities",
        "REFERENCES calendar_events(id)",
        "REFERENCES public.entities(id)",
        "ON DELETE CASCADE",
        # indexes
        "idx_calendar_event_entities_entity",
        "ix_calendar_events_source_butler",
        # title backfill from metadata
        "metadata->>'title'",
        "metadata->>'display_title'",
        "'(untitled)'",
    ],
)
def test_core_migration_required_schema_objects(needle: str):
    """core_076 adds the documented columns, junction table, indexes, and title backfill."""
    assert needle in _CORE_MIGRATION_PATH.read_text()


def test_core_migration_downgrade_drops_columns_and_table():
    source = _CORE_MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS calendar_event_entities" in source
    assert "DROP COLUMN IF EXISTS body" in source
    assert "DROP COLUMN IF EXISTS source_session_id" in source
    assert "DROP COLUMN IF EXISTS source_butler" in source


@pytest.mark.parametrize(
    "needle",
    [
        # inserts reminders into calendar_events with 15-min duration
        "INSERT INTO calendar_events",
        "interval '15 minutes'",
        "internal_reminders",
        # populates junction table
        "calendar_event_entities",
        # rrule mapping + entity resolution + idempotency
        "RRULE:FREQ=YEARLY",
        "RRULE:FREQ=MONTHLY",
        "public.contacts",
        "ON CONFLICT",
        # old-reminder-fact cleanup
        "DELETE FROM facts",
        # renames reminders table to backup
        "RENAME TO _reminders_backup",
    ],
)
def test_rel_migration_required_schema_objects(needle: str):
    """rel_007 migrates reminders into calendar_events + junction, idempotently."""
    assert needle in _REL_MIGRATION_PATH.read_text()


def test_rel_migration_downgrade_restores_reminders_table_name():
    assert "ALTER TABLE _reminders_backup RENAME TO reminders" in _REL_MIGRATION_PATH.read_text()
