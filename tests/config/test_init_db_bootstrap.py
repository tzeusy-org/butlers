"""Regression tests for the privileged init-db bootstrap script."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

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


def test_init_db_bootstrap_grants_connector_writer_switchboard_access(postgres_container):
    """connector_writer can access switchboard connector registry after bootstrap."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    migration_user_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # schema-standin-exempt: a two-column GRANT target, not a query
            # stand-in. This fixture runs before any migration, on purpose:
            # it tests init-db.sql's privilege boundary, and the registry's
            # real column list is irrelevant to that.
            conn.execute(
                text(
                    "CREATE TABLE switchboard.connector_registry ("
                    "  connector_type TEXT NOT NULL,"
                    "  endpoint_identity TEXT NOT NULL,"
                    "  PRIMARY KEY (connector_type, endpoint_identity)"
                    ")"
                )
            )
    finally:
        engine.dispose()

    butlers_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(butlers_url)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT "
                        "  has_schema_privilege('connector_writer', 'switchboard', 'USAGE') AS schema_usage,"
                        "  has_table_privilege("
                        "    'connector_writer',"
                        "    'switchboard.connector_registry',"
                        "    'SELECT,INSERT,UPDATE,DELETE'"
                        "  ) AS registry_dml"
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert row["schema_usage"] is True
    assert row["registry_dml"] is True


def test_init_db_bootstrap_repairs_connector_function_execute_grant(postgres_container):
    """bootstrap grants connector_writer EXECUTE on existing connectors functions."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            conn.execute(text("DROP ROLE IF EXISTS butlers"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    migration_user_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS connectors"))
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION connectors.test_connector_acl() "
                    "RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"
                )
            )
            conn.execute(text("REVOKE ALL ON FUNCTION connectors.test_connector_acl() FROM PUBLIC"))
            conn.execute(
                text("REVOKE ALL ON FUNCTION connectors.test_connector_acl() FROM connector_writer")
            )
    finally:
        engine.dispose()

    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    butlers_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(butlers_url)
    try:
        with engine.connect() as conn:
            can_execute = conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "  'connector_writer',"
                    "  'connectors.test_connector_acl()',"
                    "  'EXECUTE'"
                    ")"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert can_execute is True


def test_init_db_bootstrap_grants_relationship_read_access_to_switchboard_message_inbox(
    postgres_container,
):
    """relationship runtime role can read switchboard.message_inbox after bootstrap."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            conn.execute(text("DROP ROLE IF EXISTS butlers"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    migration_user_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE switchboard.message_inbox ("
                    "  id UUID PRIMARY KEY,"
                    "  direction TEXT NOT NULL,"
                    "  request_context JSONB NOT NULL DEFAULT '{}'::jsonb,"
                    "  received_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                    ")"
                )
            )
    finally:
        engine.dispose()

    butlers_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(butlers_url)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT "
                        "  has_schema_privilege('butler_relationship_rw', 'switchboard', 'USAGE') "
                        "    AS schema_usage,"
                        "  has_table_privilege("
                        "    'butler_relationship_rw',"
                        "    'switchboard.message_inbox',"
                        "    'SELECT'"
                        "  ) AS inbox_select"
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert row["schema_usage"] is True
    assert row["inbox_select"] is True


