"""core_283 / sw_054: three roles read only switchboard.message_inbox (bu-svqpzo.13).

Real PostgreSQL (Docker), CI only.  The relationship, messenger and travel runtime roles
used to hold SELECT on every Switchboard table (core_077, core_143), including
``butler_secrets``, plus default privileges for every future table.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.config.test_schema_acl_isolation import _rerun_init_db_as_control

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
    """Core runs before the Switchboard chain; sw_054 supplies the grant once the table exists."""
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
    """A database already at core_268 holds the schema-wide grants; core_283 removes them."""
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


def test_core_283_restores_a_revoked_message_inbox_grant(postgres_container) -> None:
    """Repair path: the inbox grant is missing, core_283 runs, and the grant is restored."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    command.downgrade(core, "core_282")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for role in _ROLES:
                connection.execute(text(f"REVOKE ALL ON switchboard.message_inbox FROM {role}"))
    finally:
        engine.dispose()
    for role in _ROLES:
        assert _select_grants(db_url, role)["message_inbox"] is False, role

    command.upgrade(core, "core@head")

    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_downgrade_restores_the_previous_grants_and_upgrade_repeats_cleanly(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    assert _readable(db_url, "butler_travel_rw") == {"message_inbox"}

    command.downgrade(core, "core_282")
    # Roll back only this unpublished narrowing revision. Public core_271..282
    # remain installed, and the managed migration identity owns this DDL.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.rollback_read_probe (id int)"))
    finally:
        engine.dispose()
    for role in _ROLES:
        assert "butler_secrets" in _readable(db_url, role), role
        assert _select_grants(db_url, role)["rollback_read_probe"] is True, role

    command.upgrade(core, "core@head")
    command.upgrade(core, "core@head")  # idempotent for the next butler schema's core run
    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role


def _relationship_switchboard_default_acl(db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl AS d "
                        "JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace "
                        "CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) AS a "
                        "WHERE n.nspname = 'switchboard' AND d.defaclobjtype = 'r' "
                        "AND a.grantee = (SELECT oid FROM pg_catalog.pg_roles "
                        "WHERE rolname = 'butler_relationship_rw') "
                        "AND a.privilege_type = 'SELECT')"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_rerunning_the_bootstrap_after_head_does_not_rewiden_relationship_reads(
    postgres_container,
) -> None:
    """The documented bootstrap rerun must not restore the schema-wide read or its default."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    assert _readable(db_url, "butler_relationship_rw") == {"message_inbox"}

    migration_user = urlparse(db_url).username
    assert migration_user is not None
    _rerun_init_db_as_control(
        migration_bootstrap_db_url(postgres_container, db_name), migration_user
    )

    assert _readable(db_url, "butler_relationship_rw") == {"message_inbox"}
    assert _relationship_switchboard_default_acl(db_url) is False
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.later_table (id int)"))
    finally:
        engine.dispose()
    assert _select_grants(db_url, "butler_relationship_rw")["later_table"] is False
