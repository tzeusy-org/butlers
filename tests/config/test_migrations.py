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

REQUIRED_SCHEMAS = ("shared", "general", "health", "messenger", "relationship", "switchboard")
CORE_HEAD_REVISION = "core_006"
RUNTIME_ROLES = {
    "general": "butler_general_rw",
    "health": "butler_health_rw",
    "messenger": "butler_messenger_rw",
    "relationship": "butler_relationship_rw",
    "switchboard": "butler_switchboard_rw",
}


def _quote_ident(identifier: str) -> str:
    """Quote an identifier for SQL text construction."""
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists_in_schema(db_url: str, schema_name: str, table_name: str) -> bool:
    """Check whether a table exists in a specific schema."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = :s AND table_name = :t"
                ")"
            ),
            {"s": schema_name, "t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _schema_exists(db_url: str, schema_name: str) -> bool:
    """Check whether a schema exists in the database."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.schemata"
                "  WHERE schema_name = :s"
                ")"
            ),
            {"s": schema_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _schema_owner(db_url: str, schema_name: str) -> str | None:
    """Return schema owner role name, or None if schema does not exist."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT pg_catalog.pg_get_userbyid(n.nspowner) "
                "FROM pg_namespace n "
                "WHERE n.nspname = :s"
            ),
            {"s": schema_name},
        )
        owner = result.scalar()
    engine.dispose()
    return owner


def _current_user(db_url: str) -> str:
    """Return the current DB user for the connection."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_user"))
        user = result.scalar()
    engine.dispose()
    assert isinstance(user, str)
    return user


def _role_exists(db_url: str, role_name: str) -> bool:
    """Return True when role exists in pg_roles."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r)"),
            {"r": role_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    """Execute SQL after SET ROLE and optionally return scalar result."""
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


def test_core_migrations_create_tables(postgres_container):
    """Run core migrations and verify all core tables are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    assert table_exists(db_url, "state"), "state table should exist"
    assert table_exists(db_url, "scheduled_tasks"), "scheduled_tasks table should exist"
    assert table_exists(db_url, "sessions"), "sessions table should exist"
    assert table_exists(db_url, "route_inbox"), "route_inbox table should exist"
    assert table_exists(db_url, "butler_secrets"), "butler_secrets table should exist"
    assert table_exists(db_url, "calendar_sources"), "calendar_sources table should exist"
    assert table_exists(db_url, "calendar_events"), "calendar_events table should exist"
    assert table_exists(db_url, "calendar_event_instances"), (
        "calendar_event_instances table should exist"
    )
    assert table_exists(db_url, "calendar_sync_cursors"), "calendar_sync_cursors table should exist"
    assert table_exists(db_url, "calendar_action_log"), "calendar_action_log table should exist"
    assert not table_exists(db_url, "google_oauth_credentials"), (
        "legacy google_oauth_credentials table should not exist in target-state baseline"
    )

    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist"


def test_core_scheduled_task_dispatch_mode_columns_and_constraints(postgres_container):
    """scheduled_tasks should persist dispatch metadata and enforce mode constraints."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'scheduled_tasks'
                    """
                )
            )
            columns = {str(name): default for name, default in rows}
            assert "dispatch_mode" in columns
            assert "job_name" in columns
            assert "job_args" in columns
            assert "prompt" in str(columns["dispatch_mode"])

            default_mode = conn.execute(
                text(
                    """
                    INSERT INTO scheduled_tasks (name, cron, prompt)
                    VALUES ('dispatch-default-check', '*/5 * * * *', 'default prompt')
                    RETURNING dispatch_mode
                    """
                )
            ).scalar_one()
            assert default_mode == "prompt"

            dry_run = conn.execute(
                text(
                    """
                    INSERT INTO scheduled_tasks (name, cron, dispatch_mode, job_name, job_args)
                    VALUES (
                        'dispatch-job-check',
                        '0 * * * *',
                        'job',
                        'eligibility_sweep',
                        '{"dry_run": true}'::jsonb
                    )
                    RETURNING job_args ->> 'dry_run'
                    """
                )
            ).scalar_one()
            assert dry_run == "true"

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO scheduled_tasks (name, cron, dispatch_mode)
                        VALUES ('dispatch-job-missing-name', '0 1 * * *', 'job')
                        """
                    )
                )

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode)
                        VALUES ('dispatch-bad-mode', '0 2 * * *', 'bad', 'bad')
                        """
                    )
                )
    finally:
        engine.dispose()


