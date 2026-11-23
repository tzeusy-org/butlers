"""core_365: ``role_subject_entity_id`` on each schema's ``calendar_sources`` (bu-5ii4gj.1 S3).

Real migrated databases only. Synthetic ids only.
"""

from __future__ import annotations

import shutil
import uuid

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


def _run(db_url: str, statement: str, params: dict | None = None) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{_SCHEMA}", public'))
            result = connection.execute(text(statement), params or {})
            return [tuple(r) for r in result.fetchall()] if result.returns_rows else []
    finally:
        engine.dispose()


def _upgraded(postgres_container) -> tuple[str, object]:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_365")
    return db_url, config


def test_the_column_references_entities_and_a_deleted_entity_clears_it(postgres_container) -> None:
    db_url, _config = _upgraded(postgres_container)
    entity = str(uuid.uuid4())
    _run(
        db_url,
        "INSERT INTO public.entities (id, canonical_name, entity_type) "
        "VALUES (:id, 'Synthetic Partner', 'person')",
        {"id": entity},
    )
    _run(
        db_url,
        "INSERT INTO calendar_sources (source_key, source_kind, time_role, role_basis, "
        "role_subject_entity_id) VALUES ('s', 'provider_event', 'household_member', "
        "'owner_confirmed', :id)",
        {"id": entity},
    )
    _run(db_url, "DELETE FROM public.entities WHERE id = :id", {"id": entity})
    assert _run(db_url, "SELECT role_subject_entity_id FROM calendar_sources") == [(None,)]


def test_the_subject_is_refused_for_a_role_that_has_no_subject(postgres_container) -> None:
    db_url, _config = _upgraded(postgres_container)
    entity = str(uuid.uuid4())
    _run(
        db_url,
        "INSERT INTO public.entities (id, canonical_name, entity_type) "
        "VALUES (:id, 'Synthetic Partner', 'person')",
        {"id": entity},
    )
    with pytest.raises(Exception, match="calendar_sources_role_subject_role_check"):
        _run(
            db_url,
            "INSERT INTO calendar_sources (source_key, source_kind, time_role, "
            "role_subject_entity_id) VALUES ('s', 'provider_event', 'owner', :id)",
            {"id": entity},
        )


def test_a_rerun_is_a_noop_and_downgrade_keeps_the_column(postgres_container) -> None:
    db_url, config = _upgraded(postgres_container)
    state = _run(
        db_url,
        "SELECT c.relfilenode::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relname = 'calendar_sources'",
    )
    command.downgrade(config, "core_360")
    assert _run(
        db_url,
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'calendar_sources' AND column_name = 'role_subject_entity_id'",
    )
    command.upgrade(config, "core_365")
    assert (
        _run(
            db_url,
            "SELECT c.relfilenode::text FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() "
            "AND c.relname = 'calendar_sources'",
        )
        == state
    )


def test_an_incompatible_existing_column_stops_the_upgrade(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_360")
    _run(db_url, "ALTER TABLE calendar_sources ADD COLUMN role_subject_entity_id TEXT")
    with pytest.raises(Exception, match="unexpected shape"):
        command.upgrade(config, "core_365")
