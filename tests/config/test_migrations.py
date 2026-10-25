"""Tests for Alembic migration infrastructure using testcontainers."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, ProgrammingError

from alembic import command
from butlers.bootstrap_prerequisite import prepare_bootstrap_connection
from butlers.db_authority.roles import AuthorityError
from butlers.migrations import finalize_authority, get_chain_head, migration_session, run_migrations
from butlers.testing.migration import (
    _upgrade_chain_to_revision,
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
    table_exists,
)

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

REQUIRED_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
RUNTIME_ROLES = {
    "general": "butler_general_rw",
    "health": "butler_health_rw",
    "messenger": "butler_messenger_rw",
    "relationship": "butler_relationship_rw",
    "switchboard": "butler_switchboard_rw",
}


def _maintenance_url(postgres_container, db_url):
    database = make_url(db_url).database
    if database is None:
        raise ValueError("Disposable migration database is required")
    return migration_bootstrap_db_url(postgres_container, database)


def _native_url(db_url):
    # testcontainers may use a SQLAlchemy-only driver-qualified scheme.
    return make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _run_selected_head(postgres_container, db_url, *, chain="core", schema=None):
    """Use the real maintenance actor, ordinary DDL identity and finalizer."""
    with migration_session(
        _maintenance_url(postgres_container, db_url), source_database="butlers"
    ) as connection:
        await run_migrations(db_url, chain=chain, schema=schema, connection=connection)
        finalize_authority(connection, source_database="butlers", plans=((chain, schema, None),))


def _upgrade_selected(postgres_container, db_url, config, revision, *, chain="core"):
    schema = config.get_main_option("butlers.target_schema") or None
    with migration_session(
        _maintenance_url(postgres_container, db_url), source_database="butlers"
    ) as connection:
        if revision is None:
            asyncio.run(run_migrations(db_url, chain=chain, schema=schema, connection=connection))
        else:
            _upgrade_chain_to_revision(
                db_url, chain=chain, schema=schema, revision=revision, connection=connection
            )
        finalize_authority(
            connection, source_database="butlers", plans=((chain, schema, revision),)
        )


def _downgrade_selected(postgres_container, db_url, config, target, *, revision):
    """Keep actual Alembic destination/refusal handling; do not emulate selectors."""
    schema = config.get_main_option("butlers.target_schema") or None
    with migration_session(
        _maintenance_url(postgres_container, db_url), source_database="butlers"
    ) as connection:
        previous = config.attributes.get("connection")
        config.attributes["connection"] = connection
        try:
            command.downgrade(config, target)
            finalize_authority(
                connection, source_database="butlers", plans=(("core", schema, revision),)
            )
        finally:
            if previous is None:
                config.attributes.pop("connection", None)
            else:
                config.attributes["connection"] = previous


def _latest_core_revision() -> str:
    """The core chain head, resolved from the Alembic revision graph.

    Deliberately not "the highest-numbered ``core_NNN`` file in the versions
    directory": that reads the naming convention rather than the chain itself,
    and it resolved the directory relative to the working directory.
    """
    return get_chain_head("core")


# The trusted-bootstrap runtime-attention interface: installed by core_198 and
# rolled back only under bootstrap authority, which is why rollback tests for
# older revisions must not migrate through it.
_PROTECTED_BOUNDARY_REVISION = "core_198"
_PROTECTED_BOUNDARY_TABLE = "runtime_attention_outbox"


def _stamped_versions(db_url: str) -> list[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return sorted(
                row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))
            )
    finally:
        engine.dispose()


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _get_table_columns_sql(db_url: str) -> dict[str, set[str]]:
    """Return ``{schema.table → {column, ...}}`` for all user tables via information_schema.

    Excludes system schemas (pg_catalog, information_schema, pg_toast).  Used to
    compare the connected login's privilege-filtered metadata inventory without
    relying on alembic version strings. An omitted table may still exist in
    pg_catalog; ownership and effective grants need a separate positive witness.
    """
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT table_schema, table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                    ORDER BY table_schema, table_name, column_name
                """)
            )
            result: dict[str, set[str]] = {}
            for row in rows:
                key = f"{row[0]}.{row[1]}"
                result.setdefault(key, set()).add(row[2])
        return result
    finally:
        engine.dispose()


def _schema_exists(db_url: str, schema_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = :s)"
            ),
            {"s": schema_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _table_exists_in_schema(db_url: str, schema_name: str, table_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = :s AND table_name = :t)"
            ),
            {"s": schema_name, "t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _function_is_security_definer(db_url: str, schema_name: str, function_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT p.prosecdef "
                "FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :s AND p.proname = :f"
            ),
            {"s": schema_name, "f": function_name},
        )
        value = result.scalar()
    engine.dispose()
    return bool(value)


