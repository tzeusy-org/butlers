"""core target-state baseline

Revision ID: core_001
Revises:
Create Date: 2026-02-20 00:00:00.000000

This revision is a full target-state baseline for fresh database installs.
It intentionally replaces the prior incremental core history.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_001"
down_revision = None
branch_labels = ("core",)
depends_on = None

# Required one-db schemas.
_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_SHARED_SCHEMA = "shared"
_REQUIRED_SCHEMAS = (_SHARED_SCHEMA, *_BUTLER_SCHEMAS)

# Runtime roles are schema-aligned.
_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _BUTLER_SCHEMAS}

_OWN_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES"
_OWN_SEQUENCE_PRIVILEGES = "USAGE, SELECT, UPDATE"
_SHARED_TABLE_PRIVILEGES = "SELECT"
_SHARED_SEQUENCE_PRIVILEGES = "USAGE, SELECT"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _current_database_name() -> str:
    bind = op.get_bind()
    current_db = bind.exec_driver_sql("SELECT current_database()").scalar_one()
    assert isinstance(current_db, str)
    return current_db


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute SQL while tolerating privilege/role availability differences."""
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"

    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
            WHEN undefined_object THEN
                NULL;
        END
        $$;
        """
    )


def _create_runtime_role_best_effort(role_name: str) -> None:
    quoted_role = _quote_ident(role_name)
    role_lit = _quote_literal(role_name)

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role_lit}) THEN
                EXECUTE 'CREATE ROLE {quoted_role} LOGIN';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
        """
    )


def _set_schema_owner_best_effort(schema: str) -> None:
    quoted_schema = _quote_ident(schema)
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE 'ALTER SCHEMA {quoted_schema} OWNER TO CURRENT_USER';
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
        """
    )


def _create_required_schemas() -> None:
    for schema in _REQUIRED_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        _set_schema_owner_best_effort(schema)


def _create_core_tables() -> None:
    # Shared state
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    # Scheduler
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            cron TEXT NOT NULL,
            prompt TEXT,
            dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
            job_name TEXT,
            job_args JSONB,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            until_at TIMESTAMPTZ,
            display_title TEXT,
            calendar_event_id UUID,
            source TEXT NOT NULL DEFAULT 'db',
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT scheduled_tasks_dispatch_mode_check
                CHECK (dispatch_mode IN ('prompt', 'job')),
            CONSTRAINT scheduled_tasks_dispatch_payload_check
                CHECK (
                    (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                    OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
                ),
            CONSTRAINT scheduled_tasks_window_bounds_check
                CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at),
            CONSTRAINT scheduled_tasks_until_bounds_check
                CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_calendar_event_id
        ON scheduled_tasks (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )

    # Session history and trace metadata
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_request_id
        ON sessions (request_id)
        """
    )

    # Route accept-then-process inbox
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS route_inbox (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_route_inbox_lifecycle_state
        ON route_inbox (lifecycle_state, received_at)
        """
    )

    # Generic shared secrets store
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS butler_secrets (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_butler_secrets_category
        ON butler_secrets (category)
        """
    )


def _apply_public_baseline_revokes(current_db: str) -> None:
    quoted_db = _quote_ident(current_db)

    _execute_best_effort(f"REVOKE ALL ON DATABASE {quoted_db} FROM PUBLIC")
    _execute_best_effort("REVOKE ALL ON SCHEMA public FROM PUBLIC")

    for schema in _REQUIRED_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        _execute_best_effort(f"REVOKE ALL ON SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} REVOKE ALL ON TABLES FROM PUBLIC"
        )
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            "REVOKE ALL ON SEQUENCES FROM PUBLIC"
        )
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        )


def _grant_connect_and_search_path(current_db: str, role_name: str, own_schema: str) -> None:
    quoted_db = _quote_ident(current_db)
    quoted_role = _quote_ident(role_name)
    quoted_own = _quote_ident(own_schema)
    quoted_shared = _quote_ident(_SHARED_SCHEMA)

    _execute_best_effort(
        f"GRANT CONNECT ON DATABASE {quoted_db} TO {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        (
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_db} "
            f"SET search_path = {quoted_own}, {quoted_shared}, public"
        ),
        role_name=role_name,
    )


def _grant_own_schema_privileges(schema: str, role_name: str) -> None:
    quoted_schema = _quote_ident(schema)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"GRANT USAGE, CREATE ON SCHEMA {quoted_schema} TO {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        f"GRANT {_OWN_TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_OWN_SEQUENCE_PRIVILEGES} "
            f"ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )


