"""Tests for Alembic migration infrastructure using testcontainers."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from butlers.testing.migration import create_migration_db, migration_db_name, table_exists

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

REQUIRED_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
CORE_HEAD_REVISION = "core_067"
RUNTIME_ROLES = {
    "general": "butler_general_rw",
    "health": "butler_health_rw",
    "messenger": "butler_messenger_rw",
    "relationship": "butler_relationship_rw",
    "switchboard": "butler_switchboard_rw",
}


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


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
        "calendar_event_instances",
        "calendar_sync_cursors",
        "calendar_action_log",
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
            event_id = conn.execute(
                text(
                    "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at) VALUES (:sid, 'evt-1', 'Session', 'UTC', now(), now() + interval '1 hour') RETURNING id"
                ),
                {"sid": source_id},
            ).scalar_one()
            assert event_id is not None

            # duplicate origin_ref
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at) VALUES (:sid, 'evt-1', 'Dup', 'UTC', now() + interval '2 hours', now() + interval '3 hours')"
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
    assert CORE_HEAD_REVISION in versions

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
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'reminders'"
                )
            )
            cols = {str(r[0]) for r in rows}
            for required in ("contact_id", "message", "reminder_type", "cron", "due_at"):
                assert required in cols
    finally:
        engine.dispose()