def _role_exists(db_url: str, role_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r)"),
            {"r": role_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _has_schema_create_privilege(db_url: str, schema_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        has_privilege = conn.execute(
            text(
                "SELECT has_schema_privilege(current_user, :schema_name, 'USAGE') "
                "AND has_schema_privilege(current_user, :schema_name, 'CREATE')"
            ),
            {"schema_name": schema_name},
        ).scalar_one()
    engine.dispose()
    return bool(has_privilege)


def _execute_as_role(
    postgres_container, db_url: str, role_name: str, sql: str, *, scalar: bool = False
):
    # The normal migration owner is deliberately not a member of runtime roles.
    # This is an explicit disposable administration connection, not a runtime
    # LOGIN/admission proof or a fixture-created membership/grant.
    quoted_role = _quote_ident(role_name)
    engine = create_engine(
        _maintenance_url(postgres_container, db_url), isolation_level="AUTOCOMMIT"
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def _replay_init_db(postgres_container, db_name: str, db_url: str) -> None:
    """Replay actual compiled prerequisite setup; it cannot widen application ACLs."""
    migration_user = urlparse(db_url).username
    assert migration_user is not None
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    raw_connection = None
    try:
        raw_connection = engine.raw_connection()
        raw_connection.autocommit = True
        prepare_bootstrap_connection(
            raw_connection, db_name, migration_user, source_database="butlers"
        )
    finally:
        try:
            if raw_connection is not None:
                raw_connection.close()
        finally:
            engine.dispose()


def test_create_migration_db_bootstraps_restore_drill_prerequisite(postgres_container):
    """REQ-database-security-006: source preparation replaces the three old admin installers."""
    db_url = create_migration_db(postgres_container, migration_db_name())

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            current_user = conn.execute(text("SELECT current_user")).scalar_one()
            database_owner = conn.execute(
                text(
                    "SELECT pg_get_userbyid(datdba) "
                    "FROM pg_database WHERE datname = current_database()"
                )
            ).scalar_one()
            can_create_databases = conn.execute(
                text("SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            has_trusted_installer = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_namespace AS admin_schema
                        JOIN pg_roles AS bootstrap_owner
                            ON bootstrap_owner.oid = admin_schema.nspowner
                        JOIN pg_proc AS installer
                            ON installer.pronamespace = admin_schema.oid
                           AND installer.proname = 'install_interface'
                           AND installer.pronargs = 0
                           AND installer.prosecdef
                        JOIN pg_proc AS finalizer
                            ON finalizer.pronamespace = admin_schema.oid
                           AND finalizer.proname = 'finalize_interface'
                           AND finalizer.pronargs = 0
                           AND finalizer.prosecdef
                        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
                          AND installer.proowner = admin_schema.nspowner
                          AND finalizer.proowner = admin_schema.nspowner
                          AND bootstrap_owner.rolsuper
                    )
                    """
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert database_owner == current_user
    assert can_create_databases is False
    assert has_trusted_installer is False


def test_core_migrations_tables_schemas_and_idempotency(postgres_container):
    """Core migrations create all required tables and schemas; idempotent on second run."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    for table in (
        "state",
        "scheduled_tasks",
        "sessions",
        "route_inbox",
        "butler_secrets",
        "calendar_sources",
        "calendar_events",
        "calendar_event_entities",
        "calendar_event_instances",
        "calendar_sync_cursors",
        "calendar_action_log",
        "delivery_preferences",
        "deferred_notifications",
    ):
        assert table_exists(db_url, table), f"{table} should exist"
    assert not table_exists(db_url, "google_oauth_credentials")
    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist"

    # Idempotency
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))
    assert table_exists(db_url, "state")

    # ``init-db.sql``'s managed bootstrap owns the schemas. The normal
    # NOCREATEDB migration login needs explicit schema privileges, not owner
    # bypasses, to create the migrated objects.
    for schema in REQUIRED_SCHEMAS:
        assert _has_schema_create_privilege(db_url, schema)


def test_core_only_migrations_keep_session_stubs_in_sync(postgres_container):
    """Core-only test provisioning keeps every QA sessions stub schema-compatible.

    ``core_055`` creates these schema-qualified tables for the QA UNION view
    during a default ``core`` run.  Later per-schema migrations can use bare
    ``ALTER TABLE sessions`` statements, which resolve only to
    ``public.sessions`` in this provisioning mode.  Column parity makes that
    drift visible instead of leaving a partial test database behind.
    """
    db_url = create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])
    table_columns = _get_table_columns_sql(db_url)
    public_columns = table_columns["public.sessions"]

    assert {"cached_input_tokens", "cache_creation_tokens"} <= public_columns

    from butlers.migrations import _chain_script_directory

    # The QA view migration owns these runtime-session stubs. A private auth
    # schema can also have a sessions table without sharing this data model.
    stub_schemas = _chain_script_directory("core").get_revision("core_055").module._SESSION_SCHEMAS
    session_stubs = {
        f"{schema}.sessions": table_columns[f"{schema}.sessions"] for schema in stub_schemas
    }
    assert session_stubs, "core-only provisioning must create QA sessions stubs"

    drift = {}
    for table_name, stub_columns in session_stubs.items():
        if stub_columns != public_columns:
            drift[table_name.removesuffix(".sessions")] = {
                "missing": sorted(public_columns - stub_columns),
                "unexpected": sorted(stub_columns - public_columns),
            }

    assert drift == {}, f"core-only sessions stub drift: {drift}"


def test_bounded_chain_revision_keeps_rollback_off_the_protected_boundary(postgres_container):
    """A bounded core target lets an old revision roll back without walking core_198.

    ``core_198`` installs the runtime-attention interface through the trusted
    bootstrap; its rollback is deliberately bootstrap-only and is allowed to
    refuse.  Later revisions layer their own retained cutover state on top of
    it.  So a per-migration rollback test that migrates to head first drags
    that boundary into a rollback that is really about some far older revision,
    and fails there for reasons unrelated to the migration under test.

    ``revisions=`` is the supported way to stay off it: stop at the revision
    the test owns, so the boundary is never installed and never asked to roll
    back (bu-2rqrl).
    """
    from butlers.migrations import _build_alembic_config, _chain_script_directory

    bounded_revision = "core_170"
    prior_revision = "core_169"

    # Guard against this test going vacuous: the boundary must genuinely sit
    # above the bounded target, or the bounded run was never skipping anything.
    head_to_target = {
        script.revision
        for script in _chain_script_directory("core").iterate_revisions(
            "core@head", bounded_revision
        )
    }
    assert _PROTECTED_BOUNDARY_REVISION in head_to_target - {bounded_revision}

    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core"],
        revisions={"core": bounded_revision},
    )

    assert _stamped_versions(db_url) == [bounded_revision]
    assert not table_exists(db_url, _PROTECTED_BOUNDARY_TABLE), (
        "a bounded run must not install the trusted-bootstrap runtime-attention interface"
    )

    bootstrap_config = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, db_name), chains=["core"]
    )
    _downgrade_selected(
        postgres_container, db_url, bootstrap_config, prior_revision, revision=prior_revision
    )
    assert _stamped_versions(db_url) == [prior_revision]

    _upgrade_selected(postgres_container, db_url, bootstrap_config, bounded_revision, chain="core")
    assert _stamped_versions(db_url) == [bounded_revision]