def test_core_calendar_projection_tables_constraints_and_indexes(postgres_container):
    """Calendar projection tables should support source lookup, window queries, and idempotency."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            source_id = conn.execute(
                text(
                    """
                    INSERT INTO calendar_sources (
                        source_key, source_kind, lane, provider, calendar_id
                    )
                    VALUES ('google:user-primary', 'provider', 'user', 'google', 'primary')
                    RETURNING id
                    """
                )
            ).scalar_one()

            event_id = conn.execute(
                text(
                    """
                    INSERT INTO calendar_events (
                        source_id,
                        origin_ref,
                        title,
                        timezone,
                        starts_at,
                        ends_at
                    )
                    VALUES (
                        :source_id,
                        'evt-1',
                        'Planning Session',
                        'UTC',
                        now(),
                        now() + interval '1 hour'
                    )
                    RETURNING id
                    """
                ),
                {"source_id": source_id},
            ).scalar_one()
            assert event_id is not None

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO calendar_events (
                            source_id,
                            origin_ref,
                            title,
                            timezone,
                            starts_at,
                            ends_at
                        )
                        VALUES (
                            :source_id,
                            'evt-1',
                            'Duplicate Origin',
                            'UTC',
                            now() + interval '2 hours',
                            now() + interval '3 hours'
                        )
                        """
                    ),
                    {"source_id": source_id},
                )

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO calendar_events (
                            source_id,
                            origin_ref,
                            title,
                            timezone,
                            starts_at,
                            ends_at
                        )
                        VALUES (
                            :source_id,
                            'evt-bad-window',
                            'Bad Window',
                            'UTC',
                            now() + interval '2 hours',
                            now() + interval '1 hour'
                        )
                        """
                    ),
                    {"source_id": source_id},
                )

            conn.execute(
                text(
                    """
                    INSERT INTO calendar_action_log (
                        idempotency_key, action_type, source_id, event_id
                    )
                    VALUES ('req-123:create', 'create_event', :source_id, :event_id)
                    """
                ),
                {"source_id": source_id, "event_id": event_id},
            )

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO calendar_action_log (idempotency_key, action_type)
                        VALUES ('req-123:create', 'create_event')
                        """
                    )
                )

            event_indexes = {
                str(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'calendar_events'
                        """
                    )
                )
            }
            assert "ix_calendar_events_source_starts_at" in event_indexes
            assert "ix_calendar_events_time_window_gist" in event_indexes

            instance_indexes = {
                str(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'calendar_event_instances'
                        """
                    )
                )
            }
            assert "ix_calendar_event_instances_source_starts_at" in instance_indexes
            assert "ix_calendar_event_instances_time_window_gist" in instance_indexes
    finally:
        engine.dispose()


def test_core_scheduled_task_calendar_linkage_columns(postgres_container):
    """scheduled_tasks should include calendar projection linkage columns."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name, column_default, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'scheduled_tasks'
                    """
                )
            )
            columns = {
                str(name): {"default": default, "is_nullable": nullable}
                for name, default, nullable in rows
            }

            for required in (
                "timezone",
                "start_at",
                "end_at",
                "until_at",
                "display_title",
                "calendar_event_id",
            ):
                assert required in columns, f"Missing scheduled_tasks.{required}"
            assert columns["timezone"]["is_nullable"] == "NO"
            assert "UTC" in str(columns["timezone"]["default"])

            calendar_event_id = conn.execute(
                text(
                    """
                    INSERT INTO scheduled_tasks (
                        name, cron, prompt, timezone, start_at, end_at, until_at, display_title
                    )
                    VALUES (
                        'calendar-linkage-check',
                        '0 9 * * *',
                        'calendar-linked',
                        'America/New_York',
                        '2026-03-01T14:00:00Z'::timestamptz,
                        '2026-03-01T15:00:00Z'::timestamptz,
                        '2026-04-01T14:00:00Z'::timestamptz,
                        'Medication reminder'
                    )
                    RETURNING calendar_event_id
                    """
                )
            ).scalar_one()
            assert calendar_event_id is None
    finally:
        engine.dispose()


