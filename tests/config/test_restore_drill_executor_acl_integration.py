"""Disposable PostgreSQL proof for the restore-drill role boundary.

REQ-database-security-006 requires effective role ACLs, not static SQL alone.
This module uses actual compiled preparation, a selected held maintenance
session and ordinary migration identity for real chains, then
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
from sqlalchemy import column, delete, event, table, text
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.exc import DBAPIError, ProgrammingError

from butlers.bootstrap_prerequisite import check_bootstrap_connection
from butlers.db_authority.apply import install_final_revision
from butlers.db_authority.interfaces import install_revision_group
from butlers.db_authority.roles import AuthorityError
from butlers.jobs.backup_health import get_last_restore_drill
from butlers.migrations import finalize_authority, migration_session, run_migrations
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    _upgrade_chain_to_revision,
    bootstrap_extensions,
    migration_db_name,
)

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
_MAINTENANCE_URLS: dict[str, str] = {}
_PLANS = (("core", None, None), ("relationship", "relationship", None))
_NORMAL_ROLE_PASSWORDS = {
    "butlers": _SHARED_PASSWORD,
    "butler_general_rw": _BUTLER_PASSWORD,
    "connector_writer": _CONNECTOR_PASSWORD,
    _PUBLIC_PROBE_ROLE: _PUBLIC_PASSWORD,
}


def create_engine(url, **kwargs):
    """Finite real driver operations; no authority or SQL substitution."""
    options = dict(connect_timeout=5, options="-c statement_timeout=30000 -c lock_timeout=5000")
    options.update(kwargs.pop("connect_args", {}))
    return _create_engine(url, connect_args=options, **kwargs)


def _authority_snapshot(admin_url: str):
    """Independent complete restore population + authoritative rows/audit projection."""
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("""
                SELECT jsonb_build_object(
                  'namespaces', (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.name) FROM (
                    SELECT n.nspname AS name, n.nspowner, n.nspacl::text
                    FROM pg_catalog.pg_namespace n
                    WHERE n.nspname IN ('restore_drill_executor','restore_drill_executor_admin')
                  ) x),
                  'relations', (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.oid) FROM (
                    SELECT c.oid,c.relname,c.relkind,c.relowner,c.relacl::text,
                      c.relrowsecurity,c.relforcerowsecurity
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname IN ('restore_drill_executor','restore_drill_executor_admin')
                  ) x),
                  'functions', (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.oid) FROM (
                    SELECT p.oid,p.proname,p.proargtypes::text,p.prosrc,p.proowner,
                      p.prosecdef,p.proconfig,p.proacl::text
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname IN ('restore_drill_executor','restore_drill_executor_admin')
                  ) x),
                  'results', (SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                    FROM restore_drill_executor.restore_drill_results r),
                  'audit', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id)
                    FROM public.audit_log a WHERE a.action='restore_drill_result')
                )
            """)
            ).scalar_one()
    finally:
        engine.dispose()


def _assert_current_restore_surface(admin_url: str) -> None:
    """Real installed body/settings/ACL oracle, not a source-text-only proof."""
    from butlers.db_authority.interfaces import (
        function_matches,
        inspect_function,
        protected_group_functions,
    )
    from butlers.db_authority.roles import compile_deployment_profiles

    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            database, actor = connection.execute(
                text("SELECT current_database(),session_user")
            ).one()
            profiles = compile_deployment_profiles(database, source_database="butlers")
            definitions = protected_group_functions(profiles, actor, {"restore"})
            assert len(definitions) == 4
            for definition in definitions:
                assert function_matches(
                    connection, definition, inspect_function(connection, definition)
                )
    finally:
        engine.dispose()


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
        timeout=30,
    )


def _expect_permission_denied(engine, statement: str) -> None:
    with engine.connect() as connection, pytest.raises(ProgrammingError) as denied:
        connection.execute(text(statement))
    assert denied.value.orig.pgcode == "42501"


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
                text(
                    "ALTER ROLE butlers LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :password"
                ),
                {"password": _SHARED_PASSWORD},
            )
            connection.execute(text(f'CREATE DATABASE "{database}" OWNER butlers'))
    finally:
        engine.dispose()

    bootstrap_extensions(admin_url)
    _MAINTENANCE_URLS[shared_url] = admin_url
    return admin_url, shared_url, host, port, admin_user, admin_password


def _bootstrap_database(postgres_container) -> tuple[str, str, str, str, str, str]:
    """Actual compiled operator preparation, without running a migration yet."""
    result = _create_database_before_restore_bootstrap(postgres_container)
    _bootstrap_migration_prerequisites(result[0], "butlers")
    return result


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
                            SELECT 1 FROM pg_roles
                            WHERE rolname = 'restore_drill_alternate_bootstrap'
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
                    "ALTER ROLE restore_drill_alternate_bootstrap "
                    "LOGIN SUPERUSER PASSWORD :password"
                ),
                {"password": _ALTERNATE_BOOTSTRAP_PASSWORD},
            )
    finally:
        engine.dispose()


def _configure_direct_acl_subjects(admin_url: str) -> None:
    """Unprivileged PUBLIC probe only; capability roles keep their NOLOGIN posture."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles
                        WHERE rolname='restore_drill_public_probe') THEN
                        CREATE ROLE restore_drill_public_probe NOLOGIN NOINHERIT NOSUPERUSER
                          NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS;
                    END IF;
                END; $$;
            """)
            )
    finally:
        engine.dispose()


def _role_engine(admin_url: str, role: str):
    """Native effective role without making serving capability principals logins."""
    if role not in {*_NORMAL_ROLE_PASSWORDS, "dashboard_api"}:
        raise ValueError("Unexpected fixed test subject")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    @event.listens_for(engine, "connect")
    def bind_subject(connection, _record):
        # SET ROLE must survive dialect initialization/checkout rollback.
        # Commit this fixed session choice independently of that ordering.
        original_autocommit = connection.autocommit
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{role}"')
        finally:
            connection.autocommit = original_autocommit

    @event.listens_for(engine, "engine_connect")
    def verify_subject(connection):
        actual_role = connection.exec_driver_sql("SELECT current_user").scalar_one()
        assert actual_role == role

    return engine


def _run_real_core_chain_with_relationship_prerequisite(shared_url: str) -> None:
    """Real core/relationship chains on one held selected maintenance connection."""
    with migration_session(_MAINTENANCE_URLS[shared_url], source_database="butlers") as held:
        _upgrade_chain_to_revision(
            shared_url, chain="core", schema=None, revision="core_122", connection=held
        )
        asyncio.run(
            run_migrations(shared_url, chain="relationship", schema="relationship", connection=held)
        )
        asyncio.run(run_migrations(shared_url, chain="core", connection=held))
        finalize_authority(held, source_database="butlers", plans=_PLANS)


def _run_core_chain_through_restore_drill_predecessor(shared_url: str) -> None:
    """Actual selected core195 DDL, distinct from the pinned historical release."""
    with migration_session(_MAINTENANCE_URLS[shared_url], source_database="butlers") as held:
        _upgrade_chain_to_revision(
            shared_url, chain="core", schema=None, revision="core_122", connection=held
        )
        asyncio.run(
            run_migrations(shared_url, chain="relationship", schema="relationship", connection=held)
        )
        _upgrade_chain_to_revision(
            shared_url, chain="core", schema=None, revision="core_195", connection=held
        )
        held.commit()


def _rerun_restore(shared_url: str) -> None:
    """The real fixed installer; no stored installer or fixture capability grant."""
    with migration_session(_MAINTENANCE_URLS[shared_url], source_database="butlers") as held:
        install_revision_group(held, "restore")
        held.commit()


def _finalize_current(shared_url: str) -> None:
    with migration_session(_MAINTENANCE_URLS[shared_url], source_database="butlers") as held:
        finalize_authority(held, source_database="butlers", plans=_PLANS)


def _assert_current_final_edge_refuses(shared_url: str) -> None:
    with pytest.raises(AuthorityError):
        with migration_session(_MAINTENANCE_URLS[shared_url], source_database="butlers") as held:
            install_final_revision(held)
            held.commit()


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
        timeout=30,
    )


def _database_from_url(url: str) -> str:
    return url.rsplit("/", 1)[1]


async def _read_restore_drill_api_shape(database_url: str) -> dict[str, object] | None:
    """Actual API reader with the dedicated dashboard effective role."""

    async def dashboard_identity(connection):
        await connection.execute("SET ROLE dashboard_api")

    pool = None
    try:
        async with asyncio.timeout(10):
            pool = await asyncpg.create_pool(
                _MAINTENANCE_URLS[database_url], min_size=1, max_size=1, init=dashboard_identity
            )
            return await get_last_restore_drill(pool)
    finally:
        if pool is not None:
            try:
                async with asyncio.timeout(10):
                    await pool.close()
            except BaseException:
                pool.terminate()
                raise


def _precreate_shared_namespace(shared_url: str) -> None:
    """Database owner plants its own namespace; no trusted object GRANT."""
    engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE SCHEMA restore_drill_executor"))
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
    """Only production compiled preparation may create current purpose roles."""
    _bootstrap_migration_prerequisites(admin_url, "butlers")


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
                                to_regprocedure('restore_drill_executor_admin.finalize_interface()'),
                                'EXECUTE'
                            ) IS TRUE AS shared_finalizer_execute,
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
    """Retired noop signatures cannot authorize the current selected final edge."""
    # Preserve the original missing-interface/no-effects condition as well as
    # the already-current final-edge check below. This must expose a product
    # preflight omission; a later final-edge rejection cannot excuse fresh DDL.
    fresh_admin, fresh_shared, *_ = _bootstrap_database(postgres_container)
    _run_core_chain_through_restore_drill_predecessor(fresh_shared)
    _precreate_untrusted_admin_bootstrap(fresh_shared)
    fresh_engine = create_engine(fresh_shared, isolation_level="AUTOCOMMIT")
    try:
        with fresh_engine.connect() as connection:
            connection.execute(text("SELECT restore_drill_executor_admin.install_interface()"))
            connection.execute(text("SELECT restore_drill_executor_admin.finalize_interface()"))
    finally:
        fresh_engine.dispose()
    for _ in range(2):
        with pytest.raises(AuthorityError):
            _rerun_restore(fresh_shared)
        state = _read_noop_state(fresh_admin)
        assert state == {
            "admin_schema_owner": "butlers",
            "installer_owner": "butlers",
            "finalizer_owner": "butlers",
            "trusted_ledger_exists": False,
            "executor_due_execute": False,
        }

    admin_url, shared_url, *_ = _bootstrap_database(postgres_container)
    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    _precreate_untrusted_admin_bootstrap(shared_url)
    engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT restore_drill_executor_admin.install_interface()"))
            connection.execute(text("SELECT restore_drill_executor_admin.finalize_interface()"))
    finally:
        engine.dispose()
    before = _read_noop_state(admin_url)
    full_before = _authority_snapshot(admin_url)
    for _ in range(2):
        _assert_current_final_edge_refuses(shared_url)
        assert _read_noop_state(admin_url) == before
        assert _authority_snapshot(admin_url) == full_before
    assert before == {
        "admin_schema_owner": "butlers",
        "installer_owner": "butlers",
        "finalizer_owner": "butlers",
        "trusted_ledger_exists": True,
        "executor_due_execute": True,
    }


def _read_noop_state(admin_url: str) -> dict:
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            return dict(
                connection.execute(
                    text("""
                SELECT pg_get_userbyid(n.nspowner) AS admin_schema_owner,
                  pg_get_userbyid(i.proowner) AS installer_owner,
                  pg_get_userbyid(f.proowner) AS finalizer_owner,
                  to_regclass('restore_drill_executor.restore_drill_results') IS NOT NULL
                    AS trusted_ledger_exists,
                  has_function_privilege('restore_drill_executor',
                    to_regprocedure('restore_drill_executor.is_due(integer)'),'EXECUTE')
                    IS TRUE AS executor_due_execute
                FROM pg_catalog.pg_namespace n
                JOIN pg_catalog.pg_proc i ON i.pronamespace=n.oid AND i.proname='install_interface'
                JOIN pg_catalog.pg_proc f ON f.pronamespace=n.oid AND f.proname='finalize_interface'
                WHERE n.nspname='restore_drill_executor_admin'
            """)
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()


def test_privileged_rerun_preserves_existing_admin_interface_owner(
    postgres_container,
) -> None:
    """A different maintenance login must retain the fixed NOLOGIN purpose owner."""
    admin_url, shared_url, host, port, admin_user, _ = _bootstrap_database(postgres_container)
    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    _create_alternate_bootstrap_superuser(admin_url)
    assert admin_user != _ALTERNATE_BOOTSTRAP_ROLE
    alternate = _url(
        user=_ALTERNATE_BOOTSTRAP_ROLE,
        password=_ALTERNATE_BOOTSTRAP_PASSWORD,
        host=host,
        port=port,
        database=_database_from_url(admin_url),
    )
    with migration_session(alternate, source_database="butlers") as held:
        install_revision_group(held, "restore")
        finalize_authority(held, source_database="butlers", plans=_PLANS)
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            admin_interface = (
                connection.execute(
                    text("""
                SELECT pg_get_userbyid(n.nspowner) AS schema_owner,
                  pg_get_userbyid(c.relowner) AS bootstrap_configuration_owner,
                  pg_get_userbyid(i.proowner) AS installer_owner,
                  pg_get_userbyid(f.proowner) AS finalizer_owner
                FROM pg_catalog.pg_namespace n
                JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid
                  AND c.relname='restore_drill_results'
                JOIN pg_catalog.pg_proc i ON i.pronamespace=n.oid AND i.proname='is_due'
                JOIN pg_catalog.pg_proc f ON f.pronamespace=n.oid AND f.proname='record_result'
                WHERE n.nspname='restore_drill_executor'
            """)
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()
    assert dict(admin_interface) == {
        "schema_owner": "restore_drill_executor_owner",
        "bootstrap_configuration_owner": "restore_drill_executor_owner",
        "installer_owner": "restore_drill_executor_owner",
        "finalizer_owner": "restore_drill_executor_owner",
    }


def test_normal_migration_role_temp_catalog_shadow_cannot_subvert_restore_installer(
    postgres_container,
) -> None:
    """The normal migration role's temporary ``pg_proc`` cannot shadow catalog checks."""
    admin_url, shared_url, *_ = _bootstrap_database(postgres_container)
    _run_real_core_chain_with_relationship_prerequisite(shared_url)

    shared_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with shared_engine.connect() as connection:
            connection.info["butlers.source_database"] = "butlers"
            connection.execute(text("SET ROLE butlers"))
            assert connection.execute(
                text("SELECT has_database_privilege(current_user, current_database(), 'TEMPORARY')")
            ).scalar_one()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION pg_temp.catalog_shadow_membership_marker()
                    RETURNS integer
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        GRANT restore_drill_executor_owner TO butlers WITH SET TRUE;
                        RETURN 0;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TEMPORARY VIEW pg_proc AS
                    SELECT
                        catalog_proc.oid,
                        (
                            catalog_proc.proowner::integer
                            + pg_temp.catalog_shadow_membership_marker()
                        )::oid AS proowner,
                        catalog_proc.prosecdef
                    FROM pg_catalog.pg_proc AS catalog_proc
                    """
                )
            )
            # Qualified admission must not execute the malicious temp catalog view.
            check_bootstrap_connection(connection)
            install_revision_group(connection, "restore")
            gained_owner_membership = connection.execute(
                text("SELECT pg_has_role('butlers', 'restore_drill_executor_owner', 'USAGE')")
            ).scalar_one()
    finally:
        shared_engine.dispose()

    admin_engine = create_engine(admin_url)
    try:
        with admin_engine.connect() as connection:
            state = dict(
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(ledger.relowner) AS ledger_owner,
                            has_function_privilege(
                                'butlers',
                                to_regprocedure('restore_drill_executor_admin.install_interface()'),
                                'EXECUTE'
                            ) IS TRUE AS shared_installer_execute,
                            has_function_privilege(
                                'butlers',
                                to_regprocedure('restore_drill_executor_admin.finalize_interface()'),
                                'EXECUTE'
                            ) IS TRUE AS shared_finalizer_execute
                        FROM pg_class AS ledger
                        JOIN pg_namespace AS result_schema
                            ON result_schema.oid = ledger.relnamespace
                        WHERE result_schema.nspname = 'restore_drill_executor'
                          AND ledger.relname = 'restore_drill_results'
                          AND ledger.relkind = 'r'
                        """
                    )
                )
                .mappings()
                .one()
            )
    finally:
        admin_engine.dispose()

    assert state == {
        "ledger_owner": "restore_drill_executor_owner",
        "shared_installer_execute": False,
        "shared_finalizer_execute": False,
    }
    assert gained_owner_membership is False


