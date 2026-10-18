"""core_269 / sw_047: three roles read only switchboard.message_inbox (bu-svqpzo.13).

Real PostgreSQL (Docker), CI only.  The relationship, messenger and travel runtime roles
used to hold SELECT on every Switchboard table (core_077, core_143), including
``butler_secrets``, plus default privileges for every future table.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_ROLES = ("butler_relationship_rw", "butler_messenger_rw", "butler_travel_rw")


def _configs(db_url: str):
    core = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    chain = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    return core, chain


def _select_grants(db_url: str, role: str) -> dict[str, bool]:
    """has_table_privilege(role, ..., SELECT) for every switchboard table."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT c.relname, has_table_privilege(CAST(:role AS name), c.oid, 'SELECT') "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'switchboard' AND c.relkind IN ('r', 'p') "
                    "AND NOT c.relispartition"
                ),
                {"role": role},
            )
            return {name: bool(granted) for name, granted in rows}
    finally:
        engine.dispose()


def _readable(db_url: str, role: str) -> set[str]:
    return {name for name, granted in _select_grants(db_url, role).items() if granted}


def test_fresh_database_grants_message_inbox_and_nothing_else(postgres_container) -> None:
    """Core runs before the Switchboard chain; sw_047 supplies the grant once the table exists."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")

    for role in _ROLES:
        grants = _select_grants(db_url, role)
        assert grants["message_inbox"] is True
        assert "butler_secrets" in grants
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_existing_wide_grants_are_narrowed_and_future_tables_are_not_readable(
    postgres_container,
) -> None:
    """A database already at core_268 holds the schema-wide grants; core_269 removes them."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core_268")
    command.upgrade(chain, "switchboard@sw_046")

    for role in _ROLES:
        before = _readable(db_url, role)
        assert {"message_inbox", "butler_secrets"} <= before, role

    command.upgrade(core, "core@head")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.future_goal_ledger (id int)"))
    finally:
        engine.dispose()

    for role in _ROLES:
        grants = _select_grants(db_url, role)
        assert grants["future_goal_ledger"] is False, role
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_downgrade_restores_the_previous_grants_and_upgrade_repeats_cleanly(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    assert _readable(db_url, "butler_travel_rw") == {"message_inbox"}

    command.downgrade(core, "core_268")
    for role in _ROLES:
        assert "butler_secrets" in _readable(db_url, role), role

    command.upgrade(core, "core@head")
    command.upgrade(core, "core@head")  # idempotent for the next butler schema's core run
    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role
