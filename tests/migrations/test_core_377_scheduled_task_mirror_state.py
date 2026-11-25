"""core_377: link id apart from the provider id, plus per-row mirror state (bu-5ii4gj.4).

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

_PREVIOUS = "core_375"
_REVISION = "core_377"
_GOOGLE_ID = "k3v9a1b2c3d4e5f6g7h8i9j0kl"


def _run(db_url: str, schema: str, statement: str, params: dict | None = None) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            result = connection.execute(text(statement), params or {})
            return [tuple(r) for r in result.fetchall()] if result.returns_rows else []
    finally:
        engine.dispose()


def _task(db_url: str, schema: str, name: str, calendar_event_id: str | None) -> None:
    _run(
        db_url,
        schema,
        "INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled, "
        "calendar_event_id) VALUES (:n, '0 9 * * *', 'prompt', 'p', 'db', true, :c)",
        {"n": name, "c": calendar_event_id},
    )


def test_a_uuid_in_the_provider_column_moves_to_the_link_and_a_google_id_stays(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, _PREVIOUS)
    local = str(uuid.uuid4())
    _task(db_url, "general", "local", local)
    _task(db_url, "general", "google", _GOOGLE_ID)
    _task(db_url, "general", "none", None)

    command.upgrade(config, _REVISION)

    rows = {
        r[0]: r[1:]
        for r in _run(
            db_url,
            "general",
            "SELECT name, calendar_event_id, calendar_link_id, mirror_state FROM scheduled_tasks",
        )
    }
    assert rows["local"] == (None, local, "pending")
    assert rows["google"] == (_GOOGLE_ID, None, "pending")
    assert rows["none"] == (None, None, "pending")


def test_the_link_id_is_unique_and_the_state_is_checked(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, _REVISION)
    link = str(uuid.uuid4())
    for name in ("a", "b"):
        _task(db_url, "general", name, None)
    _run(
        db_url,
        "general",
        "UPDATE scheduled_tasks SET calendar_link_id = :l WHERE name = 'a'",
        {"l": link},
    )
    with pytest.raises(Exception, match="ix_scheduled_tasks_calendar_link_id"):
        _run(
            db_url,
            "general",
            "UPDATE scheduled_tasks SET calendar_link_id = :l WHERE name = 'b'",
            {"l": link},
        )
    with pytest.raises(Exception, match="scheduled_tasks_mirror_state_check"):
        _run(db_url, "general", "UPDATE scheduled_tasks SET mirror_state = 'bogus'")


def test_two_schemas_each_get_the_columns_and_a_rerun_changes_nothing(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    for schema in ("general", "travel"):
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=schema), _PREVIOUS
        )
    local = str(uuid.uuid4())
    _task(db_url, "travel", "t", local)
    for schema in ("general", "travel"):
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=schema), _REVISION
        )
    for schema in ("general", "travel"):
        columns = {
            r[0]
            for r in _run(
                db_url,
                schema,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'scheduled_tasks'",
            )
        }
        assert {
            "calendar_link_id",
            "mirror_state",
            "mirror_error",
            "mirror_attempts",
            "mirror_attempted_at",
            "mirror_fingerprint",
            "mirror_attempt_fingerprint",
            "delete_requested_at",
        } <= columns, schema
    assert _run(db_url, "travel", "SELECT calendar_link_id FROM scheduled_tasks") == [(local,)]
    command.downgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="travel"), _PREVIOUS
    )
    command.upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="travel"), _REVISION
    )
    assert _run(db_url, "travel", "SELECT calendar_link_id FROM scheduled_tasks") == [(local,)]
