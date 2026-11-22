"""Real-Postgres check of the one superuser ownership step (bu-dss7he.6 S3).

The migrated test database stands in for a deployed one: its migration login plays the
shared login. After ``transfer_ownership`` the shared login owns nothing in the database,
the migrator owns everything the shared login owned, every object owned by a purpose role
(the DND FORCE-RLS tables' ``dnd_generation_owner`` included) keeps its owner, and the
migrator holds every membership the shared login held. Replaying init-db.sql with the
migrator as the connecting user and re-running the migrations as the migrator then
succeeds, which is the activation path in docs/data_and_storage/migrator-login.md.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any
from urllib.parse import quote, urlparse

import pytest
from sqlalchemy import create_engine, text

from butlers.db_authority.ownership import (
    read_transfer_plan,
    transfer_ownership,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_OWNERS_SQL = text(
    """
    SELECT pg_catalog.pg_describe_object(d.classid, d.objid, d.objsubid), r.rolname
    FROM pg_catalog.pg_shdepend AS d
    JOIN pg_catalog.pg_roles AS r ON r.oid = d.refobjid
    WHERE d.deptype = 'o'
      AND d.classid <> 'pg_catalog.pg_default_acl'::regclass
      AND d.dbid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())
    """
)
_MEMBERSHIPS_SQL = text(
    """
    SELECT r.rolname, bool_or(m.inherit_option), bool_or(m.set_option), bool_or(m.admin_option)
    FROM pg_catalog.pg_auth_members AS m
    JOIN pg_catalog.pg_roles AS r ON r.oid = m.roleid
    WHERE m.member = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :login)
    GROUP BY r.rolname
    """
)


def _owners(conn) -> dict[str, str]:
    return dict(conn.execute(_OWNERS_SQL).all())


def _memberships(conn, login: str) -> set[tuple]:
    return {tuple(row) for row in conn.execute(_MEMBERSHIPS_SQL, {"login": login}).all()}


def _create_login(admin_url: str, name: str, password: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f'CREATE ROLE "{name}" LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE '
                    "NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD :password"
                ),
                {"password": password},
            )
    finally:
        engine.dispose()


def _replay_init_db(bootstrap_url: str, connecting_user: str) -> None:
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    raw: Any = engine.raw_connection()  # DBAPI cursor; init-db has literal % signs
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)", (connecting_user,)
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()


def test_transfer_moves_only_the_shared_logins_objects_and_activation_migrates(
    postgres_container, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A template clone shares its login with the template database, which would make that
    # database a foreign shared object of the login (and a refusal); use a private login.
    monkeypatch.setenv("BUTLERS_TEST_DB_TEMPLATES", "0")
    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )
    shared = str(urlparse(db_url).username)
    migrator = f"migrator_{db_name}"
    password = uuid.uuid4().hex
    _create_login(postgres_container.get_connection_url(), migrator, password)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            owners_before = _owners(conn)
            memberships_before = _memberships(conn, shared)
            dry = read_transfer_plan(conn, shared, migrator)
        assert dry.refusals == ()
        # The test database itself is owned by its migration login, so it moves too.
        assert set(dry.owned) == {o for o, r in owners_before.items() if r == shared} | {
            f"database {db_name}"
        }
        assert dry.owned, "the shared login must own something for this test to mean anything"
        assert memberships_before, "init-db grants the shared login the runtime memberships"
        # init-db's default privileges stay with the shared login and are reported, not moved.
        assert any(o.startswith("default privileges") for o in dry.acl_not_copied)

        with engine.begin() as conn:
            applied = transfer_ownership(conn, shared, migrator)
        assert set(applied.owned) == set(dry.owned)

        with engine.connect() as conn:
            owners_after = _owners(conn)
            assert _memberships(conn, migrator) >= memberships_before
            assert _memberships(conn, shared) == memberships_before
            rerun = read_transfer_plan(conn, shared, migrator)
    finally:
        engine.dispose()

    assert shared not in owners_after.values()
    for obj, owner in owners_before.items():
        assert owners_after.get(obj) == (migrator if owner == shared else owner), obj
    assert set(owners_before.values()) - {shared}, "purpose-owned objects must exist"
    assert rerun.owned == () and rerun.refusals == ()

    # Activation: init-db names the migrator as the connecting user, then it migrates.
    _replay_init_db(bootstrap_url, migrator)
    from butlers.migrations import run_migrations

    host, port = urlparse(db_url).hostname, urlparse(db_url).port
    migrator_url = f"postgresql://{quote(migrator, safe='')}:{password}@{host}:{port}/{db_name}"
    asyncio.run(run_migrations(migrator_url, chain="core"))
    asyncio.run(run_migrations(migrator_url, chain="switchboard", schema="switchboard"))
