"""core_385: the shared acknowledgement table installs once, is narrowly granted, drops last.

The core chain runs once per butler schema over the one ``public`` table, so a second schema's
upgrade is a no-op, and only the last schema's downgrade drops it (bu-5ii4gj.11 R2a).
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.db_authority.roles import butler_roles
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_TABLE = "public.calendar_conflict_acknowledgements"
_BELOW = "core_380"


def _roles(engine) -> None:
    with engine.connect() as conn:
        for role in butler_roles():
            if not conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
            ).scalar():
                conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))


def test_two_schemas_install_once_and_only_the_last_downgrade_drops_the_table(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="travel")
    command.upgrade(first, "core_385")
    command.upgrade(second, "core_385")  # the second schema finds it installed
    command.upgrade(second, "core_385")  # and a replay changes nothing
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:

        def present() -> bool:
            with engine.connect() as conn:
                return bool(conn.execute(text("SELECT to_regclass(:n)"), {"n": _TABLE}).scalar())

        assert present()
        command.downgrade(first, _BELOW)
        assert present()  # an earlier schema's downgrade keeps what the other still needs
        command.downgrade(second, _BELOW)
        assert not present()  # the last one drops it
    finally:
        engine.dispose()


def test_every_butler_role_converges_to_exactly_dml(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        _roles(engine)
        for schema in ("general", "travel"):  # the second run converges, it does not add
            command.upgrade(
                _build_alembic_config(db_url, chains=["core"], target_schema=schema), "core_385"
            )
        with engine.connect() as conn:
            granted = {
                (role, privilege)
                for role, privilege in conn.execute(
                    text(
                        "SELECT r.rolname, a.privilege_type "
                        "FROM pg_class c, aclexplode(c.relacl) a "
                        "JOIN pg_roles r ON r.oid = a.grantee "
                        "WHERE c.oid = to_regclass(:n) AND a.grantee <> c.relowner"
                    ),
                    {"n": _TABLE},
                )
            }
            public = conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a "
                    "WHERE c.oid = to_regclass(:n) AND a.grantee = 0"
                ),
                {"n": _TABLE},
            ).scalar()
        dml = ("SELECT", "INSERT", "UPDATE", "DELETE")
        # Every butler role holds exactly DML; the only other grantee is the fleet's connector
        # role (init-db's default privileges), also DML at most.
        assert {g for g in granted if g[0] in butler_roles()} == {
            (role, privilege) for role in butler_roles() for privilege in dml
        }
        assert {privilege for _, privilege in granted} <= set(dml)
        assert public == 0
    finally:
        engine.dispose()