def test_privileged_rerun_repairs_legacy_executor_interface_search_paths(
    postgres_container,
) -> None:
    """A trusted source-created body repairs its known legacy path configuration.

    Only the configuration differs; ownership and body remain the actual fixed
    installer output. This does not adopt an attacker-owned predecessor.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    _assert_current_restore_surface(admin_url)

    procedures = (
        "restore_drill_executor.is_due(integer)",
        "restore_drill_executor.record_result(text,text,text,integer)",
        "restore_drill_executor.latest_result()",
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for procedure in procedures:
                connection.execute(text(f"ALTER FUNCTION {procedure} SET search_path = pg_catalog"))
            legacy_configs = (
                connection.execute(
                    text(
                        """
                        SELECT p.proname, array_to_string(p.proconfig, ',') AS function_config
                        FROM pg_proc AS p
                        JOIN pg_namespace AS n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'restore_drill_executor'
                          AND p.proname IN ('is_due', 'latest_result', 'record_result')
                        ORDER BY p.proname
                        """
                    )
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    assert [dict(row) for row in legacy_configs] == [
        {"proname": "is_due", "function_config": "search_path=pg_catalog"},
        {"proname": "latest_result", "function_config": "search_path=pg_catalog"},
        {"proname": "record_result", "function_config": "search_path=pg_catalog"},
    ]

    normal_engine = create_engine(shared_url)
    try:
        with normal_engine.connect() as connection:
            connection.info["butlers.source_database"] = "butlers"
            with pytest.raises(AuthorityError):
                install_revision_group(connection, "restore")
    finally:
        normal_engine.dispose()
    _rerun_restore(shared_url)
    _finalize_current(shared_url)
    _assert_current_restore_surface(admin_url)

    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            repaired_configs = (
                connection.execute(
                    text(
                        """
                        SELECT p.proname, array_to_string(p.proconfig, ',') AS function_config
                        FROM pg_proc AS p
                        JOIN pg_namespace AS n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'restore_drill_executor'
                          AND p.proname IN ('is_due', 'latest_result', 'record_result')
                        ORDER BY p.proname
                        """
                    )
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    assert [dict(row) for row in repaired_configs] == [
        {
            "proname": "is_due",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
        {
            "proname": "latest_result",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
        {
            "proname": "record_result",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
    ]


def test_executor_interface_never_runs_a_public_decoy_after_rerun(
    postgres_container, tmp_path: Path
) -> None:
    """bu-mzm3su.1: the migration login cannot plant code the executor owner runs.

    Keep the original public overload and a companion in the owner's already
    authorized namespace. The companion makes path poisoning executable
    without a fixture GRANT. The actual group installer restores the fixed
    path; the full finalizer independently refuses unknown overloads.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    database = _database_from_url(admin_url)
    password_file = tmp_path / "restore-drill-executor-password"
    password_file.write_text(_EXECUTOR_PASSWORD + "\n", encoding="utf-8")
    provisioned = _provision_executor(
        host=host,
        port=port,
        admin_user=admin_user,
        admin_password=admin_password,
        database=database,
        password_file=password_file,
    )
    assert provisioned.returncode == 0, provisioned.stderr

    shared_engine = create_engine(shared_url, isolation_level="AUTOCOMMIT")
    try:
        with shared_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.make_interval(secs integer)
                    RETURNS interval
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        RAISE EXCEPTION 'hijacked as %', current_user;
                    END;
                    $$
                    """
                )
            )
    finally:
        shared_engine.dispose()

    # Same attack payload in an already authorized namespace independently proves
    # the pin, without inventing a public-schema capability for its purpose owner.
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text("""
                CREATE FUNCTION restore_drill_executor.make_interval(secs integer)
                RETURNS interval LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'hijacked as %', current_user; END;
                $$
            """)
            )
    finally:
        engine.dispose()
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            for procedure in (
                "restore_drill_executor.is_due(integer)",
                "restore_drill_executor.record_result(text,text,text,integer)",
            ):
                connection.execute(
                    text(
                        f"ALTER FUNCTION {procedure} SET search_path = pg_catalog, public, pg_temp"
                    )
                )
                connection.execute(
                    text(
                        f"ALTER FUNCTION {procedure} SET search_path = pg_catalog, "
                        "restore_drill_executor, public, pg_temp"
                    )
                )
    finally:
        admin_engine.dispose()

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
    try:
        # The companion reaches the same unqualified overload with the actual
        # purpose identity. No fixture-granted public USAGE supplies authority.
        # is_due() only reaches make_interval() once a result exists (the
        # empty-ledger branch short-circuits), so record one first.
        with executor_engine.connect() as connection:
            connection.execute(
                text("SELECT restore_drill_executor.record_result('legacy.sql.gz', 'pass', 'x', 1)")
            ).scalar_one()
            with pytest.raises(DBAPIError, match="hijacked as restore_drill_executor_owner"):
                connection.execute(text("SELECT restore_drill_executor.is_due(604800)"))

        for _ in range(2):
            _rerun_restore(shared_url)
        # Full catalog admission must reject the two unknown overloads. This is
        # a refusal check, not a claim that the group installer adopts them.
        with pytest.raises(AuthorityError):
            _finalize_current(shared_url)

        with executor_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT restore_drill_executor.is_due(604800)")
                ).scalar_one()
                is False
            )
            result_id = connection.execute(
                text("SELECT restore_drill_executor.record_result('decoy.sql.gz', 'fail', 'x', 1)")
            ).scalar_one()
            assert isinstance(result_id, int)
            assert (
                connection.execute(text("SELECT restore_drill_executor.is_due(1)")).scalar_one()
                is False
            )
    finally:
        executor_engine.dispose()

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            configs = connection.execute(
                text(
                    """
                    SELECT p.proname, p.proconfig
                    FROM pg_proc AS p
                    JOIN pg_namespace AS n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'restore_drill_executor'
                      AND p.proname IN ('is_due', 'record_result')
                    ORDER BY p.proname
                    """
                )
            ).all()
    finally:
        admin_engine.dispose()
    assert [(row.proname, list(row.proconfig)) for row in configs] == [
        ("is_due", ["search_path=pg_catalog, pg_temp"]),
        ("record_result", ["search_path=pg_catalog, pg_temp"]),
    ]

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("DROP FUNCTION restore_drill_executor.make_interval(integer)"))
            connection.execute(text("DROP FUNCTION public.make_interval(integer)"))
    finally:
        engine.dispose()
    _finalize_current(shared_url)
    _assert_current_restore_surface(admin_url)


def test_shared_authority_poison_cannot_be_finalized_or_reblessed_on_bootstrap_rerun(
    postgres_container,
) -> None:
    """Attacker-owned compatible signatures/trigger never become trusted authority."""
    admin_url, shared_url, *_ = _bootstrap_database(postgres_container)
    _run_core_chain_through_restore_drill_predecessor(shared_url)
    _precreate_shared_namespace(shared_url)
    _precreate_shared_authority_interface(shared_url)
    before = _read_shared_authority_handoff_state(admin_url)
    for _ in range(2):
        with pytest.raises(AuthorityError):
            _rerun_restore(shared_url)
        assert _read_shared_authority_handoff_state(admin_url) == before
    assert before == {
        "ledger_owner": "butlers",
        "is_due_owner": "butlers",
        "record_result_owner": "butlers",
        "latest_result_owner": "butlers",
        "poison_trigger_exists": True,
        "executor_is_due_execute": False,
        "executor_record_result_execute": False,
        "executor_latest_result_execute": False,
        "shared_schema_create": True,
        "shared_schema_usage": True,
        "shared_finalizer_execute": False,
        "owner_audit_insert": False,
    }


def test_precreated_shared_ledger_rejects_core_migration_without_authority_handoff(
    postgres_container,
) -> None:
    """REQ-database-security-006: a shared-role precreation is never trusted.

    A database owner can precreate an untrusted namespace before preparation.
    Its compatible table and trigger must make the fixed installer fail closed,
    rather than letting maintenance bless an attacker-controlled relation. The
    failed revision must leave its trusted interface absent; after an
    administrator removes the untrusted objects in this disposable database,
    retrying the revision remains valid.
    """
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _precreate_shared_namespace(shared_url)

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

    for _ in range(2):
        with pytest.raises(AuthorityError):
            _rerun_restore(shared_url)

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
            connection.execute(text("DROP SCHEMA restore_drill_executor"))
    finally:
        engine.dispose()

    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    _rerun_restore(shared_url)
    _finalize_current(shared_url)

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
    from tests.bootstrap_prerequisite_helpers import exercise_historical_bootstrap

    exercise_historical_bootstrap(
        postgres_container, tmp_path, _create_database_before_restore_bootstrap, _run_psql_file
    )
    admin_url, shared_url, host, port, admin_user, admin_password = _bootstrap_database(
        postgres_container
    )
    _run_real_core_chain_with_relationship_prerequisite(shared_url)
    _configure_direct_acl_subjects(admin_url)
    _assert_current_restore_surface(admin_url)

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
                            ('restore_drill_executor_audit_writer'::name),
                            ('dashboard_api'::name)
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
                    SELECT
                        p.proname,
                        pg_get_userbyid(p.proowner) AS owner,
                        array_to_string(p.proconfig, ',') AS function_config
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
                        'restore_drill_executor.write_audit_projection(text)'::regprocedure
                    """
                    )
                )
                .mappings()
                .one()
            )
            admin_bootstrap_boundary = (
                connection.execute(
                    text("""
                SELECT to_regnamespace('restore_drill_executor_admin') IS NULL
                    AS retired_schema_absent,
                  r.rolcanlogin, r.rolcreatedb, r.rolsuper, r.rolcreaterole,
                  r.rolinherit, r.rolbypassrls,
                  EXISTS(SELECT FROM pg_catalog.pg_auth_members m
                    WHERE m.roleid=r.oid OR m.member=r.oid)
                    AS owner_membership
                FROM pg_catalog.pg_roles r WHERE r.rolname='restore_drill_executor_owner'
            """)
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
        "schema_usage": False,
        "is_due_execute": False,
        "record_result_execute": False,
        "latest_result_execute": False,
        "ledger_select": False,
        "ledger_insert": False,
        "owner_membership": False,
    }
    assert acl["dashboard_api"] == {
        "subject": "dashboard_api",
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
        "schema_usage": True,
        "is_due_execute": False,
        "record_result_execute": False,
        "latest_result_execute": False,
        "ledger_select": False,
        "ledger_insert": False,
        "owner_membership": False,
    }
    assert function_owners == [
        {
            "proname": "is_due",
            "owner": "restore_drill_executor_owner",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
        {
            "proname": "latest_result",
            "owner": "restore_drill_executor_owner",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
        {
            "proname": "record_result",
            "owner": "restore_drill_executor_owner",
            "function_config": "search_path=pg_catalog, pg_temp",
        },
    ]
    assert dict(audit_writer_boundary) == {
        "function_owner": "restore_drill_executor_audit_writer",
        "security_definer": True,
        "function_config": "search_path=pg_catalog, pg_temp",
        "private_schema_usage": True,
        "ledger_insert": False,
        "latest_result_execute": False,
        "public_schema_create": False,
        "audit_insert": True,
        "private_owner_audit_insert": False,
        "private_owner_membership": False,
    }
    assert dict(admin_bootstrap_boundary) == {
        "retired_schema_absent": True,
        "rolcanlogin": False,
        "rolcreatedb": False,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolinherit": False,
        "rolbypassrls": False,
        "owner_membership": False,
    }

    database = _database_from_url(admin_url)
    direct_engines = {role: _role_engine(admin_url, role) for role in _NORMAL_ROLE_PASSWORDS}
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
                        INSERT INTO public.audit_log (actor, action, target, result, error,
                            metadata)
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
                    INSERT INTO public.audit_log (actor, action, target, result, error,
                            metadata)
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

    history_engine = _role_engine(admin_url, "butler_general_rw")
    # Construct the forbidden operation structurally. Production still has no
    # destructive audit SQL literal, and this real PG negative must prove the
    # selected General identity receives 42501 for the exact audit predicate.
    audit_history = table("audit_log", column("action"), schema="public")
    denied_delete = delete(audit_history).where(audit_history.c.action == "restore_drill_result")
    assert denied_delete.table.schema == "public"
    assert denied_delete.table.name == "audit_log"
    assert denied_delete.compile().params == {"action_1": "restore_drill_result"}
    try:
        for statement in (
            "UPDATE public.audit_log SET result='pass' WHERE action='restore_drill_result'",
            str(denied_delete.compile(compile_kwargs={"literal_binds": True})),
            "TRUNCATE public.audit_log",
            "UPDATE restore_drill_executor.restore_drill_results SET result='pass'",
            "DELETE FROM restore_drill_executor.restore_drill_results",
            "TRUNCATE restore_drill_executor.restore_drill_results",
        ):
            _expect_permission_denied(history_engine, statement)
    finally:
        history_engine.dispose()

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
