"""Disposable PostgreSQL proof for the restore-drill role boundary.

REQ-database-security-006 requires effective role ACLs, not static SQL alone.
This module bootstraps an isolated testcontainer, runs ``init-db.sql`` and the
real core migration chain as the shared migration login, then verifies the
executor-only database interface.  It deliberately does not create a scratch
database or invoke any restore client; that broader lifecycle proof belongs to
the later restore integration slice.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

docker_available = shutil.which("docker") is not None
psql_available = shutil.which("psql") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.skipif(not psql_available, reason="psql not available"),
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
_PROVISIONER = _REPO_ROOT / "scripts" / "provision_restore_drill_executor.sh"
_MIGRATION = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_196_restore_drill_executor_boundary.py"
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
    *, host: str, port: str, user: str, password: str, database: str, file_path: Path
) -> None:
    env = {**os.environ, "PGPASSWORD": password}
    subprocess.run(
        [
            "psql",
            "-X",
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


def _expect_permission_denied(engine, statement: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError):
        connection.execute(text(statement))


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_196", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_executor_boundary_migration(shared_url: str) -> None:
    """Run the production core_196 upgrade as the shared migration login.

    The full core chain intentionally supports deployments that add specialist
    schemas later. This focused role-boundary test supplies the already-migrated
    audit-log prerequisite and executes the target migration through a real
    Alembic operation context, rather than depending on unrelated specialist
    chain ordering.
    """
    migration = _load_migration()
    engine = create_engine(shared_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE public.audit_log (
                        id BIGSERIAL PRIMARY KEY,
                        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target TEXT,
                        result TEXT,
                        error TEXT,
                        metadata JSONB
                    )
                    """
                )
            )
            context = MigrationContext.configure(connection)
            real_op = Operations(context)
            with patch.object(migration, "op", real_op):
                migration.upgrade()
    finally:
        engine.dispose()


def test_bootstrap_and_migration_keep_the_executor_interface_exclusive(
    postgres_container, tmp_path: Path
) -> None:
    """REQ-database-security-006: shared credentials cannot become the executor."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    admin_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE ROLE butlers LOGIN PASSWORD 'shared-test-password'"))
            connection.execute(text("CREATE DATABASE butlers OWNER butlers"))
    finally:
        engine.dispose()

    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database="butlers",
        file_path=_INIT_DB,
    )

    shared_url = f"postgresql://butlers:shared-test-password@{host}:{port}/butlers"
    _apply_executor_boundary_migration(shared_url)

    password_file = tmp_path / "restore-drill-executor-password"
    password_file.write_text("executor-test-password", encoding="utf-8")
    provisioned = subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "PGHOST": host,
            "PGPORT": port,
            "PGUSER": admin_user,
            "PGPASSWORD": admin_password,
            "PGDATABASE": "butlers",
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
        },
        capture_output=True,
        text=True,
    )
    assert provisioned.returncode == 0, provisioned.stderr

    audit_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/butlers"
    audit_engine = create_engine(audit_url)
    try:
        with audit_engine.connect() as connection:
            roles = (
                connection.execute(
                    text(
                        """
                    SELECT rolname, rolcreatedb
                    FROM pg_roles
                    WHERE rolname = 'butlers'
                       OR rolname = 'connector_writer'
                       OR rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
                       OR rolname IN ('restore_drill_executor', 'restore_drill_executor_owner')
                    """
                    )
                )
                .mappings()
                .all()
            )
            acl = (
                connection.execute(
                    text(
                        """
                    SELECT
                        has_schema_privilege('restore_drill_executor', 'restore_drill_executor', 'USAGE')
                            AS executor_schema_usage,
                        has_schema_privilege('butlers', 'restore_drill_executor', 'USAGE')
                            AS shared_schema_usage,
                        has_function_privilege(
                            'restore_drill_executor',
                            'restore_drill_executor.is_due(integer)',
                            'EXECUTE'
                        ) AS executor_execute,
                        has_function_privilege(
                            'butlers',
                            'restore_drill_executor.is_due(integer)',
                            'EXECUTE'
                        ) AS shared_execute,
                        has_schema_privilege(
                            'butlers',
                            'restore_drill_executor_admin',
                            'USAGE'
                        ) AS shared_finalizer_schema_usage,
                        has_function_privilege(
                            'butlers',
                            'restore_drill_executor_admin.finalize_interface()',
                            'EXECUTE'
                        ) AS shared_finalizer_execute,
                        pg_has_role(
                            'butlers',
                            'restore_drill_executor_owner',
                            'USAGE'
                        ) AS shared_owner_membership,
                        (
                            SELECT pg_get_userbyid(p.proowner)
                            FROM pg_proc p
                            JOIN pg_namespace n ON n.oid = p.pronamespace
                            WHERE n.nspname = 'restore_drill_executor'
                              AND p.proname = 'is_due'
                        ) AS function_owner
                    """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        audit_engine.dispose()

    role_flags = {row["rolname"]: row["rolcreatedb"] for row in roles}
    assert role_flags["restore_drill_executor"] is True
    assert role_flags["restore_drill_executor_owner"] is False
    assert all(
        role_flags[name] is False for name in role_flags if name not in {"restore_drill_executor"}
    )
    assert acl == {
        "executor_schema_usage": True,
        "shared_schema_usage": False,
        "executor_execute": True,
        "shared_execute": False,
        "shared_finalizer_schema_usage": False,
        "shared_finalizer_execute": False,
        "shared_owner_membership": False,
        "function_owner": "restore_drill_executor_owner",
    }

    shared_engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    executor_url = (
        f"postgresql://restore_drill_executor:executor-test-password@{host}:{port}/butlers"
    )
    executor_engine = create_engine(executor_url, isolation_level="AUTOCOMMIT")
    try:
        _expect_permission_denied(shared_engine, "CREATE DATABASE forbidden_shared_restore_drill")
        _expect_permission_denied(
            shared_engine,
            "SELECT restore_drill_executor.is_due(604800)",
        )
        _expect_permission_denied(
            executor_engine,
            "SELECT count(*) FROM public.audit_log",
        )
        _expect_permission_denied(
            executor_engine,
            "CREATE TABLE restore_drill_executor.forbidden_direct_table (id integer)",
        )
        with executor_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT restore_drill_executor.is_due(604800)")
                ).scalar_one()
                is True
            )
    finally:
        shared_engine.dispose()
        executor_engine.dispose()
