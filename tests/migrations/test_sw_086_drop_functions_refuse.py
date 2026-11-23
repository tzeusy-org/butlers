"""sw_086: the partition-drop functions refuse until a retention contract exists (bu-uhbhzq.4).

``message_inbox`` is a routing buffer, not an evidence surface, and for many sources its rows
are the only verbatim copy. Real PostgreSQL: either function, with or without arguments,
raises and drops nothing; the SECURITY DEFINER and pinned search path from sw_039 hold; the
downgrade restores the working pinned bodies.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.definer_search_path import is_pinned
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SCHEMA = "switchboard"
_FUNCTIONS = {
    "switchboard_message_inbox_drop_expired_partitions": "message_inbox",
    "switchboard_connector_heartbeat_log_drop_expired_partitions": "connector_heartbeat_log",
}
_FAR_FUTURE = "'2099-05-15T00:00:00+00:00'::timestamptz"


def _migrated(postgres_container) -> str:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard", schema=_SCHEMA))
    return db_url


def _config(db_url: str):
    return _build_alembic_config(db_url, ["switchboard"], target_schema=_SCHEMA)


def _calls(name: str) -> list[str]:
    return [
        f"SELECT switchboard.{name}()",
        f"SELECT switchboard.{name}(INTERVAL '1 day')",
        f"SELECT switchboard.{name}(INTERVAL '1 day', {_FAR_FUTURE})",
        f"SELECT switchboard.{name}(INTERVAL '100 years', {_FAR_FUTURE})",
    ]


def _partitions(db_url: str, parent: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "SELECT child.relname FROM pg_inherits "
                        "JOIN pg_class p ON p.oid = pg_inherits.inhparent "
                        "JOIN pg_namespace ns ON ns.oid = p.relnamespace "
                        "JOIN pg_class child ON child.oid = pg_inherits.inhrelid "
                        "WHERE ns.nspname = :schema AND p.relname = :parent"
                    ),
                    {"schema": _SCHEMA, "parent": parent},
                ).scalars()
            )
    finally:
        engine.dispose()


def _catalog(db_url: str, name: str) -> tuple[bool, list[str]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT p.prosecdef, p.proconfig FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = :schema AND p.proname = :name"
                ),
                {"schema": _SCHEMA, "name": name},
            ).one()
            return bool(row.prosecdef), list(row.proconfig or [])
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", sorted(_FUNCTIONS))
def test_either_drop_function_refuses_and_drops_nothing(postgres_container, name: str) -> None:
    db_url = _migrated(postgres_container)
    parent = _FUNCTIONS[name]
    before = _partitions(db_url, parent)
    assert before  # the current month's partition exists
    for statement in _calls(name):
        engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn, pytest.raises(DBAPIError) as raised:
                conn.execute(text(statement))
            assert "bu-uhbhzq.7" in str(raised.value.orig), statement
        finally:
            engine.dispose()
    assert _partitions(db_url, parent) == before


@pytest.mark.parametrize("name", sorted(_FUNCTIONS))
def test_the_definer_attributes_and_pinned_path_survive(postgres_container, name: str) -> None:
    db_url = _migrated(postgres_container)
    definer, config = _catalog(db_url, name)
    assert definer
    assert is_pinned(config), config


@pytest.mark.parametrize("name", sorted(_FUNCTIONS))
def test_the_downgrade_restores_the_working_pinned_function(postgres_container, name: str) -> None:
    db_url = _migrated(postgres_container)
    command.downgrade(_config(db_url), "switchboard@sw_085")
    definer, config = _catalog(db_url, name)
    assert definer and is_pinned(config)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            dropped = conn.execute(
                text(f"SELECT switchboard.{name}(INTERVAL '1 day', {_FAR_FUTURE})")
            ).scalar()
        assert dropped >= 1  # the old behaviour is back
    finally:
        engine.dispose()
