"""Disposable PostgreSQL proof for the restore-drill role boundary.

REQ-database-security-006 requires effective role ACLs, not static SQL alone.
This module bootstraps an isolated testcontainer, runs ``init-db.sql`` and the
real ``core@head`` migration chain as the normal shared migration login, then
checks the resulting effective ACL matrix. It deliberately does not create a
scratch database or invoke restore clients; that broader lifecycle proof is a
later restore-integration slice.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from alembic import command
from butlers.jobs.backup_health import get_last_restore_drill
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import bootstrap_extensions, migration_db_name

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

_SHARED_PASSWORD = "shared-restore-drill-test-password"
_BUTLER_PASSWORD = "butler-restore-drill-test-password"
_CONNECTOR_PASSWORD = "connector-restore-drill-test-password"
_PUBLIC_PASSWORD = "public-restore-drill-test-password"
_EXECUTOR_PASSWORD = "executor-restore-drill-test-password"
_ALTERNATE_BOOTSTRAP_ROLE = "restore_drill_alternate_bootstrap"
_ALTERNATE_BOOTSTRAP_PASSWORD = "alternate-restore-drill-test-password"
_PUBLIC_PROBE_ROLE = "restore_drill_public_probe"
_NORMAL_ROLE_PASSWORDS = {
    "butlers": _SHARED_PASSWORD,
    "butler_general_rw": _BUTLER_PASSWORD,
    "connector_writer": _CONNECTOR_PASSWORD,
    _PUBLIC_PROBE_ROLE: _PUBLIC_PASSWORD,
}


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


def _url(*, user: str, password: str, host: str, port: str, database: str) -> str:
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}"
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
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text(statement))


def _create_database_before_restore_bootstrap(
    postgres_container,
) -> tuple[str, str, str, str, str, str]:
    """Create a disposable database before its managed restore bootstrap."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    database = migration_db_name()
    admin_url = _url(
        user=admin_user,
        password=admin_password,
        host=host,
        port=port,
        database=database,
    )
    shared_url = _url(
        user="butlers",
        password=_SHARED_PASSWORD,
        host=host,
        port=port,
        database=database,
    )
    control_url = _url(
        user=admin_user,
        password=admin_password,
        host=host,
        port=port,
        database="postgres",
    )
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butlers') THEN
                            CREATE ROLE butlers LOGIN PASSWORD 'shared-restore-drill-test-password';
                        END IF;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text("ALTER ROLE butlers LOGIN PASSWORD :password"), {"password": _SHARED_PASSWORD}
            )
            connection.execute(text(f'CREATE DATABASE "{database}" OWNER butlers'))
    finally:
        engine.dispose()

    bootstrap_extensions(admin_url)
    return admin_url, shared_url, host, port, admin_user, admin_password


def _bootstrap_database(postgres_container) -> tuple[str, str, str, str, str, str]:
    """Create a disposable database and run the managed bootstrap as admin.

    The return values are ``admin_url, shared_url, host, port, admin_user,
    admin_password``. The full core chain is intentionally not run here so the
    invalid-secret regression can prove the provisioner transaction alone.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = (
        _create_database_before_restore_bootstrap(postgres_container)
    )
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=_database_from_url(admin_url),
        file_path=_INIT_DB,
    )
    return admin_url, shared_url, host, port, admin_user, admin_password


def _create_alternate_bootstrap_superuser(admin_url: str) -> None:
    """Create the distinct trusted principal used by rerun provenance tests."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_alternate_bootstrap'
                        ) THEN
                            CREATE ROLE restore_drill_alternate_bootstrap LOGIN SUPERUSER;
                        END IF;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    "ALTER ROLE restore_drill_alternate_bootstrap LOGIN SUPERUSER PASSWORD :password"
                ),
                {"password": _ALTERNATE_BOOTSTRAP_PASSWORD},
            )
    finally:
        engine.dispose()


def _configure_direct_acl_subjects(admin_url: str) -> None:
    """Give disposable direct-login test roles passwords without widening ACLs."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text("ALTER ROLE butler_general_rw LOGIN PASSWORD :password"),
                {"password": _BUTLER_PASSWORD},
            )
            connection.execute(
                text("ALTER ROLE connector_writer LOGIN PASSWORD :password"),
                {"password": _CONNECTOR_PASSWORD},
            )
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_public_probe'
                        ) THEN
                            CREATE ROLE restore_drill_public_probe LOGIN NOINHERIT
                                NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION
                                PASSWORD 'public-restore-drill-test-password';
                        END IF;
                    END;
                    $$
                    """
                ),
            )
            connection.execute(
                text("ALTER ROLE restore_drill_public_probe LOGIN NOCREATEDB PASSWORD :password"),
                {"password": _PUBLIC_PASSWORD},
            )
            database = _database_from_url(admin_url)
            connection.execute(
                text(f'GRANT CONNECT ON DATABASE "{database}" TO restore_drill_public_probe')
            )
    finally:
        engine.dispose()


def _run_real_core_chain_with_relationship_prerequisite(shared_url: str) -> None:
    """Migrate the actual core chain through its cross-chain prerequisite.

    ``core_150`` grants access to the relationship fact table. The real
    relationship migration itself references core's entity table, so a fresh
    database must reach core_122, migrate the relationship chain, then finish
    ``core@head``. No table or target migration is hand-created here.
    """
    config = _build_alembic_config(shared_url, chains=["core"])
    command.upgrade(config, "core_122")
    asyncio.run(run_migrations(shared_url, chain="relationship", schema="relationship"))
    asyncio.run(run_migrations(shared_url, chain="core"))


def _run_core_chain_through_restore_drill_predecessor(shared_url: str) -> None:
    """Reach the real core_195 state without applying core_196 itself."""
    config = _build_alembic_config(shared_url, chains=["core"])
    command.upgrade(config, "core_122")
    asyncio.run(run_migrations(shared_url, chain="relationship", schema="relationship"))
    command.upgrade(config, "core_195")


