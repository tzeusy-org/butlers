"""core_341: typed provider fields on each schema's ``calendar_events`` (bu-s11n0s.4 S1).

Real migrated databases only: a legacy row becomes ``default``/NULL, a re-run is a catalog
no-op, the evidence-preserving downgrade keeps columns and values and a later upgrade reuses
them, and an incompatible pre-existing column stops the migration.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SCHEMA = "general"


def _execute(db_url: str, statement: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{_SCHEMA}", public'))
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _fetch(db_url: str, statement: str) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{_SCHEMA}", public'))
            return [tuple(row) for row in connection.execute(text(statement)).fetchall()]
    finally:
        engine.dispose()


_SEED_LEGACY = """
    WITH source AS (
        INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id)
        VALUES ('provider:google:primary', 'provider_event', 'user', 'google', 'primary')
        RETURNING id
    )
    INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at,
                                 status, metadata, source_butler)
    SELECT id, 'legacy-1', 'Out of office', 'UTC', '2026-07-01T09:00Z', '2026-07-01T17:00Z',
           'confirmed', '{"provider": "google"}'::jsonb, 'general'
    FROM source
"""

_TABLE_STATE = """
    SELECT c.xmin::text, c.relfilenode::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema() AND c.relname = 'calendar_events'
"""


def test_legacy_rows_default_reruns_are_noops_and_downgrade_keeps_evidence(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_340")
    _execute(db_url, _SEED_LEGACY)

    command.upgrade(config, "core_341")
    # Never guessed from the title.
    assert _fetch(db_url, "SELECT event_type, working_location FROM calendar_events") == [
        ("default", None)
    ]
    _execute(
        db_url,
        "UPDATE calendar_events SET event_type = 'workingLocation', "
        """working_location = '{"type": "homeOffice", "label": "home office"}'::jsonb""",
    )
    table_state = _fetch(db_url, _TABLE_STATE)

    # Evidence-preserving downgrade: the version marker moves, the columns and values stay.
    command.downgrade(config, "core_340")
    typed = _fetch(db_url, "SELECT event_type, working_location FROM calendar_events")
    assert typed == [("workingLocation", {"type": "homeOffice", "label": "home office"})]

    # Re-upgrade reuses the compatible columns without touching the table.
    command.upgrade(config, "core_341")
    assert _fetch(db_url, "SELECT event_type, working_location FROM calendar_events") == typed
    assert _fetch(db_url, _TABLE_STATE) == table_state


def test_an_incompatible_existing_column_stops_the_upgrade(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_340")
    _execute(db_url, "ALTER TABLE calendar_events ADD COLUMN event_type INTEGER")

    with pytest.raises(Exception, match="unexpected shape"):
        command.upgrade(config, "core_341")