def test_core_migrations_retire_legacy_permission_default_seeds(postgres_container):
    """core@head leaves defaults inherited and honours explicit overrides."""
    import asyncpg

    from butlers.core.permissions import (
        SPAWN_PERMISSION,
        check_permission,
        require_permission,
    )

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    # core_121 initially seeded defaults, but final core head retires those rows
    # so inherited state remains distinguishable from an operator decision.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM public.permissions")).scalar_one()
            assert total == 0, "legacy default seeds must not survive at final core head"
    finally:
        engine.dispose()

    # Runtime enforcer against a real asyncpg pool: absent rows default-allow,
    # an explicit revoke denies (deny BLOCKS), and unknown butlers stay inherited.
    async def _exercise_enforcer() -> None:
        pool = await asyncpg.create_pool(_native_url(db_url), min_size=1, max_size=2)
        try:
            # No persisted row → inherited system default allows.
            allowed = await check_permission(pool, "chronicler", SPAWN_PERMISSION)
            assert allowed.allowed is True
            assert allowed.explicit is False

            # Explicit revoke must deny + require_permission raises.
            await pool.execute(
                "INSERT INTO public.permissions (butler, permission, granted, reason) "
                "VALUES ('chronicler', $1, FALSE, 'test-revoke')",
                SPAWN_PERMISSION,
            )
            denied = await check_permission(pool, "chronicler", SPAWN_PERMISSION)
            assert denied.allowed is False
            assert denied.explicit is True

            from butlers.core.permissions import PermissionDenied

            with pytest.raises(PermissionDenied):
                await require_permission(pool, "chronicler", SPAWN_PERMISSION)

            # Unknown butler with no row → default allow.
            unknown = await check_permission(pool, "nonexistent-butler", SPAWN_PERMISSION)
            assert unknown.allowed is True
            assert unknown.explicit is False
        finally:
            await pool.close()

    asyncio.run(_exercise_enforcer())


def test_core_migrations_create_delivery_tables_in_target_schema(postgres_container):
    """Schema-scoped core runs create notification delivery tables for new butlers."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core", schema="chronicler"))

    assert _table_exists_in_schema(db_url, "chronicler", "delivery_preferences")
    assert _table_exists_in_schema(db_url, "chronicler", "deferred_notifications")


def test_core_migration_repairs_relationship_read_access_to_switchboard_message_inbox(
    postgres_container,
):
    """core head repairs existing one-db deployments missing switchboard read grants."""
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    relationship_core = _build_alembic_config(db_url, chains=["core"], target_schema="relationship")
    switchboard_core = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    switchboard_chain = _build_alembic_config(
        db_url, chains=["switchboard"], target_schema="switchboard"
    )

    from butlers.bootstrap_prerequisite import (
        BootstrapPrerequisiteError,
        check_bootstrap_connection,
    )

    def relationship_read_default() -> bool:
        engine = create_engine(db_url)
        try:
            with engine.connect() as connection:
                return connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl AS d "
                        "JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace "
                        "CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) AS a "
                        "WHERE d.defaclrole = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) "
                        "AND n.nspname = 'switchboard' AND d.defaclobjtype = 'r' "
                        "AND a.grantee = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'butler_relationship_rw') "
                        "AND a.privilege_type = 'SELECT')"
                    )
                ).scalar_one()
        finally:
            engine.dispose()

    # Checked prerequisites carry no future relation privileges. A bounded
    # historical core closes ACLs rather than certifying today's runtime.
    assert relationship_read_default() is False
    _upgrade_selected(postgres_container, db_url, relationship_core, "core_076", chain="core")
    assert relationship_read_default() is False
    with migration_session(
        _maintenance_url(postgres_container, db_url), source_database="butlers"
    ) as connection:
        check_bootstrap_connection(connection)
        before = tuple(
            connection.execute(
                text(
                    "SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl::text "
                    "FROM pg_catalog.pg_default_acl ORDER BY 1,2,3"
                )
            )
        )
        check_bootstrap_connection(connection)
        assert (
            tuple(
                connection.execute(
                    text(
                        "SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl::text "
                        "FROM pg_catalog.pg_default_acl ORDER BY 1,2,3"
                    )
                )
            )
            == before
        )
        # The retired own-table default GRANT is no longer a prerequisite.
        # Corrupt a genuinely required role fact instead and prove admission
        # refuses read-only; explicit administrator restoration stays visible.
        connection.exec_driver_sql("ALTER ROLE butler_relationship_rw LOGIN")
        connection.commit()
        try:
            unsafe_before = tuple(
                connection.execute(
                    text(
                        "SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreaterole,"
                        "rolcreatedb,rolreplication,rolbypassrls FROM pg_roles ORDER BY rolname"
                    )
                )
            )
            with pytest.raises(BootstrapPrerequisiteError):
                check_bootstrap_connection(connection)
            assert (
                tuple(
                    connection.execute(
                        text(
                            "SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreaterole,"
                            "rolcreatedb,rolreplication,rolbypassrls FROM pg_roles ORDER BY rolname"
                        )
                    )
                )
                == unsafe_before
            )
        finally:
            connection.rollback()
            connection.exec_driver_sql("ALTER ROLE butler_relationship_rw NOLOGIN")
            connection.commit()
        check_bootstrap_connection(connection)
    _upgrade_selected(postgres_container, db_url, switchboard_core, "core_076", chain="core")
    assert relationship_read_default() is False
    _upgrade_selected(postgres_container, db_url, switchboard_chain, None, chain="switchboard")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # The partition function lives in the switchboard schema and its
            # body references ``message_inbox`` unqualified, so align the
            # connection's search_path before invoking it.
            conn.execute(text("SET search_path TO switchboard, public"))
            conn.execute(
                text("SELECT switchboard.switchboard_message_inbox_ensure_partition(now())")
            )
            conn.execute(
                text(
                    "INSERT INTO switchboard.message_inbox ("
                    "  received_at, request_context, raw_payload, normalized_text, "
                    "  direction, lifecycle_state, schema_version"
                    ") VALUES ("
                    "  now(), '{}'::jsonb, '{}'::jsonb, 'acl probe', "
                    "  'inbound', 'accepted', 'message_inbox.v2'"
                    ")"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            postgres_container,
            db_url,
            RUNTIME_ROLES["relationship"],
            "SELECT COUNT(*) FROM switchboard.message_inbox",
            scalar=True,
        )

    relationship_core = _build_alembic_config(db_url, chains=["core"], target_schema="relationship")
    # pinned-revision: both genuine schema histories must finish core_083's
    # shared contact_info ALTER before the first core_115 retires that table.
    _upgrade_selected(postgres_container, db_url, relationship_core, "core_114", chain="core")
    _upgrade_selected(postgres_container, db_url, switchboard_core, "core_114", chain="core")
    staged_engine = create_engine(db_url)
    try:
        with staged_engine.connect() as connection:
            for schema in ("relationship", "switchboard"):
                # pinned-revision: prove the shared-table pre-retirement staging point.
                assert (
                    connection.execute(
                        text(
                            f"SELECT version_num FROM {schema}.alembic_version "
                            "WHERE version_num = 'core_114'"
                        )
                    ).scalar_one()
                    == "core_114"
                )
            assert (
                connection.execute(
                    text("SELECT to_regclass('public.contact_info') IS NOT NULL")
                ).scalar_one()
                is True
            )
    finally:
        staged_engine.dispose()
    _upgrade_selected(postgres_container, db_url, relationship_core, None, chain="core")
    _upgrade_selected(postgres_container, db_url, switchboard_core, None, chain="core")

    count = _execute_as_role(
        postgres_container,
        db_url,
        RUNTIME_ROLES["relationship"],
        "SELECT COUNT(*) FROM switchboard.message_inbox",
        scalar=True,
    )
    assert count == 1
    assert relationship_read_default() is False
    _upgrade_selected(postgres_container, db_url, relationship_core, None, chain="core")
    _upgrade_selected(postgres_container, db_url, switchboard_core, None, chain="core")
    _upgrade_selected(postgres_container, db_url, switchboard_core, None, chain="core")
    assert relationship_read_default() is False
    assert (
        _execute_as_role(
            postgres_container,
            db_url,
            RUNTIME_ROLES["relationship"],
            "SELECT COUNT(*) FROM switchboard.message_inbox",
            scalar=True,
        )
        == 1
    )


def test_switchboard_runtime_role_can_ensure_message_inbox_partitions(postgres_container):
    """switchboard runtime role can create inbox partitions without owning the parent table."""
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    switchboard_core = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    switchboard_chain = _build_alembic_config(
        db_url, chains=["switchboard"], target_schema="switchboard"
    )

    _upgrade_selected(postgres_container, db_url, switchboard_core, None, chain="core")
    _upgrade_selected(postgres_container, db_url, switchboard_chain, None, chain="switchboard")

    assert _function_is_security_definer(
        db_url,
        "switchboard",
        "switchboard_message_inbox_ensure_partition",
    )

    partition_name = _execute_as_role(
        postgres_container,
        db_url,
        RUNTIME_ROLES["switchboard"],
        "SELECT switchboard.switchboard_message_inbox_ensure_partition("
        "'2099-01-15T00:00:00+00:00'::timestamptz"
        ")",
        scalar=True,
    )
    assert partition_name == "message_inbox_p209901"
    assert _table_exists_in_schema(db_url, "switchboard", "message_inbox_p209901")


def test_connector_writer_can_ensure_filtered_events_partition(postgres_container):
    """connector_writer can create filtered_events partitions without owning the parent table.

    Regression guard for bu-0qmj7: connectors_filtered_events_ensure_partition()
    must be SECURITY DEFINER so that the restricted connector_writer role can
    call it to create new monthly partitions even though it is not the owner of
    connectors.filtered_events.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    # Verify the schema-qualified function is SECURITY DEFINER.
    assert _function_is_security_definer(
        db_url,
        "connectors",
        "connectors_filtered_events_ensure_partition",
    )

    # Call the function as connector_writer for a far-future month that was
    # NOT pre-created by the migration (guarantees we're testing a genuine DDL
    # execution path, not a no-op IF NOT EXISTS).
    partition_name = _execute_as_role(
        postgres_container,
        db_url,
        "connector_writer",
        "SELECT connectors.connectors_filtered_events_ensure_partition("
        "'2099-03-15T00:00:00+00:00'::timestamptz"
        ")",
        scalar=True,
    )
    assert partition_name == "filtered_events_209903"
    assert _table_exists_in_schema(db_url, "connectors", "filtered_events_209903")


