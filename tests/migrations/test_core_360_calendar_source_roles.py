"""core_360: a role and a basis on each schema's ``calendar_sources`` (bu-5ii4gj.1).

Real migrated databases only: existing rows are classified from stored discovery facts and never
from a name, a re-run is a catalog no-op, the evidence-preserving downgrade keeps columns and
values, an incompatible pre-existing column stops the migration, and the checks refuse an unknown
role. Synthetic calendar ids only.
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


# source_key, source_kind, lane, calendar_id, metadata
_LEGACY = [
    (
        "provider:google:owner@example.test",
        "provider_event",
        "user",
        "owner@example.test",
        '{"account_email": "owner@example.test", "butler_specific": false}',
    ),
    # A partner's shared calendar: same account, not the account's own calendar.
    (
        "provider:google:partner@example.test",
        "provider_event",
        "user",
        "partner@example.test",
        '{"account_email": "owner@example.test", "butler_specific": false}',
    ),
    (
        "provider:google:butlers-cal",
        "provider_event",
        "user",
        "butlers-cal@group.calendar.google.com",
        '{"account_email": "owner@example.test", "butler_specific": true}',
    ),
    (
        "provider:google:holidays",
        "provider_event",
        "user",
        "en.sg#holiday@group.v.calendar.google.com",
        '{"account_email": "owner@example.test"}',
    ),
    (
        "provider:google:birthdays",
        "provider_event",
        "user",
        "addressbook#contacts@group.v.calendar.google.com",
        '{"account_email": "owner@example.test"}',
    ),
    ("internal:scheduler:general", "internal_scheduler", "butler", None, "{}"),
    # Nothing stored that identifies the primary: never assumed to be the owner's.
    ("provider:google:unknown", "provider_event", "user", "someone@example.test", "{}"),
]

_ROLES = "SELECT source_key, time_role, role_basis FROM calendar_sources ORDER BY source_key"

_EXPECTED = {
    "provider:google:owner@example.test": "owner",
    "provider:google:partner@example.test": "unclassified",
    # Not trusted from metadata.butler_specific: discovery re-classifies it from the resolved id.
    "provider:google:butlers-cal": "unclassified",
    "provider:google:holidays": "holidays",
    "provider:google:birthdays": "birthdays",
    "internal:scheduler:general": "butler",
    "provider:google:unknown": "unclassified",
}

_TABLE_STATE = """
    SELECT c.xmin::text, c.relfilenode::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema() AND c.relname = 'calendar_sources'
"""


def _seed(db_url: str) -> None:
    for key, kind, lane, calendar_id, metadata in _LEGACY:
        calendar = "NULL" if calendar_id is None else f"'{calendar_id}'"
        _execute(
            db_url,
            "INSERT INTO calendar_sources (source_key, source_kind, lane, calendar_id, metadata) "
            f"VALUES ('{key}', '{kind}', '{lane}', {calendar}, '{metadata}'::jsonb)",
        )


def test_existing_rows_are_classified_from_stored_facts_and_downgrade_keeps_evidence(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_358")
    _seed(db_url)

    command.upgrade(config, "core_360")
    rows = _fetch(db_url, _ROLES)
    assert {key: role for key, role, _ in rows} == _EXPECTED
    assert {basis for _, _, basis in rows} == {"provider_inferred"}

    # An owner's confirmed role is stored data; a re-run must leave the table untouched.
    _execute(
        db_url,
        "UPDATE calendar_sources SET time_role = 'household_member', "
        "role_basis = 'owner_confirmed' WHERE source_key = 'provider:google:partner@example.test'",
    )
    table_state = _fetch(db_url, _TABLE_STATE)
    stored = _fetch(db_url, _ROLES)

    command.downgrade(config, "core_358")
    assert _fetch(db_url, _ROLES) == stored

    command.upgrade(config, "core_360")
    assert _fetch(db_url, _ROLES) == stored
    assert _fetch(db_url, _TABLE_STATE) == table_state


def test_a_new_source_defaults_to_unclassified_and_the_checks_refuse_unknown_values(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_360")

    _execute(
        db_url,
        "INSERT INTO calendar_sources (source_key, source_kind) VALUES ('new', 'provider_event')",
    )
    assert _fetch(db_url, "SELECT time_role, role_basis FROM calendar_sources") == [
        ("unclassified", "provider_inferred")
    ]
    with pytest.raises(Exception, match="calendar_sources_time_role_check"):
        _execute(db_url, "UPDATE calendar_sources SET time_role = 'mine'")
    with pytest.raises(Exception, match="calendar_sources_role_basis_check"):
        _execute(db_url, "UPDATE calendar_sources SET role_basis = 'guessed'")


def test_an_incompatible_existing_column_stops_the_upgrade(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_358")
    _execute(db_url, "ALTER TABLE calendar_sources ADD COLUMN time_role INTEGER")

    with pytest.raises(Exception, match="unexpected shape"):
        command.upgrade(config, "core_360")
