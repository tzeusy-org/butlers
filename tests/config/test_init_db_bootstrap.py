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
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    if connecting_user is not None:
        env["PGOPTIONS"] = f"-c butlers.connecting_user={connecting_user}"
    return subprocess.run(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
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
        ],
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
    assert result["can_read_state_store"] is False, (
        "butler_chronicler_rw must NOT be able to SELECT education.state_store "
        "(not an approved RFC 0014 evidence surface — broad grants violate RFC 0014 §D1)"
    )