def test_core_scheduled_tasks_schema_and_constraints(postgres_container):
    """scheduled_tasks has dispatch/calendar columns; constraints enforced; calendar linkage columns present."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, column_default FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'scheduled_tasks'"
                )
            )
            columns = {str(name): default for name, default in rows}

            # dispatch columns
            for col in (
                "dispatch_mode",
                "job_name",
                "job_args",
                "timezone",
                "start_at",
                "end_at",
                "until_at",
                "display_title",
                "calendar_event_id",
            ):
                assert col in columns, f"Missing scheduled_tasks.{col}"
            assert "prompt" in str(columns["dispatch_mode"])

            # default mode = prompt
            default_mode = conn.execute(
                text(
                    "INSERT INTO scheduled_tasks (name, cron, prompt) VALUES ('test-default', '*/5 * * * *', 'p') RETURNING dispatch_mode"
                )
            ).scalar_one()
            assert default_mode == "prompt"

            # job mode with job_name
            conn.execute(
                text(
                    "INSERT INTO scheduled_tasks (name, cron, dispatch_mode, job_name, job_args) VALUES ('test-job', '0 * * * *', 'job', 'sweep', '{}'::jsonb)"
                )
            )

            # constraint: job mode requires job_name
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO scheduled_tasks (name, cron, dispatch_mode) VALUES ('bad-job', '0 1 * * *', 'job')"
                    )
                )
    finally:
        engine.dispose()


def test_core_migration_backfills_scheduled_tasks_calendar_linkage_columns(postgres_container):
    """Core head repairs existing schema-scoped scheduled_tasks tables missing linkage columns."""
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    config = _build_alembic_config(db_url, chains=["core"], target_schema="messenger")
    _upgrade_selected(postgres_container, db_url, config, "core_103", chain="core")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text("DROP INDEX IF EXISTS messenger.ix_scheduled_tasks_calendar_event_id")
            )
            conn.execute(
                text(
                    "ALTER TABLE messenger.scheduled_tasks "
                    "DROP CONSTRAINT IF EXISTS scheduled_tasks_until_bounds_check"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE messenger.scheduled_tasks "
                    "DROP CONSTRAINT IF EXISTS scheduled_tasks_window_bounds_check"
                )
            )
            for column in (
                "calendar_event_id",
                "display_title",
                "until_at",
                "end_at",
                "start_at",
                "timezone",
            ):
                conn.execute(text(f"ALTER TABLE messenger.scheduled_tasks DROP COLUMN {column}"))
    finally:
        engine.dispose()

    _upgrade_selected(postgres_container, db_url, config, None, chain="core")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'messenger' AND table_name = 'scheduled_tasks'"
                )
            )
            columns = {str(row[0]) for row in rows}
            assert {
                "timezone",
                "start_at",
                "end_at",
                "until_at",
                "display_title",
                "calendar_event_id",
            }.issubset(columns)

            constraints = conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'messenger.scheduled_tasks'::regclass"
                )
            )
            constraint_names = {str(row[0]) for row in constraints}
            assert "scheduled_tasks_window_bounds_check" in constraint_names
            assert "scheduled_tasks_until_bounds_check" in constraint_names

            index_exists = conn.execute(
                text("SELECT to_regclass('messenger.ix_scheduled_tasks_calendar_event_id')")
            ).scalar_one()
            assert index_exists == "messenger.ix_scheduled_tasks_calendar_event_id"
    finally:
        engine.dispose()


def test_core_112_downgrade_preserves_baseline_scheduler_projection_columns(monkeypatch):
    """The core_112 repair migration must not remove fields that core_001 now owns."""
    migration_path = Path(
        "alembic/versions/core/core_112_scheduled_tasks_calendar_linkage_backfill.py"
    )
    spec = importlib.util.spec_from_file_location("core_112_calendar_linkage", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sql_statements: list[str] = []
    monkeypatch.setattr(mod.op, "execute", sql_statements.append)

    mod.downgrade()

    assert sql_statements == []


def test_core_calendar_tables_and_constraints(postgres_container):
    """Calendar tables support source lookup, window queries, idempotency keys; GIST indexes exist."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            source_id = conn.execute(
                text(
                    "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id) VALUES ('google:user-primary', 'provider', 'user', 'google', 'primary') RETURNING id"
                )
            ).scalar_one()
            # source_butler is now NOT NULL with no default — supply it explicitly.
            event_id = conn.execute(
                text(
                    "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at, source_butler) VALUES (:sid, 'evt-1', 'Session', 'UTC', now(), now() + interval '1 hour', 'health') RETURNING id"
                ),
                {"sid": source_id},
            ).scalar_one()
            assert event_id is not None

            # duplicate origin_ref
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at, source_butler) VALUES (:sid, 'evt-1', 'Dup', 'UTC', now() + interval '2 hours', now() + interval '3 hours', 'health')"
                    ),
                    {"sid": source_id},
                )

            # idempotency key
            conn.execute(
                text(
                    "INSERT INTO calendar_action_log (idempotency_key, action_type, source_id, event_id) VALUES ('req-123:create', 'create_event', :sid, :eid)"
                ),
                {"sid": source_id, "eid": event_id},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO calendar_action_log (idempotency_key, action_type) VALUES ('req-123:create', 'create_event')"
                    )
                )

            # GIST indexes
            event_idxs = {
                str(r[0])
                for r in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'calendar_events'"
                    )
                )
            }
            assert "ix_calendar_events_source_starts_at" in event_idxs
            assert "ix_calendar_events_time_window_gist" in event_idxs

            # New columns and table added by core_076
            cal_event_cols = {
                str(r[0])
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = 'public' AND table_name = 'calendar_events'"
                    )
                )
            }
            for col in ("source_butler", "source_session_id", "body"):
                assert col in cal_event_cols, f"Missing calendar_events.{col}"

            # calendar_event_entities junction table
            assert table_exists(db_url, "calendar_event_entities"), (
                "calendar_event_entities table should exist"
            )
            junction_idxs = {
                str(r[0])
                for r in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
                        " AND tablename = 'calendar_event_entities'"
                    )
                )
            }
            assert "idx_calendar_event_entities_entity" in junction_idxs
    finally:
        engine.dispose()


