"""Regression tests for the privileged init-db bootstrap script."""

from __future__ import annotations

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
) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    subprocess.run(
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
        check=True,
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