def _provision_executor(
    *,
    host: str,
    port: str,
    admin_user: str,
    admin_password: str,
    database: str,
    password_file: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "PGHOST": host,
            "PGPORT": port,
            "PGUSER": admin_user,
            "PGPASSWORD": admin_password,
            "PGDATABASE": database,
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
        },
        capture_output=True,
        text=True,
    )


def _database_from_url(url: str) -> str:
    return url.rsplit("/", 1)[1]


async def _read_restore_drill_api_shape(database_url: str) -> dict[str, object] | None:
    """Exercise the real dashboard ledger reader against the disposable core chain."""
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    try:
        return await get_last_restore_drill(pool)
    finally:
        await pool.close()


def _grant_legacy_shared_authority_staging(admin_url: str) -> None:
    """Model the pre-fix grants that let the shared login poison core_196."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text("GRANT USAGE, CREATE ON SCHEMA restore_drill_executor TO butlers")
            )
            connection.execute(
                text("GRANT USAGE ON SCHEMA restore_drill_executor_admin TO butlers")
            )
            connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    "restore_drill_executor_admin.finalize_interface() TO butlers"
                )
            )
    finally:
        engine.dispose()


def _precreate_untrusted_admin_bootstrap(shared_url: str) -> None:
    """Model a database-owner shared login poisoning the admin bootstrap schema."""
    engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE SCHEMA restore_drill_executor_admin"))
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor_admin.finalize_interface()
                    RETURNS void
                    LANGUAGE plpgsql
                    SECURITY DEFINER
                    SET search_path = pg_catalog
                    AS $$
                    BEGIN
                        RETURN;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor_admin.install_interface()
                    RETURNS void
                    LANGUAGE plpgsql
                    SECURITY DEFINER
                    SET search_path = pg_catalog
                    AS $$
                    BEGIN
                        RETURN;
                    END;
                    $$
                    """
                )
            )
    finally:
        engine.dispose()


def _seed_untrusted_admin_migration_prerequisites(admin_url: str) -> None:
    """Supply only the role/schema prerequisites, never a trusted bootstrap."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_executor'
                        ) THEN
                            CREATE ROLE restore_drill_executor
                                NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB;
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_executor_owner'
                        ) THEN
                            CREATE ROLE restore_drill_executor_owner
                                NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB;
                        END IF;
                    END;
                    $$;
                    CREATE SCHEMA restore_drill_executor
                        AUTHORIZATION restore_drill_executor_owner;
                    GRANT USAGE ON SCHEMA restore_drill_executor_admin TO butlers;
                    GRANT EXECUTE ON FUNCTION
                        restore_drill_executor_admin.install_interface() TO butlers;
                    """
                )
            )
    finally:
        engine.dispose()