def test_alembic_version_tracking_and_schema_scoped(postgres_container):
    """alembic_version table has correct head revision; schema-scoped runs track in schema-local tables."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    assert table_exists(db_url, "alembic_version")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    engine.dispose()
    assert _latest_core_revision() in versions

    # Schema-scoped version tracking
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core", schema="general"))
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core", schema="health"))
    assert _table_exists_in_schema(db_url, "general", "alembic_version")
    assert _table_exists_in_schema(db_url, "health", "alembic_version")
    assert _table_exists_in_schema(db_url, "public", "alembic_version")


def test_insight_feedback_migration_shape_and_constraints(postgres_container):
    """core_241 keeps feedback typed and engagement attributable by category."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='insight_feedback'"
                    )
                )
            }
            assert {
                "insight_id",
                "dedup_family",
                "category",
                "origin_butler",
                "verdict",
                "snooze_until",
                "actor",
                "decided_at",
                "evidence_ref",
            } <= columns
            engagement_columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='insight_engagement'"
                    )
                )
            }
            assert {"category", "origin_butler"} <= engagement_columns
            conn.execute(
                text(
                    "INSERT INTO public.attention_ledger "
                    "(origin_butler, source, outcome, reason) "
                    "VALUES ('health', 'insight', 'expired', 'blocked_by:budget')"
                )
            )

            candidate_id = conn.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(origin_butler, priority, category, dedup_key, expires_at, message) "
                    "VALUES ('health', 50, 'Health', 'health:signal:today', now() + interval '1 day', 'x') "
                    "RETURNING id"
                )
            ).scalar_one()
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO public.insight_feedback "
                            "(insight_id, dedup_family, category, origin_butler, verdict, actor) "
                            "VALUES (:id, 'health:signal', 'Health', 'health', "
                            "'not_now', 'owner')"
                        ),
                        {"id": candidate_id},
                    )
    finally:
        engine.dispose()


