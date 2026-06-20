"""Tests for Alembic migration infrastructure using testcontainers."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from alembic import command
from butlers.testing.migration import create_migration_db, migration_db_name, table_exists

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


def _latest_core_revision() -> str:
    core_dir = Path("alembic/versions/core")
    revisions: list[tuple[int, str]] = []
    for path in core_dir.glob("core_*.py"):
        match = re.match(r"core_(\d+)", path.stem)
        if match is not None:
            revisions.append((int(match.group(1)), f"core_{match.group(1)}"))

    if not revisions:
        raise AssertionError("No core migrations found")

    return max(revisions, key=lambda item: item[0])[1]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _get_table_columns_sql(db_url: str) -> dict[str, set[str]]:
    """Return ``{schema.table → {column, ...}}`` for all user tables via information_schema.

    Excludes system schemas (pg_catalog, information_schema, pg_toast).  Used to
    compare schema structure between two databases without relying on alembic
    version strings.
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


def _current_user(db_url: str) -> str:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_user"))
        user = result.scalar()
    engine.dispose()
    assert isinstance(user, str)
    return user


def _schema_owner(db_url: str, schema_name: str) -> str | None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT pg_catalog.pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = :s"
            ),
            {"s": schema_name},
        )
        owner = result.scalar()
    engine.dispose()
    return owner


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
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


