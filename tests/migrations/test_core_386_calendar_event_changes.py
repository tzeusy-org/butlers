"""core_386: the shared journal reader survives a per-schema downgrade (bu-5ii4gj.11 N3).

The core chain runs once per butler schema over one shared function: a downgrade of one schema must
leave it for the other, and only the last schema removes it. Real migrated databases only.
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

_PREVIOUS = "core_383"
_REVISION = "core_386"
_SIGNATURE = "public.read_calendar_event_changes(timestamptz, integer, text[])"


def _exists(db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            return bool(
                connection.execute(text(f"SELECT to_regprocedure('{_SIGNATURE}')")).scalar()
            )
    finally:
        engine.dispose()


def test_the_reader_is_removed_only_by_the_last_schema_that_downgrades(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    schemas = ("general", "travel")
    configs = {s: _build_alembic_config(db_url, chains=["core"], target_schema=s) for s in schemas}
    for config in configs.values():
        command.upgrade(config, _REVISION)
    assert _exists(db_url)

    command.upgrade(configs["general"], _REVISION)  # an idempotent re-run
    command.downgrade(configs["general"], _PREVIOUS)
    assert _exists(db_url), "travel still depends on the shared function"

    command.downgrade(configs["travel"], _PREVIOUS)
    assert not _exists(db_url)

    command.upgrade(configs["general"], _REVISION)
    assert _exists(db_url)