def test_insight_feedback_rls_survives_bootstrap_replay(postgres_container):
    """Public-table grants cannot let non-Switchboard roles read or forge feedback."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            candidate_id = conn.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(origin_butler, priority, category, dedup_key, expires_at, message) "
                    "VALUES ('health', 50, 'Health', 'health:signal:today', "
                    "now() + interval '1 day', 'x') RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO public.insight_feedback "
                    "(insight_id, dedup_family, category, origin_butler, verdict, actor) "
                    "VALUES (:id, 'health:signal', 'Health', 'health', 'useful', 'owner')"
                ),
                {"id": candidate_id},
            )
    finally:
        engine.dispose()

    _replay_init_db(postgres_container, db_name, db_url)

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.insight_feedback'::regclass"
                )
            ).scalar_one()
            table_owner = conn.execute(
                text(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE oid = 'public.insight_feedback'::regclass"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert table_owner not in {*RUNTIME_ROLES.values(), "connector_writer"}

    assert (
        _execute_as_role(
            postgres_container,
            db_url,
            RUNTIME_ROLES["switchboard"],
            "SELECT count(*) FROM public.insight_feedback",
            scalar=True,
        )
        == 1
    )
    for role in (RUNTIME_ROLES["general"], "connector_writer"):
        with pytest.raises(ProgrammingError) as read_error:
            _execute_as_role(
                postgres_container,
                db_url,
                role,
                "SELECT count(*) FROM public.insight_feedback",
                scalar=True,
            )
        assert read_error.value.orig.pgcode == "42501"
        with pytest.raises(ProgrammingError) as write_error:
            _execute_as_role(
                postgres_container,
                db_url,
                role,
                "INSERT INTO public.insight_feedback "
                f"(insight_id, dedup_family, category, origin_butler, verdict, actor) VALUES "
                f"('{candidate_id}', 'health:signal', 'Health', 'health', 'useful', 'forged')",
            )
        assert write_error.value.orig.pgcode == "42501"

    async def owner_feedback_still_reaches_the_server_path() -> None:
        import asyncpg

        from butlers.tools.switchboard.insight.broker import record_insight_feedback

        async def as_switchboard(connection):
            await connection.execute('SET ROLE "butler_switchboard_rw"')

        pool = await asyncpg.create_pool(
            _native_url(_maintenance_url(postgres_container, db_url)),
            min_size=1,
            max_size=1,
            setup=as_switchboard,
        )
        try:
            result = await record_insight_feedback(
                pool,
                insight_id=str(candidate_id),
                verdict="useful",
                actor="owner",
            )
        finally:
            await pool.close()
        assert result["status"] == "recorded"

    asyncio.run(owner_feedback_still_reaches_the_server_path())


def test_insight_feedback_downgrade_tolerates_shared_table_replay(postgres_container):
    """Two schema-scoped core chains may remove the same public table in turn."""
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    general = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    health = _build_alembic_config(db_url, chains=["core"], target_schema="health")

    _upgrade_selected(postgres_container, db_url, general, "core_114", chain="core")
    _upgrade_selected(postgres_container, db_url, health, "core_114", chain="core")
    _upgrade_selected(postgres_container, db_url, general, "core_241", chain="core")
    _upgrade_selected(postgres_container, db_url, health, "core_241", chain="core")
    assert table_exists(db_url, "insight_feedback")

    _downgrade_selected(postgres_container, db_url, general, "core_240", revision="core_240")
    _downgrade_selected(postgres_container, db_url, health, "core_240", revision="core_240")
    assert not table_exists(db_url, "insight_feedback")


def test_core_acl_and_relationship_chain(postgres_container):
    """ACL: runtime roles exist, own-schema write allowed, cross-schema denied. relationship chain creates reminders."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    for role in RUNTIME_ROLES.values():
        assert _role_exists(db_url, role), f"expected role {role!r}"

    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core", schema="general"))
    # relationship chain
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="relationship"))
    # rel_007 renamed the live `reminders` table to `_reminders_backup` as a
    # safety net; rel_020 then dropped that dead backup table. After the full
    # relationship chain neither table should exist.
    assert not _table_exists_in_schema(db_url, "public", "reminders"), (
        "reminders table should have been renamed to _reminders_backup by rel_007"
    )
    assert not _table_exists_in_schema(db_url, "public", "_reminders_backup"), (
        "_reminders_backup table should have been dropped by rel_020"
    )
    # The relationship head (rel_035 today, the effective-time expand) runs after
    # the historical direct-DML migrations and keeps the deployed writer's legacy conflict target beside
    # the new occurrence index; no cutover migration is in the chain.
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            indexes = conn.execute(
                text(
                    "SELECT to_regclass('relationship.uq_ef_spo_active') IS NOT NULL, "
                    "to_regclass('relationship.uq_ef_spo_occurrence_active') IS NOT NULL"
                )
            ).one()
            versions = {
                row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))
            }
    finally:
        engine.dispose()
    assert tuple(indexes) == (True, True)
    # alembic_version also holds the core head here, so check membership of the
    # resolved relationship head rather than assert_at_chain_head's single row.
    assert get_chain_head("relationship") in versions
    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(text("CREATE TABLE general.acl_test (id INT PRIMARY KEY, note TEXT)"))
            conn.execute(text("CREATE TABLE health.acl_test (id INT PRIMARY KEY, note TEXT)"))
            conn.execute(text("CREATE TABLE public.acl_shared (id SERIAL PRIMARY KEY, note TEXT)"))
            conn.execute(text("INSERT INTO public.acl_shared (note) VALUES ('seed')"))
    finally:
        setup_engine.dispose()

    general_role = RUNTIME_ROLES["general"]
    _execute_as_role(
        postgres_container,
        db_url,
        general_role,
        "INSERT INTO general.state (key,value) VALUES ('acl-probe','{\"note\":\"ok\"}'::jsonb)",
    )
    assert (
        _execute_as_role(
            postgres_container,
            db_url,
            general_role,
            "SELECT value->>'note' FROM general.state WHERE key='acl-probe'",
            scalar=True,
        )
        == "ok"
    )
    for sql in (
        "INSERT INTO general.acl_test (id,note) VALUES (1,'unknown')",
        "SELECT note FROM public.acl_shared ORDER BY id LIMIT 1",
        "INSERT INTO public.acl_shared (note) VALUES ('shared-write')",
        "SELECT * FROM health.acl_test",
    ):
        with pytest.raises(ProgrammingError) as error:
            _execute_as_role(postgres_container, db_url, general_role, sql)
        assert error.value.orig.pgcode == "42501"
    # Unknown objects cannot be adopted by a later source finalization either.
    with migration_session(
        _maintenance_url(postgres_container, db_url), source_database="butlers"
    ) as connection:
        with pytest.raises(AuthorityError, match="not declared"):
            finalize_authority(connection, plans=(("core", "general", None),))


# ---------------------------------------------------------------------------
# Smoke-tier tests
# ---------------------------------------------------------------------------
# These tests are also marked @pytest.mark.smoke so they are selected by
# ``pytest -m smoke`` for the fast CI gate.  They are intentionally narrow —
# they assert the migration machinery works end-to-end without re-asserting
# table/schema presence already covered by the integration tests above.