def _grant_shared_schema_read_privileges(role_name: str) -> None:
    quoted_schema = _quote_ident(_SHARED_SCHEMA)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )
    _execute_best_effort(
        f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        (
            f"GRANT {_SHARED_TABLE_PRIVILEGES} "
            f"ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_SHARED_SEQUENCE_PRIVILEGES} "
            f"ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )


def _revoke_cross_schema_privileges(own_schema: str, role_name: str) -> None:
    quoted_role = _quote_ident(role_name)

    for other_schema in _BUTLER_SCHEMAS:
        if other_schema == own_schema:
            continue

        quoted_schema = _quote_ident(other_schema)
        _execute_best_effort(
            f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )


def _apply_default_privileges(own_schema: str, role_name: str) -> None:
    quoted_role = _quote_ident(role_name)
    quoted_own_schema = _quote_ident(own_schema)
    quoted_shared_schema = _quote_ident(_SHARED_SCHEMA)

    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT {_OWN_TABLE_PRIVILEGES} ON TABLES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT {_OWN_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT EXECUTE ON FUNCTIONS TO {quoted_role}"
        ),
        role_name=role_name,
    )

    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"GRANT {_SHARED_TABLE_PRIVILEGES} ON TABLES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"GRANT {_SHARED_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
        ),
        role_name=role_name,
    )

    for other_schema in _BUTLER_SCHEMAS:
        if other_schema == own_schema:
            continue

        quoted_other_schema = _quote_ident(other_schema)
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE ALL ON TABLES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE ALL ON SEQUENCES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
            ),
            role_name=role_name,
        )


def _revoke_role_access(current_db: str, role_name: str) -> None:
    quoted_db = _quote_ident(current_db)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"REVOKE CONNECT ON DATABASE {quoted_db} FROM {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        f"ALTER ROLE {quoted_role} IN DATABASE {quoted_db} RESET search_path", role_name=role_name
    )
    _execute_best_effort(f"REVOKE ALL ON SCHEMA public FROM {quoted_role}", role_name=role_name)

    for schema in _REQUIRED_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        _execute_best_effort(
            f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE ALL ON TABLES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE ALL ON SEQUENCES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
            ),
            role_name=role_name,
        )


def _apply_runtime_acl() -> None:
    current_db = _current_database_name()
    _apply_public_baseline_revokes(current_db)

    for role_name in _RUNTIME_ROLES.values():
        _create_runtime_role_best_effort(role_name)

    for own_schema, role_name in _RUNTIME_ROLES.items():
        _grant_connect_and_search_path(current_db, role_name, own_schema)
        _grant_own_schema_privileges(own_schema, role_name)
        _grant_shared_schema_read_privileges(role_name)
        _revoke_cross_schema_privileges(own_schema, role_name)
        _apply_default_privileges(own_schema, role_name)


def upgrade() -> None:
    # Ensure UUID helpers are available for default PK generation.
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    _create_core_tables()
    _create_required_schemas()
    _apply_runtime_acl()


def downgrade() -> None:
    current_db = _current_database_name()
    for role_name in _RUNTIME_ROLES.values():
        _revoke_role_access(current_db, role_name)

    op.execute("DROP INDEX IF EXISTS ix_butler_secrets_category")
    op.execute("DROP TABLE IF EXISTS butler_secrets")

    op.execute("DROP INDEX IF EXISTS idx_route_inbox_lifecycle_state")
    op.execute("DROP TABLE IF EXISTS route_inbox")

    op.execute("DROP INDEX IF EXISTS idx_sessions_request_id")
    op.execute("DROP TABLE IF EXISTS sessions")

    op.execute("DROP TABLE IF EXISTS scheduled_tasks")
    op.execute("DROP TABLE IF EXISTS state")

    # Drop only when empty; keep schemas if module objects are present.
    for schema in reversed(_REQUIRED_SCHEMAS):
        quoted_schema = _quote_ident(schema)
        op.execute(
            f"""
            DO $$
            BEGIN
                EXECUTE 'DROP SCHEMA IF EXISTS {quoted_schema}';
            EXCEPTION
                WHEN dependent_objects_still_exist THEN
                    NULL;
                WHEN insufficient_privilege THEN
                    NULL;
            END
            $$;
            """
        )