def _precreate_shared_authority_interface(shared_url: str) -> None:
    """Create a compatible relation, trigger, and canonical signatures as ``butlers``."""
    engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE restore_drill_executor.restore_drill_results (
                        id BIGSERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                        result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
                        detail TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor.precreated_result_trigger()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        RETURN NEW;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER precreated_result_trigger
                    BEFORE INSERT ON restore_drill_executor.restore_drill_results
                    FOR EACH ROW
                    EXECUTE FUNCTION restore_drill_executor.precreated_result_trigger()
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor.is_due(p_interval_seconds INTEGER)
                    RETURNS BOOLEAN
                    LANGUAGE sql
                    AS $$
                        SELECT false
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor.record_result(
                        p_backup_name TEXT,
                        p_result TEXT,
                        p_detail TEXT,
                        p_table_count INTEGER
                    )
                    RETURNS BIGINT
                    LANGUAGE sql
                    AS $$
                        SELECT 1::bigint
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor.latest_result()
                    RETURNS TABLE (
                        checked_at TIMESTAMPTZ,
                        result TEXT,
                        detail TEXT
                    )
                    LANGUAGE sql
                    AS $$
                        SELECT NULL::timestamptz, NULL::text, NULL::text WHERE false
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    REVOKE ALL ON FUNCTION restore_drill_executor.is_due(INTEGER) FROM PUBLIC;
                    REVOKE ALL ON FUNCTION restore_drill_executor.record_result(
                        TEXT, TEXT, TEXT, INTEGER
                    ) FROM PUBLIC;
                    REVOKE ALL ON FUNCTION restore_drill_executor.latest_result() FROM PUBLIC;
                    """
                )
            )
    finally:
        engine.dispose()


def _read_shared_authority_handoff_state(admin_url: str) -> dict[str, object]:
    """Return authority ownership and effective grants after a poison attempt."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        """
                        SELECT
                            (
                                SELECT pg_get_userbyid(result_ledger.relowner)
                                FROM pg_class AS result_ledger
                                JOIN pg_namespace AS result_schema
                                    ON result_schema.oid = result_ledger.relnamespace
                                WHERE result_schema.nspname = 'restore_drill_executor'
                                  AND result_ledger.relname = 'restore_drill_results'
                            ) AS ledger_owner,
                            (
                                SELECT pg_get_userbyid(interface_function.proowner)
                                FROM pg_proc AS interface_function
                                WHERE interface_function.oid =
                                    'restore_drill_executor.is_due(integer)'::regprocedure
                            ) AS is_due_owner,
                            (
                                SELECT pg_get_userbyid(interface_function.proowner)
                                FROM pg_proc AS interface_function
                                WHERE interface_function.oid =
                                    'restore_drill_executor.record_result(text,text,text,integer)'
                                    ::regprocedure
                            ) AS record_result_owner,
                            (
                                SELECT pg_get_userbyid(interface_function.proowner)
                                FROM pg_proc AS interface_function
                                WHERE interface_function.oid =
                                    'restore_drill_executor.latest_result()'::regprocedure
                            ) AS latest_result_owner,
                            EXISTS (
                                SELECT 1
                                FROM pg_trigger AS trigger_row
                                JOIN pg_class AS result_ledger
                                    ON result_ledger.oid = trigger_row.tgrelid
                                JOIN pg_namespace AS result_schema
                                    ON result_schema.oid = result_ledger.relnamespace
                                WHERE result_schema.nspname = 'restore_drill_executor'
                                  AND result_ledger.relname = 'restore_drill_results'
                                  AND trigger_row.tgname = 'precreated_result_trigger'
                                  AND NOT trigger_row.tgisinternal
                            ) AS poison_trigger_exists,
                            has_function_privilege(
                                'restore_drill_executor',
                                'restore_drill_executor.is_due(integer)',
                                'EXECUTE'
                            ) AS executor_is_due_execute,
                            has_function_privilege(
                                'restore_drill_executor',
                                'restore_drill_executor.record_result(text,text,text,integer)',
                                'EXECUTE'
                            ) AS executor_record_result_execute,
                            has_function_privilege(
                                'restore_drill_executor',
                                'restore_drill_executor.latest_result()',
                                'EXECUTE'
                            ) AS executor_latest_result_execute,
                            has_schema_privilege(
                                'butlers', 'restore_drill_executor', 'CREATE'
                            ) AS shared_schema_create,
                            has_schema_privilege(
                                'butlers', 'restore_drill_executor', 'USAGE'
                            ) AS shared_schema_usage,
                            has_function_privilege(
                                'butlers',
                                'restore_drill_executor_admin.finalize_interface()',
                                'EXECUTE'
                            ) AS shared_finalizer_execute,
                            has_table_privilege(
                                'restore_drill_executor_owner', 'public.audit_log', 'INSERT'
                            ) AS owner_audit_insert
                        """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()


def test_untrusted_admin_bootstrap_cannot_supply_a_noop_core_196_installer(
    postgres_container,
) -> None:
    """REQ-database-security-006: admin bootstrap provenance is fail-closed.

    The shared login owns this disposable database, as it can on a fresh
    deployment before the privileged bootstrap runs. It pre-creates the admin
    schema plus both zero-argument SECURITY DEFINER signatures, then calls the
    no-op installer directly. A core_196 attempt must reject that untrusted
    provenance instead of accepting its signature, and privileged bootstrap
    reruns must refuse to reclaim it silently.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = (
        _create_database_before_restore_bootstrap(postgres_container)
    )
    _precreate_untrusted_admin_bootstrap(shared_url)
    _seed_untrusted_admin_migration_prerequisites(admin_url)

    shared_engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with shared_engine.connect() as connection:
            connection.execute(text("SELECT restore_drill_executor_admin.install_interface()"))
            connection.execute(text("SELECT restore_drill_executor_admin.finalize_interface()"))
    finally:
        shared_engine.dispose()

    config = _build_alembic_config(shared_url, chains=["core"])
    command.stamp(config, "core_195")
    with pytest.raises(
        Exception, match="restore-drill bootstrap installer is missing or untrusted"
    ):
        command.upgrade(config, "core_196")

    for _ in range(2):
        with pytest.raises(subprocess.CalledProcessError) as bootstrap_error:
            _run_psql_file(
                host=host,
                port=port,
                user=admin_user,
                password=admin_password,
                database=_database_from_url(admin_url),
                file_path=_INIT_DB,
            )
        assert "restore-drill admin schema is not owned by a trusted bootstrap superuser" in (
            bootstrap_error.value.stderr
        )

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            state = (
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(admin_schema.nspowner) AS admin_schema_owner,
                            pg_get_userbyid(installer.proowner) AS installer_owner,
                            pg_get_userbyid(finalizer.proowner) AS finalizer_owner,
                            to_regclass('restore_drill_executor.restore_drill_results')
                                IS NOT NULL AS trusted_ledger_exists,
                            COALESCE(
                                has_function_privilege(
                                    'restore_drill_executor',
                                    to_regprocedure('restore_drill_executor.is_due(integer)'),
                                    'EXECUTE'
                                ),
                                false
                            ) AS executor_due_execute
                        FROM pg_namespace AS admin_schema
                        JOIN pg_proc AS installer
                            ON installer.pronamespace = admin_schema.oid
                           AND installer.proname = 'install_interface'
                           AND installer.pronargs = 0
                        JOIN pg_proc AS finalizer
                            ON finalizer.pronamespace = admin_schema.oid
                           AND finalizer.proname = 'finalize_interface'
                           AND finalizer.pronargs = 0
                        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
                        """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert state == {
        "admin_schema_owner": "butlers",
        "installer_owner": "butlers",
        "finalizer_owner": "butlers",
        "trusted_ledger_exists": False,
        "executor_due_execute": False,
    }


def test_privileged_rerun_preserves_existing_admin_interface_owner(
    postgres_container,
) -> None:
    """REQ-database-security-006: trusted reruns retain core_196 provenance.

    A prior privileged bootstrap can have created the trusted admin schema
    before an interruption. A retry through a different cluster superuser must
    create the retained admin objects under that existing schema owner,
    otherwise core_196 fails closed despite safe provenance.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = (
        _create_database_before_restore_bootstrap(postgres_container)
    )
    _create_alternate_bootstrap_superuser(admin_url)
    assert admin_user != _ALTERNATE_BOOTSTRAP_ROLE
    database = _database_from_url(admin_url)

    alternate_engine = create_engine(
        _url(
            user=_ALTERNATE_BOOTSTRAP_ROLE,
            password=_ALTERNATE_BOOTSTRAP_PASSWORD,
            host=host,
            port=port,
            database=database,
        ),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with alternate_engine.connect() as connection:
            connection.execute(text("CREATE SCHEMA restore_drill_executor_admin"))
    finally:
        alternate_engine.dispose()

    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=database,
        file_path=_INIT_DB,
    )

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            admin_interface = (
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(admin_schema.nspowner) AS schema_owner,
                            pg_get_userbyid(bootstrap_configuration.relowner)
                                AS bootstrap_configuration_owner,
                            pg_get_userbyid(installer.proowner) AS installer_owner,
                            pg_get_userbyid(finalizer.proowner) AS finalizer_owner
                        FROM pg_namespace AS admin_schema
                        JOIN pg_class AS bootstrap_configuration
                            ON bootstrap_configuration.relnamespace = admin_schema.oid
                           AND bootstrap_configuration.relname = 'bootstrap_configuration'
                           AND bootstrap_configuration.relkind = 'r'
                        JOIN pg_proc AS installer
                            ON installer.pronamespace = admin_schema.oid
                           AND installer.proname = 'install_interface'
                           AND installer.pronargs = 0
                        JOIN pg_proc AS finalizer
                            ON finalizer.pronamespace = admin_schema.oid
                           AND finalizer.proname = 'finalize_interface'
                           AND finalizer.pronargs = 0
                        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
                        """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert dict(admin_interface) == {
        "schema_owner": _ALTERNATE_BOOTSTRAP_ROLE,
        "bootstrap_configuration_owner": _ALTERNATE_BOOTSTRAP_ROLE,
        "installer_owner": _ALTERNATE_BOOTSTRAP_ROLE,
        "finalizer_owner": _ALTERNATE_BOOTSTRAP_ROLE,
    }

    _run_core_chain_through_restore_drill_predecessor(shared_url)
    command.upgrade(_build_alembic_config(shared_url, chains=["core"]), "core_196")


def test_shared_authority_poison_cannot_be_finalized_or_reblessed_on_bootstrap_rerun(
    postgres_container,
) -> None:
    """REQ-database-security-006: stale shared grants cannot bless arbitrary DDL.

    This models a database that retains the old shared ``CREATE`` and finalizer
    ``EXECUTE`` grants across an upgrade.  The shared login creates every
    canonical object plus a trigger, attempts the finalizer directly, then a
    privileged ``init-db.sql`` rerun.  Neither route may transfer ownership or
    grant executor capabilities to the attacker-owned interface.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _run_core_chain_through_restore_drill_predecessor(shared_url)
    _grant_legacy_shared_authority_staging(admin_url)
    _precreate_shared_authority_interface(shared_url)

    shared_engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with (
            shared_engine.connect() as connection,
            pytest.raises(
                DBAPIError,
                match="restore-drill interface ownership is untrusted",
            ),
        ):
            connection.execute(text("SELECT restore_drill_executor_admin.finalize_interface()"))
    finally:
        shared_engine.dispose()

    with pytest.raises(subprocess.CalledProcessError, match="init-db.sql") as rerun_error:
        _run_psql_file(
            host=host,
            port=port,
            user=admin_user,
            password=admin_password,
            database=_database_from_url(admin_url),
            file_path=_INIT_DB,
        )
    assert "restore-drill interface ownership is untrusted" in rerun_error.value.stderr

    assert _read_shared_authority_handoff_state(admin_url) == {
        "ledger_owner": "butlers",
        "is_due_owner": "butlers",
        "record_result_owner": "butlers",
        "latest_result_owner": "butlers",
        "poison_trigger_exists": True,
        "executor_is_due_execute": False,
        "executor_record_result_execute": False,
        "executor_latest_result_execute": False,
        "shared_schema_create": False,
        "shared_schema_usage": False,
        "shared_finalizer_execute": False,
        "owner_audit_insert": False,
    }


def test_precreated_shared_ledger_rejects_core_migration_without_authority_handoff(
    postgres_container,
) -> None:
    """REQ-database-security-006: a shared-role precreation is never trusted.

    A deployment upgraded from the old staging design can retain shared
    protected-schema CREATE. A compatible table and trigger created through
    that legacy grant must make the fixed installer fail closed, rather than
    letting an ownership finalizer bless an attacker-controlled relation. The
    failed revision must leave its trusted interface absent; after an
    administrator removes the untrusted objects in this disposable database,
    retrying the revision remains valid.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _grant_legacy_shared_authority_staging(admin_url)

    shared_engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with shared_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE restore_drill_executor.restore_drill_results (
                        id BIGSERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                        result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
                        detail TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION restore_drill_executor.precreated_result_trigger()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        RETURN NEW;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER precreated_result_trigger
                    BEFORE INSERT ON restore_drill_executor.restore_drill_results
                    FOR EACH ROW
                    EXECUTE FUNCTION restore_drill_executor.precreated_result_trigger()
                    """
                )
            )
    finally:
        shared_engine.dispose()

    with pytest.raises(
        Exception,
        match="restore-drill authority interface must be absent before fixed bootstrap installation",
    ):
        _run_real_core_chain_with_relationship_prerequisite(shared_url)

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            failed_state = (
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(result_ledger.relowner) AS ledger_owner,
                            EXISTS (
                                SELECT 1
                                FROM pg_trigger trigger_row
                                WHERE trigger_row.tgrelid = result_ledger.oid
                                  AND trigger_row.tgname = 'precreated_result_trigger'
                                  AND NOT trigger_row.tgisinternal
                            ) AS poison_trigger_exists,
                            to_regprocedure('restore_drill_executor.is_due(integer)')
                                IS NOT NULL AS is_due_exists,
                            to_regprocedure(
                                'restore_drill_executor.record_result(text,text,text,integer)'
                            ) IS NOT NULL AS record_result_exists,
                            to_regprocedure('restore_drill_executor.latest_result()')
                                IS NOT NULL AS latest_result_exists
                        FROM pg_class AS result_ledger
                        JOIN pg_namespace AS result_schema
                            ON result_schema.oid = result_ledger.relnamespace
                        WHERE result_schema.nspname = 'restore_drill_executor'
                          AND result_ledger.relname = 'restore_drill_results'
                        """
                    )
                )
                .mappings()
                .one()
            )
            assert failed_state == {
                "ledger_owner": "butlers",
                "poison_trigger_exists": True,
                "is_due_exists": False,
                "record_result_exists": False,
                "latest_result_exists": False,
            }

            # The protected migration must not absorb a relation or trigger
            # created by the shared role.  Removing the explicitly untrusted
            # disposable objects restores the ordinary retry path.
            connection.execute(text("DROP TABLE restore_drill_executor.restore_drill_results"))
            connection.execute(
                text("DROP FUNCTION restore_drill_executor.precreated_result_trigger()")
            )
    finally:
        engine.dispose()

    asyncio.run(run_migrations(shared_url, chain="core"))

    # A clean owner-finalized interface survives an idempotent privileged
    # bootstrap rerun; this is distinct from the poisoned rerun above, which
    # must fail before the handoff.
    _run_psql_file(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=_database_from_url(admin_url),
        file_path=_INIT_DB,
    )

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            repaired_state = (
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(result_ledger.relowner) AS ledger_owner,
                            to_regprocedure('restore_drill_executor.is_due(integer)')
                                IS NOT NULL AS is_due_exists,
                            to_regprocedure(
                                'restore_drill_executor.record_result(text,text,text,integer)'
                            ) IS NOT NULL AS record_result_exists,
                            to_regprocedure('restore_drill_executor.latest_result()')
                                IS NOT NULL AS latest_result_exists
                        FROM pg_class AS result_ledger
                        JOIN pg_namespace AS result_schema
                            ON result_schema.oid = result_ledger.relnamespace
                        WHERE result_schema.nspname = 'restore_drill_executor'
                          AND result_ledger.relname = 'restore_drill_results'
                        """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert repaired_state == {
        "ledger_owner": "restore_drill_executor_owner",
        "is_due_exists": True,
        "record_result_exists": True,
        "latest_result_exists": True,
    }


def test_real_core_chain_keeps_the_executor_result_authority_exclusive(
    postgres_container, tmp_path: Path
) -> None:
    """REQ-database-security-006: only the executor can write trusted results.

    This is intentionally a true core-chain proof: no hand-created audit table,
    patched Alembic operation, or mock ownership transfer can make a stale ACL
    look correct. ``public.audit_log`` remains broad-DML telemetry, so a
    restore-drill-shaped public row must never become the scheduling or API
    authority.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _configure_direct_acl_subjects(admin_url)
    _run_real_core_chain_with_relationship_prerequisite(shared_url)

    password_file = tmp_path / "restore-drill-executor-password"
    # One terminal LF is intentional: it exercises the same contract enforced
    # by the executor process and prevents drift between bootstrap and runtime.
    password_file.write_text(_EXECUTOR_PASSWORD + "\n", encoding="utf-8")
    provisioned = _provision_executor(
        host=host,
        port=port,
        admin_user=admin_user,
        admin_password=admin_password,
        database=_database_from_url(admin_url),
        password_file=password_file,
    )
    assert provisioned.returncode == 0, provisioned.stderr

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            roles = (
                connection.execute(
                    text(
                        """
                    SELECT rolname, rolcanlogin, rolcreatedb
                    FROM pg_roles
                    WHERE rolname IN (
                        'butlers',
                        'butler_general_rw',
                        'connector_writer',
                        'restore_drill_public_probe',
                        'restore_drill_executor',
                        'restore_drill_executor_owner',
                        'restore_drill_executor_audit_writer'
                    )
                    """
                    )
                )
                .mappings()
                .all()
            )
            acl_rows = (
                connection.execute(
                    text(
                        """
                    WITH subjects(subject) AS (
                        VALUES
                            ('butlers'::name),
                            ('butler_general_rw'::name),
                            ('connector_writer'::name),
                            ('restore_drill_public_probe'::name),
                            ('restore_drill_executor'::name),
                            ('restore_drill_executor_audit_writer'::name)
                    )
                    SELECT
                        subject::text AS subject,
                        has_schema_privilege(
                            subject, 'restore_drill_executor', 'USAGE'
                        ) AS schema_usage,
                        has_function_privilege(
                            subject, 'restore_drill_executor.is_due(integer)', 'EXECUTE'
                        ) AS is_due_execute,
                        has_function_privilege(
                            subject,
                            'restore_drill_executor.record_result(text,text,text,integer)',
                            'EXECUTE'
                        ) AS record_result_execute,
                        has_function_privilege(
                            subject,
                            'restore_drill_executor.latest_result()',
                            'EXECUTE'
                        ) AS latest_result_execute,
                        has_table_privilege(
                            subject,
                            'restore_drill_executor.restore_drill_results',
                            'SELECT'
                        ) AS ledger_select,
                        has_table_privilege(
                            subject,
                            'restore_drill_executor.restore_drill_results',
                            'INSERT'
                        ) AS ledger_insert,
                        pg_has_role(subject, 'restore_drill_executor_owner', 'USAGE')
                            AS owner_membership
                    FROM subjects
                    ORDER BY subject::text
                    """
                    )
                )
                .mappings()
                .all()
            )
            function_owners = (
                connection.execute(
                    text(
                        """
                    SELECT p.proname, pg_get_userbyid(p.proowner) AS owner
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'restore_drill_executor'
                      AND p.proname IN ('is_due', 'latest_result', 'record_result')
                    ORDER BY p.proname
                    """
                    )
                )
                .mappings()
                .all()
            )
            audit_writer_boundary = (
                connection.execute(
                    text(
                        """
                    SELECT
                        pg_get_userbyid(projection_writer.proowner) AS function_owner,
                        projection_writer.prosecdef AS security_definer,
                        array_to_string(projection_writer.proconfig, ',') AS function_config,
                        has_schema_privilege(
                            'restore_drill_executor_audit_writer',
                            'restore_drill_executor',
                            'USAGE'
                        ) AS private_schema_usage,
                        has_table_privilege(
                            'restore_drill_executor_audit_writer',
                            'restore_drill_executor.restore_drill_results',
                            'INSERT'
                        ) AS ledger_insert,
                        has_function_privilege(
                            'restore_drill_executor_audit_writer',
                            'restore_drill_executor.latest_result()',
                            'EXECUTE'
                        ) AS latest_result_execute,
                        has_schema_privilege(
                            'restore_drill_executor_audit_writer', 'public', 'CREATE'
                        ) AS public_schema_create,
                        has_table_privilege(
                            'restore_drill_executor_audit_writer', 'public.audit_log', 'INSERT'
                        ) AS audit_insert,
                        has_table_privilege(
                            'restore_drill_executor_owner', 'public.audit_log', 'INSERT'
                        ) AS private_owner_audit_insert,
                        pg_has_role(
                            'restore_drill_executor_audit_writer',
                            'restore_drill_executor_owner',
                            'USAGE'
                        ) AS private_owner_membership
                    FROM pg_proc AS projection_writer
                    WHERE projection_writer.oid =
                        'restore_drill_executor_admin.write_audit_projection(text)'::regprocedure
                    """
                    )
                )
                .mappings()
                .one()
            )
            admin_bootstrap_boundary = (
                connection.execute(
                    text(
                        """
                    SELECT
                        pg_get_userbyid(admin_schema.nspowner) AS schema_owner,
                        bootstrap_owner.rolsuper AS schema_owner_is_superuser,
                        pg_get_userbyid(installer.proowner) AS installer_owner,
                        installer.prosecdef AS installer_security_definer,
                        array_to_string(installer.proconfig, ',') AS installer_config,
                        pg_get_userbyid(finalizer.proowner) AS finalizer_owner,
                        finalizer.prosecdef AS finalizer_security_definer,
                        array_to_string(finalizer.proconfig, ',') AS finalizer_config,
                        has_schema_privilege(
                            'butlers', 'restore_drill_executor_admin', 'USAGE'
                        ) AS shared_admin_schema_usage,
                        has_function_privilege(
                            'butlers',
                            'restore_drill_executor_admin.install_interface()',
                            'EXECUTE'
                        ) AS shared_installer_execute,
                        has_function_privilege(
                            'butlers',
                            'restore_drill_executor_admin.finalize_interface()',
                            'EXECUTE'
                        ) AS shared_finalizer_execute
                    FROM pg_namespace AS admin_schema
                    JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
                    JOIN pg_proc AS installer
                        ON installer.pronamespace = admin_schema.oid
                       AND installer.proname = 'install_interface'
                       AND installer.pronargs = 0
                    JOIN pg_proc AS finalizer
                        ON finalizer.pronamespace = admin_schema.oid
                       AND finalizer.proname = 'finalize_interface'
                       AND finalizer.pronargs = 0
                    WHERE admin_schema.nspname = 'restore_drill_executor_admin'
                    """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    role_flags = {row["rolname"]: row for row in roles}
    assert role_flags["restore_drill_executor"] == {
        "rolname": "restore_drill_executor",
        "rolcanlogin": True,
        "rolcreatedb": True,
    }
    assert role_flags["restore_drill_executor_owner"] == {
        "rolname": "restore_drill_executor_owner",
        "rolcanlogin": False,
        "rolcreatedb": False,
    }
    assert role_flags["restore_drill_executor_audit_writer"] == {
        "rolname": "restore_drill_executor_audit_writer",
        "rolcanlogin": False,
        "rolcreatedb": False,
    }
    for role_name in _NORMAL_ROLE_PASSWORDS:
        assert role_flags[role_name]["rolcreatedb"] is False

    acl = {row["subject"]: row for row in acl_rows}
    assert acl["butlers"] == {
        "subject": "butlers",
        "schema_usage": True,
        "is_due_execute": False,
        "record_result_execute": False,
        "latest_result_execute": True,
        "ledger_select": False,
        "ledger_insert": False,
        "owner_membership": False,
    }
    for role_name in ("butler_general_rw", "connector_writer", _PUBLIC_PROBE_ROLE):
        assert acl[role_name] == {
            "subject": role_name,
            "schema_usage": False,
            "is_due_execute": False,
            "record_result_execute": False,
            "latest_result_execute": False,
            "ledger_select": False,
            "ledger_insert": False,
            "owner_membership": False,
        }
    assert acl["restore_drill_executor"] == {
        "subject": "restore_drill_executor",
        "schema_usage": True,
        "is_due_execute": True,
        "record_result_execute": True,
        "latest_result_execute": False,
        "ledger_select": False,
        "ledger_insert": False,
        "owner_membership": False,
    }
    assert acl["restore_drill_executor_audit_writer"] == {
        "subject": "restore_drill_executor_audit_writer",
        "schema_usage": False,
        "is_due_execute": False,
        "record_result_execute": False,
        "latest_result_execute": False,
        "ledger_select": False,
        "ledger_insert": False,
        "owner_membership": False,
    }
    assert function_owners == [
        {"proname": "is_due", "owner": "restore_drill_executor_owner"},
        {"proname": "latest_result", "owner": "restore_drill_executor_owner"},
        {"proname": "record_result", "owner": "restore_drill_executor_owner"},
    ]
    assert dict(audit_writer_boundary) == {
        "function_owner": "restore_drill_executor_audit_writer",
        "security_definer": True,
        "function_config": "search_path=pg_catalog",
        "private_schema_usage": False,
        "ledger_insert": False,
        "latest_result_execute": False,
        "public_schema_create": False,
        "audit_insert": True,
        "private_owner_audit_insert": False,
        "private_owner_membership": False,
    }
    assert dict(admin_bootstrap_boundary) == {
        "schema_owner": admin_user,
        "schema_owner_is_superuser": True,
        "installer_owner": admin_user,
        "installer_security_definer": True,
        "installer_config": "search_path=pg_catalog",
        "finalizer_owner": admin_user,
        "finalizer_security_definer": True,
        "finalizer_config": "search_path=pg_catalog",
        "shared_admin_schema_usage": False,
        "shared_installer_execute": False,
        "shared_finalizer_execute": False,
    }

    database = _database_from_url(admin_url)
    direct_engines = {
        role: create_engine(
            _url(user=role, password=password, host=host, port=port, database=database),
            isolation_level="AUTOCOMMIT",
        )
        for role, password in _NORMAL_ROLE_PASSWORDS.items()
    }
    executor_engine = create_engine(
        _url(
            user="restore_drill_executor",
            password=_EXECUTOR_PASSWORD,
            host=host,
            port=port,
            database=database,
        ),
        isolation_level="AUTOCOMMIT",
    )
    trusted_result_id: int | None = None
    api_shape: dict[str, object] | None = None
    raw_marker = "p-detail-private-marker"
    hostile_backup_marker = "p-backup-private-dsn-marker"
    hostile_dump_marker = "private-dump-name-marker"
    absurd_table_count = -2_147_483_648
    try:
        for role_name, direct_engine in direct_engines.items():
            _expect_permission_denied(
                direct_engine,
                f"CREATE DATABASE forbidden_restore_drill_{role_name}",
            )
            _expect_permission_denied(
                direct_engine,
                "SELECT restore_drill_executor.is_due(604800)",
            )
            _expect_permission_denied(
                direct_engine,
                "SELECT restore_drill_executor.record_result('forbidden.sql.gz', 'pass', 'x', 1)",
            )
            _expect_permission_denied(
                direct_engine,
                "SELECT count(*) FROM restore_drill_executor.restore_drill_results",
            )
            _expect_permission_denied(
                direct_engine,
                "INSERT INTO restore_drill_executor.restore_drill_results (result, detail) "
                "VALUES ('pass', 'forbidden')",
            )

        for role_name in ("butlers", "butler_general_rw", "connector_writer"):
            with direct_engines[role_name].connect() as connection:
                spoof_id = connection.execute(
                    text(
                        """
                        INSERT INTO public.audit_log (actor, action, target, result, error, metadata)
                        VALUES (
                            :actor,
                            'restore_drill_result',
                            'ordinary-public-audit-spoof',
                            'pass',
                            'spoofed public audit result',
                            '{}'::jsonb
                        )
                        RETURNING id
                        """
                    ),
                    {"actor": f"ordinary-public-audit-spoof:{role_name}"},
                ).scalar_one()
                assert isinstance(spoof_id, int)

        # Broad public audit DML deliberately remains available to normal
        # writers. It is a projection only: before the executor writes the
        # private authority ledger, it cannot manufacture a dashboard pass or
        # change the due decision.
        assert asyncio.run(_read_restore_drill_api_shape(shared_url)) is None

        # A public-audit table owner can install a hostile trigger. It runs as
        # the effective user of the INSERT statement, so it must never inherit
        # the private-owner authority carried by record_result(). The trigger
        # catches privilege errors to prove the allowed audit projection still
        # completes without an extra protected result.
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(
                    text(
                        """
                        CREATE FUNCTION public.restore_drill_hostile_audit_trigger()
                        RETURNS trigger
                        LANGUAGE plpgsql
                        AS $$
                        DECLARE
                            v_ledger_write TEXT := 'succeeded';
                            v_reader_call TEXT := 'succeeded';
                        BEGIN
                            BEGIN
                                INSERT INTO restore_drill_executor.restore_drill_results (
                                    result, detail
                                )
                                VALUES ('pass', 'hostile audit trigger marker');
                            EXCEPTION WHEN insufficient_privilege THEN
                                v_ledger_write := 'denied';
                            END;
                            BEGIN
                                PERFORM restore_drill_executor.latest_result();
                            EXCEPTION WHEN insufficient_privilege THEN
                                v_reader_call := 'denied';
                            END;
                            NEW.metadata := COALESCE(NEW.metadata, '{}'::jsonb)
                                || jsonb_build_object(
                                    'hostile_ledger_write', v_ledger_write,
                                    'hostile_reader_call', v_reader_call,
                                    'hostile_effective_user', current_user
                                );
                            IF NEW.result = 'pass' THEN
                                RAISE EXCEPTION 'hostile audit trigger blocked projection';
                            END IF;
                            RETURN NEW;
                        END;
                        $$;
                        CREATE TRIGGER restore_drill_hostile_audit_trigger
                        BEFORE INSERT ON public.audit_log
                        FOR EACH ROW
                        WHEN (NEW.actor = 'restore_drill')
                        EXECUTE FUNCTION public.restore_drill_hostile_audit_trigger();
                        """
                    )
                )
        finally:
            engine.dispose()

        with executor_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT restore_drill_executor.is_due(604800)")
                ).scalar_one()
                is True
            )
            # A direct executor credential holder can choose every compatibility
            # argument. The final SQL boundary must retain none of those values
            # (including the absurd table count) in either durable authority or
            # the public audit projection.
            trusted_result_id = connection.execute(
                text(
                    "SELECT restore_drill_executor.record_result("
                    ":backup_name, 'fail', :detail, :table_count)"
                ),
                {
                    "backup_name": (
                        "postgresql://restore:"
                        f"{hostile_backup_marker}@db.example.test/{hostile_dump_marker}.sql.gz"
                    ),
                    "detail": (
                        "postgresql://restore:"
                        f"{raw_marker}@db.example.test/postgres COPY sensitive_table"
                    ),
                    "table_count": absurd_table_count,
                },
            ).scalar_one()
            assert isinstance(trusted_result_id, int)
            with pytest.raises(DBAPIError, match="p_result must be pass or fail"):
                connection.execute(
                    text(
                        "SELECT restore_drill_executor.record_result("
                        ":backup_name, CAST(NULL AS text), 'restored 1 table', 1)"
                    ),
                    {"backup_name": "null-result.sql.gz"},
                ).scalar_one()
            # A public-audit owner can still cause an availability failure by
            # rejecting its projection, but the writer and ledger are one
            # statement transaction: no unprojected authoritative result may
            # survive that hostile trigger.
            with pytest.raises(DBAPIError, match="hostile audit trigger blocked projection"):
                connection.execute(
                    text(
                        "SELECT restore_drill_executor.record_result("
                        "'safe-name.sql.gz', 'pass', 'safe detail', 1)"
                    )
                ).scalar_one()
            assert (
                connection.execute(
                    text("SELECT restore_drill_executor.is_due(604800)")
                ).scalar_one()
                is False
            )
        _expect_permission_denied(executor_engine, "SELECT count(*) FROM public.audit_log")
        _expect_permission_denied(
            executor_engine,
            "SELECT count(*) FROM restore_drill_executor.restore_drill_results",
        )
        _expect_permission_denied(
            executor_engine,
            "CREATE TABLE restore_drill_executor.forbidden_direct_table (id integer)",
        )
    finally:
        for direct_engine in direct_engines.values():
            direct_engine.dispose()
        executor_engine.dispose()

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            assert trusted_result_id is not None
            # Even a newer row forged by a database administrator in the
            # intentionally broad public projection cannot override the
            # protected authority consumed by due checks and dashboard reads.
            connection.execute(
                text(
                    """
                    INSERT INTO public.audit_log (actor, action, target, result, error, metadata)
                    VALUES (
                        'admin-public-audit-spoof',
                        'restore_drill_result',
                        'admin-public-audit-spoof',
                        'pass',
                        'spoofed public audit result',
                        '{}'::jsonb
                    )
                    """
                )
            )
            assert (
                connection.execute(
                    text("SELECT restore_drill_executor.is_due(604800)")
                ).scalar_one()
                is False
            )
            authority_row = (
                connection.execute(
                    text(
                        """
                    SELECT result, detail, to_jsonb(result_row)::text AS row_text
                    FROM restore_drill_executor.restore_drill_results AS result_row
                    WHERE id = :trusted_result_id
                        """
                    ),
                    {"trusted_result_id": trusted_result_id},
                )
                .mappings()
                .one()
            )
            authority_count = connection.execute(
                text("SELECT count(*) FROM restore_drill_executor.restore_drill_results")
            ).scalar_one()
            audit_projection = (
                connection.execute(
                    text(
                        """
                    SELECT
                        error,
                        target,
                        metadata::text AS metadata_text,
                        metadata ->> 'hostile_ledger_write' AS hostile_ledger_write,
                        metadata ->> 'hostile_reader_call' AS hostile_reader_call,
                        metadata ->> 'hostile_effective_user' AS hostile_effective_user
                    FROM public.audit_log
                    WHERE actor = 'restore_drill'
                      AND action = 'restore_drill_result'
                      AND result = 'fail'
                    ORDER BY id DESC
                    LIMIT 1
                        """
                    )
                )
                .mappings()
                .one()
            )
            assert authority_row["result"] == "fail"
            assert authority_row["detail"] == "restore drill diagnostic withheld"
            assert authority_count == 1
            assert "table_count" not in authority_row["row_text"]
            assert str(absurd_table_count) not in authority_row["row_text"]
            assert raw_marker not in authority_row["row_text"]
            assert hostile_backup_marker not in authority_row["row_text"]
            assert hostile_dump_marker not in authority_row["row_text"]
            assert audit_projection["error"] == "restore drill diagnostic withheld"
            assert audit_projection["target"] == "restore_drill"
            assert audit_projection["hostile_ledger_write"] == "denied"
            assert audit_projection["hostile_reader_call"] == "denied"
            assert audit_projection["hostile_effective_user"] == (
                "restore_drill_executor_audit_writer"
            )
            assert "table_count" not in audit_projection["metadata_text"]
            assert str(absurd_table_count) not in audit_projection["metadata_text"]
            assert raw_marker not in audit_projection["metadata_text"]
            assert hostile_backup_marker not in audit_projection["metadata_text"]
            assert hostile_dump_marker not in audit_projection["metadata_text"]
    finally:
        engine.dispose()

    api_shape = asyncio.run(_read_restore_drill_api_shape(shared_url))
    assert api_shape is not None
    assert api_shape["result"] == "fail"
    assert api_shape["detail"] == "restore drill diagnostic withheld"
    assert raw_marker not in repr(api_shape)
    assert hostile_backup_marker not in repr(api_shape)
    assert hostile_dump_marker not in repr(api_shape)


@pytest.mark.parametrize("invalid_secret", [b"\xff", b"contains-nul\x00secret"])
def test_invalid_secret_rolls_back_login_and_createdb_role_mutation(
    postgres_container, tmp_path: Path, invalid_secret: bytes
) -> None:
    """A PostgreSQL decode error cannot leave the executor login partially enabled."""
    admin_url, _shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    database = _database_from_url(admin_url)
    # Roles are server-global while this module deliberately reuses one
    # container. Re-establish the managed reserved posture before asserting
    # this invocation's transaction behavior.
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "ALTER ROLE restore_drill_executor NOLOGIN NOCREATEDB "
                    "NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION"
                )
            )
    finally:
        engine.dispose()

    password_file = tmp_path / "invalid-restore-drill-password"
    password_file.write_bytes(invalid_secret)
    provisioned = _provision_executor(
        host=host,
        port=port,
        admin_user=admin_user,
        admin_password=admin_password,
        database=database,
        password_file=password_file,
    )
    assert provisioned.returncode != 0

    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            flags = (
                connection.execute(
                    text(
                        "SELECT rolcanlogin, rolcreatedb FROM pg_roles "
                        "WHERE rolname = 'restore_drill_executor'"
                    )
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()
    assert flags == {"rolcanlogin": False, "rolcreatedb": False}