@pytest.mark.smoke
def test_core_migration_smoke_empty_to_head(postgres_container):
    """Smoke: core chain runs from empty DB to head and alembic_version records the head revision.

    Confirms the migration entry-point works end-to-end.  Intentionally avoids
    re-checking individual tables or schema layout — those assertions live in
    ``test_core_migrations_tables_schemas_and_idempotency`` above.

    The factory runs the managed bootstrap contract, which creates
    ``butler_chronicler_rw`` even though this test intentionally omits the
    specialist relationship chain. Core's cross-schema grant must consequently
    tolerate absent ``relationship.entity_facts``.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            versions = [
                row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))
            ]
    finally:
        engine.dispose()

    expected_head = _latest_core_revision()
    assert expected_head in versions, (
        f"alembic_version should record the core head revision {expected_head!r} "
        f"after empty-to-head run; recorded: {versions}"
    )


@pytest.mark.smoke
def test_core_migration_smoke_downgrade_upgrade_round_trip(postgres_container):
    """Raw protected reverse refuses before effects, then head remains re-applicable.

    The older bounded roundtrip and full preserving R0/R1 operator population
    remain separate; this refusal cannot earn the supported reverse positive.
    """
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(_run_selected_head(postgres_container, db_url, chain="core"))

    bootstrap_config = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, db_name), chains=["core"]
    )
    before_versions = _stamped_versions(db_url)
    before_tables = _get_table_columns_sql(db_url)
    with pytest.raises(AuthorityError, match="Explicit preserving operator reverse is required"):
        _downgrade_selected(
            postgres_container,
            db_url,
            bootstrap_config,
            "core@-1",
            revision=None,
        )
    assert _stamped_versions(db_url) == before_versions
    assert _get_table_columns_sql(db_url) == before_tables
    _upgrade_selected(postgres_container, db_url, bootstrap_config, None, chain="core")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            versions = [
                row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))
            ]
    finally:
        engine.dispose()

    expected_head = _latest_core_revision()
    assert expected_head in versions, (
        f"alembic_version should record the core head revision {expected_head!r} "
        f"after protected refusal and head reapply; recorded: {versions}"
    )

    # Verify schema equivalence: the refused/reapplied DB must have an identical
    # table/column inventory to a fresh empty→head migration.  This catches
    # migrations whose downgrade path drops a column or table that upgrade
    # never recreates, leaving the schema in a state the alembic_version alone
    # cannot detect.
    fresh_db_name = migration_db_name()
    fresh_db_url = create_migration_db(postgres_container, fresh_db_name)
    asyncio.run(_run_selected_head(postgres_container, fresh_db_url, chain="core"))

    round_trip_tables = _get_table_columns_sql(db_url)
    fresh_tables = _get_table_columns_sql(fresh_db_url)
    only_round_trip = set(round_trip_tables) - set(fresh_tables)
    only_fresh = set(fresh_tables) - set(round_trip_tables)
    assert round_trip_tables == fresh_tables, (
        "Schema after refusal/reapply must be equivalent to a direct empty→head migration. "
        f"Tables only in round-trip DB: {only_round_trip!r}. "
        f"Tables only in fresh DB: {only_fresh!r}. "
        "This is the ordinary login's privilege-filtered metadata inventory. "
        "Check qualified object presence, ownership, effective/inherited privileges and "
        "per-creator default ACLs in a disposable database before attributing absence "
        "to asymmetric downgrade/upgrade teardown."
    )


# Current-schema event chains: core_013/044's historical fixed roster omitted QA.
# These controls run only on the genuine migrated Postgres lane.
def _apply_current_event_chains_revision(db_url: str, schema: str, operation: str) -> None:
    """Reapply the actual revision under an actual schema-scoped Alembic context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_280_current_schema_event_chains.py"
    )
    spec = importlib.util.spec_from_file_location(
        "core_280_current_schema_event_chains", migration_path
    )
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL search_path TO {_quote_ident(schema)}, public"))
            with Operations.context(MigrationContext.configure(conn)):
                getattr(revision, operation)()
    finally:
        engine.dispose()


def _event_chains_history(db_url: str, schema: str) -> list[dict]:
    """An independent committed connection reads the complete historical row values."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return list(
                conn.execute(
                    text(
                        f"SELECT to_jsonb(chain) FROM {_quote_ident(schema)}.event_chains AS chain "
                        "ORDER BY name"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("schema", ["qa", "general", None, "future_scheduler"])
def test_current_schema_event_chains_converge_and_preserve_history(postgres_container, schema):
    """Current tables, checks, indexes and no-op rollback preserve all five statuses."""
    from butlers.migrations import _build_alembic_config

    target = schema or "public"
    quoted = _quote_ident(target)
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        schemas={"core": schema} if schema else None,
        revisions={"core": "core_279"},
    )
    # This is a genuine predecessor counterfactual, not a hand-built relation.
    assert _table_exists_in_schema(db_url, target, "event_chains") is (target == "general")
    engine = create_engine(db_url)
    try:
        before = []
        if target == "general":
            with engine.begin() as conn:
                conn.execute(
                    text(f"""
                        INSERT INTO {quoted}.event_chains
                            (name, trigger_type, trigger_reference, actions, status, butler_name)
                        SELECT 'historic-' || status, 'calendar_event_end', 'old-reference',
                               '[{{"action_type":"notify","message":"history"}}]'::jsonb,
                               status, :owner
                        FROM unnest(ARRAY['active','paused','fired','failed','disabled']) AS status
                    """),
                    {"owner": target},
                )
            before = _event_chains_history(db_url, target)
        config = _build_alembic_config(db_url, ["core"], target_schema=schema)
        _upgrade_selected(postgres_container, db_url, config, "core_280", chain="core")
        assert _table_exists_in_schema(db_url, target, "event_chains")
        if before:
            assert _event_chains_history(db_url, target) == before
        else:
            with engine.begin() as conn:
                conn.execute(
                    text(f"""
                        INSERT INTO {quoted}.event_chains
                            (name, trigger_type, actions, status, butler_name)
                        SELECT 'historic-' || status, 'calendar_event_end', '[]'::jsonb,
                               status, :owner
                        FROM unnest(ARRAY['active','paused','fired','failed','disabled']) AS status
                    """),
                    {"owner": target},
                )
        history = _event_chains_history(db_url, target)
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = :schema AND table_name = 'event_chains'
                    """),
                    {"schema": target},
                ).scalars()
            )
            assert columns == {
                "id",
                "name",
                "trigger_type",
                "trigger_reference",
                "actions",
                "status",
                "butler_name",
                "created_at",
                "updated_at",
            }
            indexes = {
                row[0]: row[1]
                for row in conn.execute(
                    text("""
                        SELECT indexname, indexdef FROM pg_indexes
                        WHERE schemaname = :schema AND tablename = 'event_chains'
                    """),
                    {"schema": target},
                )
            }
            assert "UNIQUE" in indexes["idx_event_chains_name_butler"]
            assert "(name, butler_name)" in indexes["idx_event_chains_name_butler"]
            assert "(trigger_type)" in indexes["idx_event_chains_trigger_type"]
            assert "status = 'active'" in indexes["idx_event_chains_trigger_type"]
            assert "(butler_name)" in indexes["idx_event_chains_butler_name"]
        for trigger, status in (("unknown", "active"), ("deadline_passed", "unknown")):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(f"""
                            INSERT INTO {quoted}.event_chains
                                (name, trigger_type, actions, status, butler_name)
                            VALUES ('invalid', :trigger, '[]'::jsonb, :status, :owner)
                        """),
                        {"trigger": trigger, "status": status, "owner": target},
                    )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(f"""
                        INSERT INTO {quoted}.event_chains
                            (name, trigger_type, actions, butler_name)
                        VALUES ('historic-active', 'deadline_passed', '[]'::jsonb, :owner)
                    """),
                    {"owner": target},
                )
        _apply_current_event_chains_revision(db_url, target, "upgrade")
        assert _event_chains_history(db_url, target) == history
        _downgrade_selected(postgres_container, db_url, config, "core_279", revision="core_279")
        assert _event_chains_history(db_url, target) == history
        _upgrade_selected(postgres_container, db_url, config, "core_280", chain="core")
        assert _event_chains_history(db_url, target) == history
        if target in {"public", "future_scheduler"}:
            assert not _role_exists(db_url, f"butler_{target}_rw")
    finally:
        engine.dispose()