def test_core_002_adds_dispatch_mode_to_existing_table(postgres_container):
    """core_002 should add dispatch_mode columns to a pre-existing scheduled_tasks table."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # Simulate a legacy database: create the table WITHOUT dispatch_mode columns,
    # then stamp as core_001 so Alembic thinks core_001 already ran.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            conn.execute(
                text(
                    """
                    CREATE TABLE scheduled_tasks (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name TEXT NOT NULL UNIQUE,
                        cron TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'db',
                        enabled BOOLEAN NOT NULL DEFAULT true,
                        next_run_at TIMESTAMPTZ,
                        last_run_at TIMESTAMPTZ,
                        last_result JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            # Also create the other core tables so core_001 stamp is valid.
            conn.execute(
                text(
                    """
                    CREATE TABLE state (
                        key TEXT PRIMARY KEY,
                        value JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        version INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE sessions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        prompt TEXT NOT NULL,
                        trigger_source TEXT NOT NULL,
                        model TEXT,
                        success BOOLEAN,
                        error TEXT,
                        result TEXT,
                        tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
                        duration_ms INTEGER,
                        trace_id TEXT,
                        request_id TEXT,
                        cost JSONB,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        parent_session_id UUID,
                        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        completed_at TIMESTAMPTZ
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE route_inbox (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        route_envelope JSONB NOT NULL,
                        lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
                        processed_at TIMESTAMPTZ,
                        session_id UUID,
                        error TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE butler_secrets (
                        secret_key TEXT PRIMARY KEY,
                        secret_value TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'general',
                        description TEXT,
                        is_sensitive BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        expires_at TIMESTAMPTZ
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    # Stamp as core_001 so Alembic thinks it already ran.
    config = _build_alembic_config(db_url, chains=["core"])
    command.stamp(config, "core_001")

    # Now upgrade to head — core_002 should add the missing columns.
    command.upgrade(config, "core@head")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'scheduled_tasks'
                    """
                )
            )
            columns = {str(row[0]) for row in rows}
            assert "dispatch_mode" in columns, "dispatch_mode column should be added"
            assert "job_name" in columns, "job_name column should be added"
            assert "job_args" in columns, "job_args column should be added"
            assert "timezone" in columns, "timezone column should be added"
            assert "start_at" in columns, "start_at column should be added"
            assert "end_at" in columns, "end_at column should be added"
            assert "until_at" in columns, "until_at column should be added"
            assert "display_title" in columns, "display_title column should be added"
            assert "calendar_event_id" in columns, "calendar_event_id column should be added"

            # Verify constraints work.
            default_mode = conn.execute(
                text(
                    """
                    INSERT INTO scheduled_tasks (name, cron, prompt)
                    VALUES ('upgrade-test', '*/5 * * * *', 'test prompt')
                    RETURNING dispatch_mode
                    """
                )
            ).scalar_one()
            assert default_mode == "prompt"
    finally:
        engine.dispose()


def test_core_schema_bootstrap_owner_baseline(postgres_container):
    """Schema bootstrap sets owner baseline to migration user on fresh installs."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    expected_owner = _current_user(db_url)
    for schema in REQUIRED_SCHEMAS:
        assert _schema_owner(db_url, schema) == expected_owner


def test_relationship_reminder_calendar_projection_columns(postgres_container):
    """relationship reminders table should expose projection linkage columns."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    # rel_008 FKs contacts.entity_id -> entities(id) via search_path.
    # The full memory chain requires pgvector (unavailable in CI), so create
    # the entities table as a minimal prerequisite directly.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS entities (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        tenant_id TEXT NOT NULL,
                        canonical_name VARCHAR NOT NULL,
                        entity_type VARCHAR NOT NULL DEFAULT 'other'
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    asyncio.run(run_migrations(db_url, chain="relationship"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'reminders'
                    """
                )
            )
            columns = {str(name): nullable for name, nullable in rows}
            for required in (
                "next_trigger_at",
                "timezone",
                "until_at",
                "updated_at",
                "calendar_event_id",
            ):
                assert required in columns, f"Missing reminders.{required}"
            assert columns["timezone"] == "NO"
            assert columns["updated_at"] == "NO"
    finally:
        engine.dispose()


def test_migrations_idempotent(postgres_container):
    """Running migrations twice should not raise errors."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    # Second run should succeed without errors
    asyncio.run(run_migrations(db_url, chain="core"))

    assert table_exists(db_url, "state")
    assert table_exists(db_url, "scheduled_tasks")
    assert table_exists(db_url, "sessions")
    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema)


def test_upgrade_to_core_head_creates_required_schemas(postgres_container):
    """Upgrade to core head creates one-db schemas cleanly."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@head")

    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist after upgrade path"


def test_core_acl_runtime_role_isolation(postgres_container):
    """Core ACL migration enforces own-schema + shared access with cross-schema denial."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    # Seed existing objects after ACL migration to validate object-level grants.
    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE general.acl_general_existing (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE health.acl_health_existing (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE shared.acl_shared_existing (id SERIAL PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO shared.acl_shared_existing (note) VALUES ('seed')"))

            # Validate default privilege behavior for future objects created by owner.
            conn.execute(
                text("CREATE TABLE general.acl_general_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE health.acl_health_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO health.acl_health_future (id, note) VALUES (1, 'h1')"))
            conn.execute(
                text("CREATE TABLE shared.acl_shared_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO shared.acl_shared_future (id, note) VALUES (1, 's1')"))
    finally:
        setup_engine.dispose()

    for runtime_role in RUNTIME_ROLES.values():
        assert _role_exists(db_url, runtime_role), f"expected role {runtime_role!r} to exist"

    general_role = RUNTIME_ROLES["general"]

    _execute_as_role(
        db_url,
        general_role,
        "INSERT INTO general.acl_general_existing (id, note) VALUES (1, 'ok')",
    )
    own_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM general.acl_general_existing WHERE id = 1",
        scalar=True,
    )
    assert own_note == "ok"

    # Own-schema default privileges should apply to future owner-created objects.
    _execute_as_role(
        db_url,
        general_role,
        "INSERT INTO general.acl_general_future (id, note) VALUES (2, 'future-ok')",
    )

    # Shared schema is intentionally read-only for runtime roles.
    shared_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM shared.acl_shared_existing ORDER BY id LIMIT 1",
        scalar=True,
    )
    assert shared_note == "seed"

    shared_future_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM shared.acl_shared_future WHERE id = 1",
        scalar=True,
    )
    assert shared_future_note == "s1"

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            general_role,
            "INSERT INTO shared.acl_shared_existing (note) VALUES ('blocked')",
        )

    # Cross-butler schema access must be denied.
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(db_url, general_role, "SELECT * FROM health.acl_health_existing")

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(db_url, general_role, "SELECT * FROM health.acl_health_future")


def test_alembic_version_tracking(postgres_container):
    """After migration, alembic_version table should have the correct entry."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    assert table_exists(db_url, "alembic_version"), "alembic_version table should exist"

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result]
    engine.dispose()

    assert CORE_HEAD_REVISION in versions, (
        f"Expected revision {CORE_HEAD_REVISION!r} (current head) in {versions}"
    )


def test_schema_scoped_alembic_version_tracking_isolated(postgres_container):
    """Schema-scoped runs should track revisions in separate schema-local tables."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))

    assert _table_exists_in_schema(db_url, "general", "alembic_version")
    assert _table_exists_in_schema(db_url, "health", "alembic_version")
    assert not _table_exists_in_schema(db_url, "public", "alembic_version")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        general_versions = [
            row[0] for row in conn.execute(text("SELECT version_num FROM general.alembic_version"))
        ]
        health_versions = [
            row[0] for row in conn.execute(text("SELECT version_num FROM health.alembic_version"))
        ]
    engine.dispose()

    assert CORE_HEAD_REVISION in general_versions
    assert CORE_HEAD_REVISION in health_versions