def test_init_db_bootstrap_repairs_membership_set_option_for_qa(postgres_container):
    """bootstrap repairs stale role membership so SET ROLE succeeds for QA."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            conn.execute(text("DROP ROLE IF EXISTS butler_qa_rw"))
            conn.execute(text("DROP ROLE IF EXISTS butlers"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE ROLE butler_qa_rw LOGIN"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    broken_membership_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(broken_membership_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("GRANT butler_qa_rw TO butlers WITH ADMIN TRUE"))
            conn.execute(text("GRANT butler_qa_rw TO butlers WITH INHERIT FALSE"))
            conn.execute(text("GRANT butler_qa_rw TO butlers WITH SET FALSE"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    migration_user_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT "
                        "  am.inherit_option,"
                        "  am.set_option "
                        "FROM pg_auth_members am "
                        "JOIN pg_roles role_r ON role_r.oid = am.roleid "
                        "JOIN pg_roles member_r ON member_r.oid = am.member "
                        "WHERE role_r.rolname = 'butler_qa_rw' "
                        "  AND member_r.rolname = 'butlers' "
                        "  AND am.set_option IS TRUE"
                    )
                )
                .mappings()
                .one()
            )
            conn.execute(text('SET ROLE "butler_qa_rw"'))
            current_user = conn.execute(text("SELECT current_user")).scalar_one()
            conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()

    assert row["inherit_option"] is True
    assert row["set_option"] is True
    assert current_user == "butler_qa_rw"


def test_init_db_bootstrap_normalizes_existing_normal_role_privileges(postgres_container):
    """Rerun strips every recovery-capable flag without changing normal role semantics."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    normal_roles = ("butlers", *_NORMAL_RUNTIME_ROLES)
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
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

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
            membership_rows = (
                conn.execute(
                    text(
                        """
                        SELECT role_r.rolname, member.inherit_option, member.set_option
                        FROM pg_auth_members AS member
                        JOIN pg_roles AS role_r ON role_r.oid = member.roleid
                        JOIN pg_roles AS member_r ON member_r.oid = member.member
                        WHERE member_r.rolname = 'butlers'
                          AND role_r.rolname = ANY(:runtime_roles)
                        ORDER BY role_r.rolname
                        """
                    ),
                    {"runtime_roles": list(_NORMAL_RUNTIME_ROLES)},
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    assert {row["rolname"] for row in role_rows} == set(normal_roles)
    for row in role_rows:
        assert dict(row) == {
            "rolname": row["rolname"],
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
        }
    assert [dict(row) for row in membership_rows] == [
        {"rolname": role, "inherit_option": True, "set_option": True}
        for role in sorted(_NORMAL_RUNTIME_ROLES)
    ]


def test_init_db_bootstrap_hardens_existing_optional_calendar_role(postgres_container):
    """An existing optional calendar login cannot retain restore-drill authority."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    calendar_password = "calendar-test-password"
    calendar_url = (
        f"postgresql://{_OPTIONAL_CALENDAR_RUNTIME_ROLE}:{calendar_password}@{host}:{port}/butlers"
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            conn.execute(text(f"DROP ROLE IF EXISTS {_OPTIONAL_CALENDAR_RUNTIME_ROLE}"))
            conn.execute(text("DROP ROLE IF EXISTS butlers"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    from butlers.migrations import run_migrations

    migration_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    asyncio.run(run_migrations(migration_url, chain="core"))

    control_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE ROLE butler_calendar_rw LOGIN SUPERUSER CREATEROLE "
                    "CREATEDB REPLICATION PASSWORD 'calendar-test-password'"
                )
            )
            conn.execute(text("GRANT USAGE ON SCHEMA calendar TO butler_calendar_rw"))
            for authority_role in _RESTORE_DRILL_AUTHORITY_ROLES:
                conn.execute(text(f"GRANT {authority_role} TO {_OPTIONAL_CALENDAR_RUNTIME_ROLE}"))
            conn.execute(text("GRANT USAGE ON SCHEMA restore_drill_executor TO butler_calendar_rw"))
            conn.execute(
                text(
                    "GRANT SELECT, INSERT ON TABLE "
                    "restore_drill_executor.restore_drill_results TO butler_calendar_rw"
                )
            )
            conn.execute(
                text(
                    "GRANT USAGE, SELECT, UPDATE ON SEQUENCE "
                    "restore_drill_executor.restore_drill_results_id_seq TO butler_calendar_rw"
                )
            )
    finally:
        engine.dispose()

    def membership_pairs(connection) -> set[tuple[str, str]]:
        rows = (
            connection.execute(
                text(
                    """
                    SELECT granted_role.rolname, member_role.rolname
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
                    JOIN pg_roles AS member_role ON member_role.oid = membership.member
                    WHERE (
                        granted_role.rolname = ANY(:authority_roles)
                        AND member_role.rolname = :calendar_role
                    ) OR (
                        granted_role.rolname = :calendar_role
                        AND member_role.rolname = ANY(:authority_roles)
                    )
                    """
                ),
                {
                    "authority_roles": list(_RESTORE_DRILL_AUTHORITY_ROLES),
                    "calendar_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
                },
            )
            .tuples()
            .all()
        )
        return set(rows)

    def calendar_role_attributes(connection) -> dict[str, bool]:
        return dict(
            connection.execute(
                text(
                    """
                    SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication
                    FROM pg_roles
                    WHERE rolname = :calendar_role
                    """
                ),
                {"calendar_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE},
            )
            .mappings()
            .one()
        )

    def direct_calendar_effective_privileges() -> dict[str, bool | str]:
        calendar_engine = create_engine(calendar_url)
        try:
            with calendar_engine.connect() as conn:
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

    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            assert calendar_role_attributes(conn) == {
                "rolsuper": True,
                "rolcreaterole": True,
                "rolcreatedb": True,
                "rolreplication": True,
            }
            assert membership_pairs(conn) == {
                (authority_role, _OPTIONAL_CALENDAR_RUNTIME_ROLE)
                for authority_role in _RESTORE_DRILL_AUTHORITY_ROLES
            }
    finally:
        engine.dispose()
    assert direct_calendar_effective_privileges() == {
        "current_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        "is_superuser": True,
        "can_create_role": True,
        "can_create_database": True,
        "can_replicate": True,
        "schema_usage": True,
        "calendar_schema_usage": True,
        "ledger_select": True,
        "ledger_insert": True,
        "ledger_sequence_usage": True,
        "ledger_sequence_select": True,
        "ledger_sequence_update": True,
        "has_executor_membership": True,
        "has_owner_membership": True,
        "has_audit_writer_membership": True,
        "executor_has_calendar_membership": False,
        "owner_has_calendar_membership": False,
        "audit_writer_has_calendar_membership": False,
    }

    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            assert calendar_role_attributes(conn) == {
                "rolsuper": False,
                "rolcreaterole": False,
                "rolcreatedb": False,
                "rolreplication": False,
            }
            assert membership_pairs(conn) == set()
            has_migration_membership = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
                        JOIN pg_roles AS member_role ON member_role.oid = membership.member
                        WHERE granted_role.rolname = :calendar_role
                          AND member_role.rolname = 'butlers'
                    )
                    """
                ),
                {"calendar_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE},
            ).scalar_one()
    finally:
        engine.dispose()

    assert has_migration_membership is False
    # The optional calendar role intentionally receives no persistent CONNECT
    # grant from init-db. This control-only harness grant makes its direct
    # LOGIN identity observable after SUPERUSER removal without changing the
    # production calendar ACL or membership topology.
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("GRANT CONNECT ON DATABASE butlers TO butler_calendar_rw"))
    finally:
        engine.dispose()
    assert direct_calendar_effective_privileges() == {
        "current_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        "is_superuser": False,
        "can_create_role": False,
        "can_create_database": False,
        "can_replicate": False,
        "schema_usage": False,
        "calendar_schema_usage": True,
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

    # PostgreSQL rejects reciprocal memberships as a cycle, so exercise the
    # inverse authority direction after the first rerun has removed the grants
    # above. This proves both executor-is-member-of-calendar and
    # calendar-is-member-of-executor repair paths without inventing a topology
    # PostgreSQL cannot represent.
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for authority_role in _RESTORE_DRILL_AUTHORITY_ROLES:
                conn.execute(text(f"GRANT {_OPTIONAL_CALENDAR_RUNTIME_ROLE} TO {authority_role}"))
    finally:
        engine.dispose()

    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            assert membership_pairs(conn) == {
                (_OPTIONAL_CALENDAR_RUNTIME_ROLE, authority_role)
                for authority_role in _RESTORE_DRILL_AUTHORITY_ROLES
            }
    finally:
        engine.dispose()
    assert direct_calendar_effective_privileges() == {
        "current_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        "is_superuser": False,
        "can_create_role": False,
        "can_create_database": False,
        "can_replicate": False,
        "schema_usage": False,
        "calendar_schema_usage": True,
        "ledger_select": False,
        "ledger_insert": False,
        "ledger_sequence_usage": False,
        "ledger_sequence_select": False,
        "ledger_sequence_update": False,
        "has_executor_membership": False,
        "has_owner_membership": False,
        "has_audit_writer_membership": False,
        "executor_has_calendar_membership": True,
        "owner_has_calendar_membership": True,
        "audit_writer_has_calendar_membership": True,
    }

    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    engine = create_engine(control_url)
    try:
        with engine.connect() as conn:
            assert membership_pairs(conn) == set()
    finally:
        engine.dispose()
    assert direct_calendar_effective_privileges() == {
        "current_role": _OPTIONAL_CALENDAR_RUNTIME_ROLE,
        "is_superuser": False,
        "can_create_role": False,
        "can_create_database": False,
        "can_replicate": False,
        "schema_usage": False,
        "calendar_schema_usage": True,
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
    assert (
        "restore-drill admin bootstrap cannot run as the shared migration role" in completed.stderr
    )


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

    assert "restore-drill admin bootstrap requires a cluster superuser" in completed.stderr


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
    assert "restore-drill admin bootstrap requires a cluster superuser" in completed.stderr


def test_chronicler_rw_reads_sessions_but_not_other_tables(postgres_container):
    """butler_chronicler_rw can SELECT sessions but not other butler schema tables.

    RFC 0014 §D1: Chronicler LLM sessions read only named evidence surfaces.
    The bootstrap must NOT grant SELECT ON ALL TABLES — only specific tables.
    """
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS butlers"))
            conn.execute(text("DROP ROLE IF EXISTS butlers"))
            conn.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'butlers'"))
            conn.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    # Create representative tables in two butler schemas as the migration user.
    migration_user_url = f"postgresql://butlers:butlers@{host}:{port}/butlers"
    engine = create_engine(migration_user_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # education.sessions — approved evidence surface
            conn.execute(
                text(
                    "CREATE TABLE education.sessions ("
                    "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                    "  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                    "  completed_at TIMESTAMPTZ,"
                    "  trigger_source TEXT,"
                    "  success BOOLEAN,"
                    "  request_id TEXT,"
                    "  duration_ms BIGINT,"
                    "  model TEXT"
                    ")"
                )
            )
            # education.state_store — NOT an approved evidence surface
            conn.execute(
                text(
                    "CREATE TABLE education.state_store ("
                    "  key TEXT PRIMARY KEY,"
                    "  value JSONB NOT NULL DEFAULT '{}'::jsonb"
                    ")"
                )
            )
            # general.calendar_event_instances — approved evidence surface
            conn.execute(
                text(
                    "CREATE TABLE general.calendar_event_instances ("
                    "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                    "  event_id UUID,"
                    "  source_id UUID,"
                    "  origin_instance_ref TEXT,"
                    "  starts_at TIMESTAMPTZ,"
                    "  ends_at TIMESTAMPTZ,"
                    "  status TEXT,"
                    "  timezone TEXT,"
                    "  metadata JSONB,"
                    "  updated_at TIMESTAMPTZ"
                    ")"
                )
            )
            # The completed-instance projection also reads these join
            # companions; each is an approved Chronicler read surface.
            conn.execute(
                text(
                    "CREATE TABLE general.calendar_sources ("
                    "  id UUID PRIMARY KEY DEFAULT gen_random_uuid()"
                    ")"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE general.calendar_events ("
                    "  id UUID PRIMARY KEY DEFAULT gen_random_uuid()"
                    ")"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE general.calendar_event_entities ("
                    "  event_id UUID NOT NULL,"
                    "  entity_id UUID NOT NULL"
                    ")"
                )
            )
    finally:
        engine.dispose()

    # Re-run init-db.sql so the existing tables receive their specific grants.
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=script_path,
    )

    butlers_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
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