def test_current_schema_event_chains_widen_core013_status_without_rewriting(postgres_container):
    """The actual old core_013 constraint converges without rewriting calendar history."""
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        schemas={"core": "general"},
        revisions={"core": "core_013"},
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO general.event_chains (name, trigger_type, actions, status, butler_name)
                SELECT 'old-' || status, 'calendar_event_end', '[{"action_type":"notify"}]'::jsonb,
                       status, 'general'
                FROM unnest(ARRAY['active','fired','disabled']) AS status
            """)
            )
        history = _event_chains_history(db_url, "general")
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO general.event_chains
                        (name, trigger_type, actions, status, butler_name)
                    VALUES ('new-paused', 'deadline_passed', '[]'::jsonb, 'paused', 'general')
                """)
                )
        _apply_current_event_chains_revision(db_url, "general", "upgrade")
        assert _event_chains_history(db_url, "general") == history
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO general.event_chains (name, trigger_type, actions, status, butler_name)
                SELECT 'new-' || status, 'deadline_passed', '[]'::jsonb, status, 'general'
                FROM unnest(ARRAY['paused','failed']) AS status
            """)
            )
        expanded = _event_chains_history(db_url, "general")
        assert {row["status"] for row in expanded} == {
            "active",
            "paused",
            "fired",
            "failed",
            "disabled",
        }
        _apply_current_event_chains_revision(db_url, "general", "downgrade")
        _apply_current_event_chains_revision(db_url, "general", "upgrade")
        assert _event_chains_history(db_url, "general") == expanded
    finally:
        engine.dispose()


def test_current_schema_event_chains_qa_role_tick_and_foreign_write_refusal(postgres_container):
    """Real QA policy reads and scheduler tick work without granting foreign writes."""
    import asyncpg

    from butlers.core.scheduler import QaSchedulePolicy, read_qa_schedule_policy, tick
    from butlers.db import register_jsonb_codec

    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "switchboard"],
        schemas={"core": "qa", "switchboard": "switchboard"},
    )
    _replay_init_db(postgres_container, db_name, db_url)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO switchboard.butler_registry (name, endpoint_url)
                VALUES ('qa', 'http://qa:41100/mcp') ON CONFLICT (name) DO NOTHING
            """)
            )
            conn.execute(
                text("""
                UPDATE switchboard.butler_registry_control_plane
                SET policy_state = 'active', policy_provenance = 'operator' WHERE name = 'qa'
            """)
            )
        for role, destination in (("butler_general_rw", "qa"), ("butler_qa_rw", "general")):
            with pytest.raises(ProgrammingError) as error:
                _execute_as_role(
                    postgres_container,
                    db_url,
                    role,
                    f"INSERT INTO {destination}.event_chains "
                    "(name, trigger_type, actions, butler_name) "
                    "VALUES ('forbidden', 'deadline_passed', '[]'::jsonb, 'qa')",
                )
            assert error.value.orig.pgcode == "42501"

        async def exercise_qa():
            async def as_qa_role(conn):
                await conn.execute('SET ROLE "butler_qa_rw"')

            pool = await asyncpg.create_pool(
                _native_url(_maintenance_url(postgres_container, db_url)),
                min_size=1,
                max_size=1,
                init=register_jsonb_codec,
                setup=as_qa_role,
                server_settings={"search_path": "qa,public"},
            )
            try:
                assert await read_qa_schedule_policy(pool) is QaSchedulePolicy.ALLOW
                await pool.execute("""
                    INSERT INTO event_chains (name, trigger_type, actions, butler_name)
                    VALUES ('historical-calendar', 'calendar_event_end', '[]'::jsonb, 'qa')
                """)
                before = _event_chains_history(db_url, "qa")

                async def no_dispatch(**kwargs):
                    raise AssertionError("historical calendar chain must remain inert")

                assert await tick(pool, no_dispatch, butler_name="qa") == 0
                assert _event_chains_history(db_url, "qa") == before
                assert await pool.fetchval("SELECT count(*) FROM event_chains") == 1
                await pool.execute("DELETE FROM event_chains WHERE name = 'historical-calendar'")
                assert _event_chains_history(db_url, "qa") == []
            finally:
                await pool.close()

        asyncio.run(exercise_qa())
    finally:
        engine.dispose()
