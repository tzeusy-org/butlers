"""Regression tests for the privileged init-db bootstrap script."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.bootstrap_prerequisite import prepare_bootstrap_connection
from butlers.db_authority.roles import AuthorityError, bootstrap_role_manifest, load_runtime_profile
from butlers.migrations import finalize_authority, migration_session, run_migrations

docker_available = shutil.which("docker") is not None
psql_available = shutil.which("psql") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.skipif(not psql_available, reason="psql not available"),
]

_NORMAL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)
_OPTIONAL_CALENDAR_RUNTIME_ROLE = "butler_calendar_rw"
_RESTORE_DRILL_AUTHORITY_ROLES = (
    "restore_drill_executor",
    "restore_drill_executor_owner",
    "restore_drill_executor_audit_writer",
)
_SCRIPT_EXTENSIONS = ("pgcrypto", "pg_trgm", "uuid-ossp", "vector")


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


def _admin_params(postgres_container) -> tuple[str, str, str, str]:
    return (
        postgres_container.get_container_host_ip(),
        str(postgres_container.get_exposed_port(5432)),
        postgres_container.username,
        postgres_container.password,
    )


def _run_psql_file(
    *,
    host: str,
    port: str,
    user: str,
    password: str,
    database: str,
    file_path: Path,
    connecting_user: str | None = None,
    on_error_stop: bool = True,
    no_psqlrc: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    # Legacy asserted GUC is an adversarial input in the refusal cases only;
    # successful preparation always uses the real source compiler above.
    if connecting_user is not None:
        env["PGOPTIONS"] = f"-c butlers.connecting_user={connecting_user}"
    command = ["psql"]
    if no_psqlrc:
        command.append("-X")
    if on_error_stop:
        command.extend(["-v", "ON_ERROR_STOP=1"])
    command.extend(
        [
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            database,
            "-f",
            str(file_path),
        ]
    )
    return subprocess.run(
        command,
        check=check,
        env=env,
        capture_output=True,
        text=True,
    )


def _fresh_database(postgres_container):
    """Provision only the distinct normal owner; production prepares capabilities."""
    host, port, user, password = _admin_params(postgres_container)
    admin = create_engine(
        f"postgresql://{user}:{password}@{host}:{port}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE IF EXISTS butlers"))
            connection.execute(text("DROP ROLE IF EXISTS butlers"))
            connection.execute(
                text(
                    "CREATE ROLE butlers LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE "
                    "NOCREATEDB NOREPLICATION PASSWORD 'butlers'"
                )
            )
            connection.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        admin.dispose()
    return (
        f"postgresql://butlers:butlers@{host}:{port}/butlers",
        f"postgresql://{user}:{password}@{host}:{port}/butlers",
    )


def _prepare(control_url):
    """Call the source compiler/script, never an old connecting-user GUC."""
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    raw = None
    try:
        raw = engine.raw_connection()
        raw.autocommit = True
        prepare_bootstrap_connection(raw, "butlers", "butlers")
    finally:
        try:
            if raw is not None:
                raw.close()
        finally:
            engine.dispose()


def _migrate_selected(migration_url, control_url, plans):
    """One production maintenance session owns all selected chains and finalization."""
    from butlers.testing.migration import _upgrade_chain_to_revision

    with migration_session(control_url, source_database="butlers") as connection:
        # Real historical shared-table transitions must finish in each selected
        # core schema before the first chain retires those shared tables.
        core_schemas = [schema for chain, schema, _ in plans if chain == "core"]
        if len(core_schemas) > 1:
            for schema in core_schemas:
                _upgrade_chain_to_revision(
                    migration_url,
                    chain="core",
                    schema=schema,
                    revision="core_114",
                    connection=connection,
                )
        for chain, schema, revision in plans:
            if revision is None:
                asyncio.run(
                    run_migrations(
                        migration_url,
                        chain=chain,
                        schema=schema,
                        connection=connection,
                    )
                )
            else:
                _upgrade_chain_to_revision(
                    migration_url,
                    chain=chain,
                    schema=schema,
                    revision=revision,
                    connection=connection,
                )
        finalize_authority(connection, source_database="butlers", plans=tuple(plans))


@contextmanager
def _control(control_url):
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _role_scalar(control_url, role, sql):
    with _control(control_url) as connection:
        connection.execute(text('SET ROLE "' + role.replace('"', '""') + '"'))
        try:
            return connection.execute(text(sql)).scalar_one()
        finally:
            connection.execute(text("RESET ROLE"))


def test_init_db_bootstrap_grants_connector_writer_switchboard_access(postgres_container):
    """Exact source registry DML is finalized after its real producer exists."""
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    plans = (("core", "switchboard", None), ("switchboard", "switchboard", None))
    _migrate_selected(migration_url, control_url, plans)
    with _control(control_url) as connection:
        row = (
            connection.execute(
                text(
                    "SELECT has_schema_privilege('connector_writer','switchboard','USAGE') "
                    "AS schema_usage, has_table_privilege('connector_writer', "
                    "'switchboard.connector_registry','SELECT,INSERT,UPDATE') AS registry_dml"
                )
            )
            .mappings()
            .one()
        )
    assert row["schema_usage"] is True
    assert row["registry_dml"] is True
    assert (
        _role_scalar(
            control_url, "connector_writer", "SELECT count(*) FROM switchboard.connector_registry"
        )
        == 0
    )

    async def checkpoint_roundtrip():
        import asyncpg
        from sqlalchemy.engine import make_url

        from butlers.connectors.cursor_store import (
            load_connector_settings,
            load_cursor,
            save_connector_settings,
            save_cursor,
        )
        from butlers.db import register_jsonb_codec

        async def setup(connection):
            await connection.execute("SET ROLE connector_writer")

        native_url = (
            make_url(control_url).set(drivername="postgresql").render_as_string(hide_password=False)
        )
        pool = await asyncpg.create_pool(
            native_url, min_size=1, max_size=1, init=register_jsonb_codec, setup=setup
        )
        try:
            for value in ("first-checkpoint", "second-checkpoint"):
                await save_cursor(
                    pool, "test", "acl-checkpoint", value, parent_endpoint_identity=None
                )
            assert await load_cursor(pool, "test", "acl-checkpoint") == "second-checkpoint"
            assert await save_connector_settings(pool, "test", "acl-checkpoint", {"first": 1}) == {
                "first": 1
            }
            assert await save_connector_settings(pool, "test", "acl-checkpoint", {"second": 2}) == {
                "first": 1,
                "second": 2,
            }
            assert await load_connector_settings(pool, "test", "acl-checkpoint") == {
                "first": 1,
                "second": 2,
            }
            before = await pool.fetchrow(
                "SELECT checkpoint_cursor,settings FROM switchboard.connector_registry "
                "WHERE connector_type='test' AND endpoint_identity='acl-checkpoint'"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as error:
                await pool.execute(
                    "DELETE FROM switchboard.connector_registry "
                    "WHERE connector_type='test' AND endpoint_identity='acl-checkpoint'"
                )
            assert error.value.sqlstate == "42501"
        finally:
            await pool.close()
        # Independent administrative physical connection sees the committed row
        # unchanged after the denied DELETE, rather than a driver/cache echo.
        observer = await asyncpg.connect(native_url)
        try:
            await register_jsonb_codec(observer)
            after = await observer.fetchrow(
                "SELECT checkpoint_cursor,settings FROM switchboard.connector_registry "
                "WHERE connector_type='test' AND endpoint_identity='acl-checkpoint'"
            )
            assert dict(after) == dict(before)
        finally:
            await observer.close()

    asyncio.run(checkpoint_roundtrip())


def test_init_db_bootstrap_repairs_connector_function_execute_grant(postgres_container):
    """A fixed partition producer works; an arbitrary function never gains EXECUTE."""
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    _migrate_selected(migration_url, control_url, (("core", None, None),))
    engine = create_engine(migration_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION connectors.test_connector_acl() RETURNS integer "
                    "LANGUAGE sql AS $$ SELECT 1 $$"
                )
            )
            connection.execute(
                text("REVOKE ALL ON FUNCTION connectors.test_connector_acl() FROM PUBLIC")
            )
    finally:
        engine.dispose()
    _prepare(control_url)
    with _control(control_url) as connection:
        can_execute = connection.execute(
            text(
                "SELECT has_function_privilege('connector_writer', "
                "'connectors.test_connector_acl()', 'EXECUTE')"
            )
        ).scalar_one()
    assert can_execute is False
    with pytest.raises(ProgrammingError):
        _role_scalar(control_url, "connector_writer", "SELECT connectors.test_connector_acl()")
    with migration_session(control_url, source_database="butlers") as connection:
        with pytest.raises(AuthorityError, match="not declared"):
            finalize_authority(connection, plans=(("core", None, None),))
    # Refusal cannot replace the separate positive fixed-function contract.
    assert (
        _role_scalar(
            control_url,
            "connector_writer",
            "SELECT connectors.connectors_filtered_events_ensure_partition('2099-03-15'::timestamptz)",
        )
        == "filtered_events_209903"
    )


def test_init_db_bootstrap_grants_relationship_read_access_to_switchboard_message_inbox(
    postgres_container,
):
    """RFC0010 named inbox read survives without future switchboard SELECT."""
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    plans = (("core", "switchboard", None), ("switchboard", "switchboard", None))
    _migrate_selected(migration_url, control_url, plans)
    with _control(control_url) as connection:
        row = (
            connection.execute(
                text(
                    "SELECT has_schema_privilege('butler_relationship_rw','switchboard','USAGE') "
                    "AS schema_usage, has_table_privilege('butler_relationship_rw', "
                    "'switchboard.message_inbox','SELECT') AS inbox_select"
                )
            )
            .mappings()
            .one()
        )
    assert row["schema_usage"] is True
    assert row["inbox_select"] is True
    assert (
        _role_scalar(
            control_url, "butler_relationship_rw", "SELECT count(*) FROM switchboard.message_inbox"
        )
        == 0
    )
    engine = create_engine(migration_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.future_read_probe (id integer)"))
    finally:
        engine.dispose()
    _prepare(control_url)
    with pytest.raises(ProgrammingError):
        _role_scalar(
            control_url,
            "butler_relationship_rw",
            "SELECT count(*) FROM switchboard.future_read_probe",
        )


def test_init_db_bootstrap_repairs_membership_set_option_for_qa(postgres_container):
    """Bootstrap no longer makes the ordinary migrator a serving-role member."""
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    with _control(control_url) as connection:
        row = (
            connection.execute(
                text(
                    "SELECT EXISTS(SELECT FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member "
                    "WHERE r.rolname='butler_qa_rw' AND u.rolname='butlers') AS membership, "
                    "(SELECT rolcanlogin FROM pg_roles WHERE rolname='butler_qa_rw') AS login"
                )
            )
            .mappings()
            .one()
        )
    assert row["membership"] is False
    assert row["login"] is False
    assert _role_scalar(control_url, "butler_qa_rw", "SELECT current_user") == "butler_qa_rw"
    engine = create_engine(migration_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(ProgrammingError):
                connection.execute(text("SET ROLE butler_qa_rw"))
    finally:
        engine.dispose()


def test_init_db_bootstrap_normalizes_existing_normal_role_privileges(postgres_container):
    """Declared capabilities are NOLOGIN/non-inheriting; normal owner stays distinct."""
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    manifest = bootstrap_role_manifest("butlers", "butlers", source_database="butlers")
    with _control(control_url) as connection:
        for role in _NORMAL_RUNTIME_ROLES:
            # Adversarial existing-role input, not a fixture grant of application authority.
            connection.execute(
                text(
                    f'ALTER ROLE "{role}" LOGIN INHERIT SUPERUSER CREATEROLE '
                    "CREATEDB REPLICATION BYPASSRLS"
                )
            )
    _prepare(control_url)
    with _control(control_url) as connection:
        role_rows = (
            connection.execute(
                text(
                    "SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreaterole,rolcreatedb,"
                    "rolreplication,rolbypassrls "
                    "FROM pg_roles WHERE rolname=ANY(:roles) ORDER BY rolname"
                ),
                {"roles": ["butlers", *manifest["roles"]]},
            )
            .mappings()
            .all()
        )
        membership_rows = (
            connection.execute(
                text(
                    "SELECT role.rolname,m.inherit_option,m.set_option FROM pg_auth_members m "
                    "JOIN pg_roles role ON role.oid=m.roleid JOIN pg_roles member ON member.oid=m.member "
                    "WHERE member.rolname='butlers' AND role.rolname=ANY(:roles)"
                ),
                {"roles": manifest["roles"]},
            )
            .mappings()
            .all()
        )
    assert {row["rolname"] for row in role_rows} == {"butlers", *manifest["roles"]}
    for row in role_rows:
        assert dict(row) == {
            "rolname": row["rolname"],
            "rolcanlogin": row["rolname"] == "butlers",
            "rolinherit": False,
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
            "rolbypassrls": False,
        }
    assert [dict(row) for row in membership_rows] == []


def test_init_db_bootstrap_hardens_existing_optional_calendar_role(postgres_container):
    """An unlisted calendar principal cannot become a source-owned runtime.

    Normalizing/adopting an optional LOGIN and restoring its calendar access
    is retired. The full effective restore-ledger/sequence/membership refusal
    matrix remains against real migrated objects and an explicit SET ROLE.
    """
    migration_url, control_url = _fresh_database(postgres_container)
    _prepare(control_url)
    _migrate_selected(migration_url, control_url, (("core", None, None),))
    manifest = bootstrap_role_manifest("butlers", "butlers", source_database="butlers")
    assert _OPTIONAL_CALENDAR_RUNTIME_ROLE not in manifest["roles"]
    with pytest.raises(AuthorityError, match="not declared"):
        load_runtime_profile("butlers", "calendar", _OPTIONAL_CALENDAR_RUNTIME_ROLE)
    with _control(control_url) as connection:
        connection.execute(
            text(
                "CREATE ROLE butler_calendar_rw NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS"
            )
        )

    def direct_calendar_effective_privileges() -> dict[str, bool | str]:
        calendar_engine = create_engine(control_url)
        try:
            with calendar_engine.connect() as conn:
                conn.execute(text('SET LOCAL ROLE "butler_calendar_rw"'))
                return dict(
                    conn.execute(
                        text(
                            """
                            SELECT
                                current_user AS current_role,
                                (
                                    SELECT rolsuper
                                    FROM pg_roles
                                    WHERE rolname = current_user
                                ) AS is_superuser,
                                (
                                    SELECT rolcreaterole
                                    FROM pg_roles
                                    WHERE rolname = current_user
                                ) AS can_create_role,
                                (
                                    SELECT rolcreatedb
                                    FROM pg_roles
                                    WHERE rolname = current_user
                                ) AS can_create_database,
                                (
                                    SELECT rolreplication
                                    FROM pg_roles
                                    WHERE rolname = current_user
                                ) AS can_replicate,
                                has_schema_privilege(
                                    current_user,
                                    'restore_drill_executor',
                                    'USAGE'
                                ) AS schema_usage,
                                has_schema_privilege(
                                    current_user,
                                    'calendar',
                                    'USAGE'
                                ) AS calendar_schema_usage,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'SELECT'
                                ) AS ledger_select,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'INSERT'
                                ) AS ledger_insert,
                                has_sequence_privilege(
                                    current_user,
                                    (
                                        SELECT sequence.oid
                                        FROM pg_catalog.pg_class AS sequence
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = sequence.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND sequence.relname = 'restore_drill_results_id_seq'
                                          AND sequence.relkind = 'S'
                                    ),
                                    'USAGE'
                                ) AS ledger_sequence_usage,
                                has_sequence_privilege(
                                    current_user,
                                    (
                                        SELECT sequence.oid
                                        FROM pg_catalog.pg_class AS sequence
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = sequence.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND sequence.relname = 'restore_drill_results_id_seq'
                                          AND sequence.relkind = 'S'
                                    ),
                                    'SELECT'
                                ) AS ledger_sequence_select,
                                has_sequence_privilege(
                                    current_user,
                                    (
                                        SELECT sequence.oid
                                        FROM pg_catalog.pg_class AS sequence
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = sequence.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND sequence.relname = 'restore_drill_results_id_seq'
                                          AND sequence.relkind = 'S'
                                    ),
                                    'UPDATE'
                                ) AS ledger_sequence_update,
                                pg_has_role(
                                    current_user,
                                    'restore_drill_executor',
                                    'MEMBER'
                                ) AS has_executor_membership,
                                pg_has_role(
                                    current_user,
                                    'restore_drill_executor_owner',
                                    'MEMBER'
                                ) AS has_owner_membership,
                                pg_has_role(
                                    current_user,
                                    'restore_drill_executor_audit_writer',
                                    'MEMBER'
                                ) AS has_audit_writer_membership,
                                pg_has_role(
                                    'restore_drill_executor',
                                    current_user,
                                    'MEMBER'
                                ) AS executor_has_calendar_membership,
                                pg_has_role(
                                    'restore_drill_executor_owner',
                                    current_user,
                                    'MEMBER'
                                ) AS owner_has_calendar_membership,
                                pg_has_role(
                                    'restore_drill_executor_audit_writer',
                                    current_user,
                                    'MEMBER'
                                ) AS audit_writer_has_calendar_membership
                            """
                        )
                    )
                    .mappings()
                    .one()
                )
        finally:
            calendar_engine.dispose()

    expected = {
        "current_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        "is_superuser": False,
        "can_create_role": False,
        "can_create_database": False,
        "can_replicate": False,
        "schema_usage": False,
        "calendar_schema_usage": False,
        "ledger_select": False,
        "ledger_insert": False,
        "ledger_sequence_usage": False,
        "ledger_sequence_select": False,
        "ledger_sequence_update": False,
        "has_executor_membership": False,
        "has_owner_membership": False,
        "has_audit_writer_membership": False,
        "executor_has_calendar_membership": False,
        "owner_has_calendar_membership": False,
        "audit_writer_has_calendar_membership": False,
    }
    assert direct_calendar_effective_privileges() == expected
    for _ in range(2):
        # Replaying production preparation neither adds this role to the
        # source profile nor restores any old broad/calendar/restore grant.
        _prepare(control_url)
        with pytest.raises(AuthorityError, match="not declared"):
            load_runtime_profile("butlers", "calendar", _OPTIONAL_CALENDAR_RUNTIME_ROLE)
        with _control(control_url) as connection:
            attributes = dict(
                connection.execute(
                    text(
                        "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,"
                        "rolcanlogin,rolinherit,rolbypassrls FROM pg_roles "
                        "WHERE rolname='butler_calendar_rw'"
                    )
                )
                .mappings()
                .one()
            )
            memberships = connection.execute(
                text(
                    "SELECT count(*) FROM pg_auth_members WHERE member="
                    "(SELECT oid FROM pg_roles WHERE rolname='butler_calendar_rw') OR roleid="
                    "(SELECT oid FROM pg_roles WHERE rolname='butler_calendar_rw')"
                )
            ).scalar_one()
            has_migration_membership = connection.execute(
                text("SELECT pg_has_role('butlers','butler_calendar_rw','MEMBER')")
            ).scalar_one()
        assert attributes == dict.fromkeys(
            (
                "rolsuper",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolcanlogin",
                "rolinherit",
                "rolbypassrls",
            ),
            False,
        )
        assert memberships == 0
        assert has_migration_membership is False
        assert direct_calendar_effective_privileges() == expected
        with pytest.raises(ProgrammingError) as denied:
            _role_scalar(
                control_url,
                _OPTIONAL_CALENDAR_RUNTIME_ROLE,
                "SELECT count(*) FROM restore_drill_executor.restore_drill_results",
            )
        assert denied.value.orig.pgcode == "42501"


def test_init_db_rejects_migration_user_bootstrap_before_normal_role_mutation(postgres_container):
    """A configured migration user cannot self-demote while running privileged bootstrap."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    normal_roles = ("butlers", *_NORMAL_RUNTIME_ROLES)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            for role in reversed(normal_roles):
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
            conn.execute(
                text(
                    "CREATE ROLE butlers LOGIN SUPERUSER CREATEROLE CREATEDB REPLICATION "
                    "PASSWORD 'butlers'"
                )
            )
            for role in _NORMAL_RUNTIME_ROLES:
                conn.execute(
                    text(f"CREATE ROLE {role} LOGIN SUPERUSER CREATEROLE CREATEDB REPLICATION")
                )
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    completed = _run_psql_file(
        host=host,
        port=port,
        user="butlers",
        password="butlers",
        database="butlers",
        file_path=script_path,
        connecting_user="butlers",
        check=False,
    )

    assert completed.returncode != 0

    butlers_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(butlers_url)
    try:
        with engine.connect() as conn:
            role_rows = (
                conn.execute(
                    text(
                        """
                        SELECT
                            rolname,
                            rolcanlogin,
                            rolinherit,
                            rolsuper,
                            rolcreaterole,
                            rolcreatedb,
                            rolreplication
                        FROM pg_roles
                        WHERE rolname = ANY(:normal_roles)
                        ORDER BY rolname
                        """
                    ),
                    {"normal_roles": list(normal_roles)},
                )
                .mappings()
                .all()
            )
            membership_count = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_auth_members AS member
                    JOIN pg_roles AS member_r ON member_r.oid = member.member
                    WHERE member_r.rolname = 'butlers'
                      AND member.roleid = ANY(
                          SELECT oid FROM pg_roles WHERE rolname = ANY(:runtime_roles)
                      )
                    """
                ),
                {"runtime_roles": list(_NORMAL_RUNTIME_ROLES)},
            ).scalar_one()
            installed_script_extensions = (
                conn.execute(
                    text(
                        """
                    SELECT extname
                    FROM pg_extension
                    WHERE extname = ANY(:extension_names)
                    ORDER BY extname
                    """
                    ),
                    {"extension_names": ["pgcrypto", "pg_trgm", "uuid-ossp", "vector"]},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert [dict(row) for row in role_rows] == [
        {
            "rolname": role,
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": True,
            "rolcreaterole": True,
            "rolcreatedb": True,
            "rolreplication": True,
        }
        for role in sorted(normal_roles)
    ]
    assert membership_count == 0
    assert installed_script_extensions == []
    assert "source-compiled deployment manifest required" in completed.stderr


def test_init_db_rejects_distinct_non_superuser_owner_before_any_mutation(postgres_container):
    """A database owner without cluster superuser cannot mutate managed catalog state."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    owner_role = "init_db_unprivileged_owner"
    managed_roles = (
        owner_role,
        "butlers",
        _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        *_RESTORE_DRILL_AUTHORITY_ROLES,
        *_NORMAL_RUNTIME_ROLES,
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            for role in managed_roles:
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(
                text(
                    "CREATE ROLE init_db_unprivileged_owner LOGIN "
                    "NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION "
                    "PASSWORD 'unprivileged-owner'"
                )
            )
            conn.execute(text("CREATE DATABASE butlers OWNER init_db_unprivileged_owner"))
    finally:
        engine.dispose()

    control_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"

    def catalog_state(connection) -> dict[str, object]:
        """Read init-db-managed non-secret catalog surfaces."""
        return {
            "roles": [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT oid, rolname, rolsuper, rolinherit, rolcreaterole,
                               rolcreatedb, rolcanlogin, rolreplication, rolbypassrls,
                               rolconnlimit, rolvaliduntil::text AS rolvaliduntil,
                               COALESCE(rolconfig::text, '') AS rolconfig
                        FROM pg_roles
                        ORDER BY oid
                        """
                    )
                )
                .mappings()
                .all()
            ],
            "memberships": [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT roleid, member, grantor, admin_option,
                               inherit_option, set_option
                        FROM pg_auth_members
                        ORDER BY roleid, member
                        """
                    )
                )
                .mappings()
                .all()
            ],
            "schemas": [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT oid, nspname, nspowner, COALESCE(nspacl::text, '') AS nspacl
                        FROM pg_namespace
                        ORDER BY oid
                        """
                    )
                )
                .mappings()
                .all()
            ],
            "database_acl": dict(
                connection.execute(
                    text(
                        """
                        SELECT oid, datname, datdba, datallowconn, datconnlimit,
                               COALESCE(datacl::text, '') AS datacl
                        FROM pg_database
                        WHERE datname = current_database()
                        """
                    )
                )
                .mappings()
                .one()
            ),
            "database_privileges": dict(
                connection.execute(
                    text(
                        """
                        SELECT
                            has_database_privilege('butlers', current_database(), 'CONNECT')
                                AS migration_connect,
                            has_database_privilege('butlers', current_database(), 'CREATE')
                                AS migration_create,
                            has_database_privilege('butlers', current_database(), 'TEMPORARY')
                                AS migration_temporary,
                            has_schema_privilege('butlers', 'public', 'USAGE')
                                AS public_usage,
                            has_schema_privilege('butlers', 'public', 'CREATE')
                                AS public_create
                        """
                    )
                )
                .mappings()
                .one()
            ),
            "default_acls": [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT defaclrole, defaclnamespace, defaclobjtype,
                               COALESCE(defaclacl::text, '') AS defaclacl
                        FROM pg_default_acl
                        ORDER BY defaclrole, defaclnamespace, defaclobjtype
                        """
                    )
                )
                .mappings()
                .all()
            ],
            "extensions": [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT extname, extowner, extnamespace, extrelocatable,
                               extversion, COALESCE(extconfig::text, '') AS extconfig,
                               COALESCE(extcondition::text, '') AS extcondition
                        FROM pg_extension
                        ORDER BY extname
                        """
                    )
                )
                .mappings()
                .all()
            ],
        }

    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE butlers FROM PUBLIC"))
            conn.execute(text("REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE butlers FROM butlers"))
            conn.execute(text("REVOKE ALL PRIVILEGES ON SCHEMA public FROM butlers"))
            baseline = catalog_state(conn)
            assert baseline["database_privileges"] == {
                "migration_connect": False,
                "migration_create": False,
                "migration_temporary": False,
                "public_usage": True,
                "public_create": False,
            }
            assert not {extension["extname"] for extension in baseline["extensions"]}.intersection(
                _SCRIPT_EXTENSIONS
            )

            # Demonstrate that an unexpected preflight grant is visible to this
            # managed catalog snapshot, then restore the exact baseline before
            # invoking the bootstrap under test.
            conn.execute(text("GRANT TEMPORARY ON DATABASE butlers TO butlers"))
            assert catalog_state(conn) != baseline
            conn.execute(text("REVOKE TEMPORARY ON DATABASE butlers FROM butlers"))
            assert catalog_state(conn) == baseline
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    completed = _run_psql_file(
        host=host,
        port=port,
        user=owner_role,
        password="unprivileged-owner",
        database="butlers",
        file_path=script_path,
        connecting_user="butlers",
        check=False,
    )

    assert completed.returncode != 0

    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            assert catalog_state(conn) == baseline
    finally:
        engine.dispose()

    assert "explicit privileged bootstrap identity required" in completed.stderr


def test_init_db_default_psql_stops_before_extension_mutation_after_preflight_error(
    postgres_container,
):
    """Default psql -f behavior must stop without user psqlrc state."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    owner_role = "init_db_default_psql_owner"
    managed_roles = (
        owner_role,
        "butlers",
        _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        *_RESTORE_DRILL_AUTHORITY_ROLES,
        *_NORMAL_RUNTIME_ROLES,
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            for role in managed_roles:
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(
                text(
                    "CREATE ROLE init_db_default_psql_owner LOGIN "
                    "NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION "
                    "PASSWORD 'unprivileged-owner'"
                )
            )
            conn.execute(text("CREATE DATABASE butlers OWNER init_db_default_psql_owner"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    completed = _run_psql_file(
        host=host,
        port=port,
        user=owner_role,
        password="unprivileged-owner",
        database="butlers",
        file_path=script_path,
        connecting_user="butlers",
        on_error_stop=False,
        no_psqlrc=True,
        check=False,
    )

    control_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            installed_script_extensions = (
                conn.execute(
                    text(
                        """
                        SELECT extname
                        FROM pg_extension
                        WHERE extname = ANY(:extension_names)
                        ORDER BY extname
                        """
                    ),
                    {"extension_names": list(_SCRIPT_EXTENSIONS)},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert "-X" in completed.args
    assert "-v" not in completed.args
    assert "ON_ERROR_STOP=1" not in completed.args
    assert completed.returncode != 0, (
        "default psql -f continued after the rejected bootstrap preflight; "
        f"exit code={completed.returncode}, extensions={installed_script_extensions}"
    )
    assert installed_script_extensions == []
    assert "explicit privileged bootstrap identity required" in completed.stderr


def test_chronicler_rw_reads_sessions_but_not_other_tables(postgres_container):
    """butler_chronicler_rw can SELECT sessions but not other butler schema tables.

    RFC 0014 §D1: Chronicler LLM sessions read only named evidence surfaces.
    The bootstrap must NOT grant SELECT ON ALL TABLES — only specific tables.
    """
    migration_user_url, butlers_url = _fresh_database(postgres_container)
    _prepare(butlers_url)
    _migrate_selected(
        migration_user_url,
        butlers_url,
        (
            ("core", "education", None),
            ("core", "general", None),
        ),
    )
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            # A genuinely undeclared future relation must not acquire a default grant.
            connection.execute(
                text(
                    "CREATE TABLE education.state_store (key TEXT PRIMARY KEY, "
                    "value JSONB NOT NULL DEFAULT '{}'::jsonb)"
                )
            )
    finally:
        engine.dispose()
    _prepare(butlers_url)

    engine = create_engine(butlers_url)
    try:
        with engine.connect() as conn:
            result = (
                conn.execute(
                    text(
                        "SELECT "
                        # Approved — sessions
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'education.sessions',"
                        "    'SELECT'"
                        "  ) AS can_read_sessions,"
                        # Approved — calendar_event_instances
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'general.calendar_event_instances',"
                        "    'SELECT'"
                        "  ) AS can_read_calendar,"
                        # Approved — calendar join companions
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'general.calendar_sources',"
                        "    'SELECT'"
                        "  ) AS can_read_calendar_sources,"
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'general.calendar_events',"
                        "    'SELECT'"
                        "  ) AS can_read_calendar_events,"
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'general.calendar_event_entities',"
                        "    'SELECT'"
                        "  ) AS can_read_calendar_event_entities,"
                        # Denied — state_store is not an evidence surface
                        "  has_table_privilege("
                        "    'butler_chronicler_rw',"
                        "    'education.state_store',"
                        "    'SELECT'"
                        "  ) AS can_read_state_store"
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert result["can_read_sessions"] is True, (
        "butler_chronicler_rw must be able to SELECT education.sessions "
        "(approved RFC 0014 evidence surface)"
    )
    assert result["can_read_calendar"] is True, (
        "butler_chronicler_rw must be able to SELECT general.calendar_event_instances "
        "(approved RFC 0014 evidence surface)"
    )
    for key, table in (
        ("can_read_calendar_sources", "general.calendar_sources"),
        ("can_read_calendar_events", "general.calendar_events"),
        ("can_read_calendar_event_entities", "general.calendar_event_entities"),
    ):
        assert result[key] is True, (
            f"butler_chronicler_rw must be able to SELECT {table} "
            "(approved RFC 0014 evidence surface)"
        )
    assert result["can_read_state_store"] is False, (
        "butler_chronicler_rw must NOT be able to SELECT education.state_store "
        "(not an approved RFC 0014 evidence surface — broad grants violate RFC 0014 §D1)"
    )

    async def actual_evidence_readers():
        import uuid
        from datetime import UTC, datetime

        import asyncpg
        from sqlalchemy.engine import make_url

        from butlers.chronicler.adapters import CalendarCompletedAdapter, CoreSessionsAdapter
        from butlers.db import register_jsonb_codec

        async def setup(connection):
            await connection.execute("SET ROLE butler_chronicler_rw")

        native_url = (
            make_url(butlers_url).set(drivername="postgresql").render_as_string(hide_password=False)
        )
        pool = await asyncpg.create_pool(
            native_url, min_size=1, max_size=1, init=register_jsonb_codec, setup=setup
        )
        try:
            sessions = CoreSessionsAdapter(("education",))
            assert await sessions._fetch_sessions(pool, "education", None) == ([], None)
            calendar = CalendarCompletedAdapter(("general",))
            assert await calendar._fetch_instances(pool, "general", None, datetime.now(UTC)) == []
            assert await calendar._fetch_event_entities(pool, "general", [uuid.uuid4()]) == {}
            for table in (
                "education.sessions",
                "general.calendar_event_instances",
                "general.calendar_sources",
                "general.calendar_events",
                "general.calendar_event_entities",
            ):
                assert await pool.fetch(f"SELECT * FROM {table} LIMIT 1") == []
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    # Even an empty match requires DELETE authority. No source
                    # data is changed by this negative proof.
                    await pool.execute(f"DELETE FROM {table} WHERE false")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetch("SELECT * FROM education.state_store")
        finally:
            await pool.close()

    asyncio.run(actual_evidence_readers())