def test_core_migrations_tables_schemas_and_idempotency(postgres_container):
    """Core migrations create all required tables and schemas; idempotent on second run."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

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
    asyncio.run(run_migrations(db_url, chain="core"))
    assert table_exists(db_url, "state")

    # Schema owner baseline
    expected_owner = _current_user(db_url)
    for schema in REQUIRED_SCHEMAS:
        assert _schema_owner(db_url, schema) == expected_owner


def test_core_migrations_seed_permissions_vocabulary(postgres_container):
    """core@head seeds public.permissions so the matrix renders non-empty, and the
    runtime enforcer (check_permission) honours a revoked grant against real rows."""
    import asyncpg

    from butlers.core.permissions import (
        SPAWN_PERMISSION,
        check_permission,
        require_permission,
    )
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

    # Matrix vocabulary is seeded (non-empty): both axes have real options.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM public.permissions")).scalar_one()
            assert total > 0, "permissions matrix must be seeded non-empty"
            distinct_butlers = conn.execute(
                text("SELECT COUNT(DISTINCT butler) FROM public.permissions")
            ).scalar_one()
            distinct_perms = conn.execute(
                text("SELECT COUNT(DISTINCT permission) FROM public.permissions")
            ).scalar_one()
            assert distinct_butlers >= 2
            assert distinct_perms >= 1
            # The enforced spawn permission is part of the seeded vocabulary.
            spawn_rows = conn.execute(
                text("SELECT COUNT(*) FROM public.permissions WHERE permission = :p"),
                {"p": SPAWN_PERMISSION},
            ).scalar_one()
            assert spawn_rows >= 2
    finally:
        engine.dispose()

    # Runtime enforcer against a real asyncpg pool: seeded grant allows, an explicit
    # revoke denies (deny BLOCKS), and an unknown butler default-allows.
    async def _exercise_enforcer() -> None:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            # Seeded default → allowed.
            allowed = await check_permission(pool, "chronicler", SPAWN_PERMISSION)
            assert allowed.allowed is True

            # Revoke chronicler's spawn grant; enforcer must now deny + require_permission raises.
            await pool.execute(
                "UPDATE public.permissions SET granted = FALSE, reason = 'test-revoke' "
                "WHERE butler = 'chronicler' AND permission = $1",
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
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core", schema="chronicler"))

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

    command.upgrade(relationship_core, "core_076")
    command.upgrade(switchboard_core, "core_076")
    command.upgrade(switchboard_chain, "switchboard@head")

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
            db_url,
            RUNTIME_ROLES["relationship"],
            "SELECT COUNT(*) FROM switchboard.message_inbox",
            scalar=True,
        )

    relationship_core = _build_alembic_config(db_url, chains=["core"], target_schema="relationship")
    command.upgrade(relationship_core, "core@head")

    count = _execute_as_role(
        db_url,
        RUNTIME_ROLES["relationship"],
        "SELECT COUNT(*) FROM switchboard.message_inbox",
        scalar=True,
    )
    assert count == 1


def test_switchboard_runtime_role_can_ensure_message_inbox_partitions(postgres_container):
    """switchboard runtime role can create inbox partitions without owning the parent table."""
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    switchboard_core = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    switchboard_chain = _build_alembic_config(
        db_url, chains=["switchboard"], target_schema="switchboard"
    )

    command.upgrade(switchboard_core, "core@head")
    command.upgrade(switchboard_chain, "switchboard@head")

    assert _function_is_security_definer(
        db_url,
        "switchboard",
        "switchboard_message_inbox_ensure_partition",
    )

    partition_name = _execute_as_role(
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
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

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
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

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
    command.upgrade(config, "core_103")

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

    command.upgrade(config, "core@head")

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
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

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
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

    assert table_exists(db_url, "alembic_version")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    engine.dispose()
    assert _latest_core_revision() in versions

    # Schema-scoped version tracking
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))
    assert _table_exists_in_schema(db_url, "general", "alembic_version")
    assert _table_exists_in_schema(db_url, "health", "alembic_version")
    assert _table_exists_in_schema(db_url, "public", "alembic_version")


def test_core_acl_and_relationship_chain(postgres_container):
    """ACL: runtime roles exist, own-schema write allowed, cross-schema denied. relationship chain creates reminders."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

    for role in RUNTIME_ROLES.values():
        assert _role_exists(db_url, role), f"expected role {role!r}"

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
        db_url, general_role, "INSERT INTO general.acl_test (id, note) VALUES (1, 'ok')"
    )
    assert (
        _execute_as_role(
            db_url, general_role, "SELECT note FROM general.acl_test WHERE id = 1", scalar=True
        )
        == "ok"
    )
    assert (
        _execute_as_role(
            db_url,
            general_role,
            "SELECT note FROM public.acl_shared ORDER BY id LIMIT 1",
            scalar=True,
        )
        == "seed"
    )
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url, general_role, "INSERT INTO public.acl_shared (note) VALUES ('blocked')"
        )
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(db_url, general_role, "SELECT * FROM health.acl_test")

    # relationship chain
    asyncio.run(run_migrations(db_url, chain="relationship"))
    # rel_007 renamed the live `reminders` table to `_reminders_backup` as a
    # safety net; rel_020 then dropped that dead backup table. After the full
    # relationship chain neither table should exist.
    assert not _table_exists_in_schema(db_url, "public", "reminders"), (
        "reminders table should have been renamed to _reminders_backup by rel_007"
    )
    assert not _table_exists_in_schema(db_url, "public", "_reminders_backup"), (
        "_reminders_backup table should have been dropped by rel_020"
    )


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
    """
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

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
    """Smoke: latest core revision survives a one-step downgrade/upgrade round-trip.

    Confirms that the most recent migration has a coherent downgrade path and
    that down-by-one followed by up-to-head leaves alembic_version at the
    expected head revision.
    """
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))

    config = _build_alembic_config(db_url, chains=["core"])
    # Step back one revision from head, then restore.
    command.downgrade(config, "core@-1")
    command.upgrade(config, "core@head")

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
        f"after down/up round-trip; recorded: {versions}"
    )

    # Verify schema equivalence: the round-tripped DB must have an identical
    # table/column inventory to a fresh empty→head migration.  This catches
    # migrations whose downgrade path drops a column or table that upgrade
    # never recreates, leaving the schema in a state the alembic_version alone
    # cannot detect.
    fresh_db_name = migration_db_name()
    fresh_db_url = create_migration_db(postgres_container, fresh_db_name)
    asyncio.run(run_migrations(fresh_db_url, chain="core"))

    round_trip_tables = _get_table_columns_sql(db_url)
    fresh_tables = _get_table_columns_sql(fresh_db_url)
    only_round_trip = set(round_trip_tables) - set(fresh_tables)
    only_fresh = set(fresh_tables) - set(round_trip_tables)
    assert round_trip_tables == fresh_tables, (
        "Schema after round-trip must be equivalent to a direct empty→head migration. "
        f"Tables only in round-trip DB: {only_round_trip!r}. "
        f"Tables only in fresh DB: {only_fresh!r}. "
        "Check that the most recent migration's downgrade() is symmetric with its upgrade()."
    )
