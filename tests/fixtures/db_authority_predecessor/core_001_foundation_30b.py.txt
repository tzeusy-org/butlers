"""core foundation baseline

Revision ID: core_001
Revises:
Create Date: 2026-03-26 00:00:00.000000

This revision is a full target-state baseline for fresh database installs.
It intentionally replaces the prior incremental core history.

Creates:
  - PostgreSQL extensions (pgcrypto, uuid-ossp, vector, pg_trgm)
  - Per-butler schemas (role schemas + connectors)
  - public.ingestion_events table and indexes
  - Per-butler core tables: state, scheduled_tasks, sessions,
    route_inbox, butler_secrets, session_process_logs, corrections
  - Runtime roles with schema-isolated ACL
  - connector_writer role with connectors schema access
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_001"
down_revision = None
branch_labels = ("core",)
depends_on = None

# Butler schemas split into role-owned schemas and full butler schemas
# (which include the connectors namespace).
_ROLE_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)
_BUTLER_SCHEMAS = (*_ROLE_SCHEMAS, "connectors")
_REQUIRED_SCHEMAS = _BUTLER_SCHEMAS

# Runtime roles are schema-aligned (one per role schema, not connectors).
_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _ROLE_SCHEMAS}

_CONNECTOR_ROLE = "connector_writer"

_OWN_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES"
_OWN_SEQUENCE_PRIVILEGES = "USAGE, SELECT, UPDATE"
_PUBLIC_TABLE_PRIVILEGES = "SELECT"
_PUBLIC_SEQUENCE_PRIVILEGES = "USAGE, SELECT"

_DEFAULT_TTL_INTERVAL = "14 days"


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


def _create_ingestion_events() -> None:
    """Create public.ingestion_events — the canonical ingest record table."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ingestion_events (
            id                       UUID PRIMARY KEY,
            received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_channel           TEXT NOT NULL,
            source_provider          TEXT NOT NULL,
            source_endpoint_identity TEXT NOT NULL,
            source_sender_identity   TEXT,
            source_thread_identity   TEXT,
            external_event_id        TEXT NOT NULL,
            dedupe_key               TEXT NOT NULL UNIQUE,
            dedupe_strategy          TEXT NOT NULL,
            ingestion_tier           TEXT NOT NULL,
            policy_tier              TEXT NOT NULL,
            triage_decision          TEXT,
            triage_target            TEXT,
            status                   TEXT NOT NULL DEFAULT 'ingested',
            error_detail             TEXT,
            CONSTRAINT ck_ingestion_events_status
                CHECK (status IN ('ingested', 'failed', 'replay_pending'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_received_at
        ON public.ingestion_events (received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_source_channel
        ON public.ingestion_events (source_channel, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_status
        ON public.ingestion_events (status)
        WHERE status != 'ingested'
        """
    )


def _create_core_tables() -> None:
    # State store (KV JSONB)
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
            description TEXT,
            location TEXT,
            calendar_event_id TEXT,
            complexity TEXT DEFAULT 'medium',
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
            request_id TEXT NOT NULL,
            cost JSONB,
            input_tokens INTEGER,
            output_tokens INTEGER,
            parent_session_id UUID,
            ingestion_event_id UUID REFERENCES public.ingestion_events(id),
            complexity TEXT DEFAULT 'medium',
            resolution_source TEXT DEFAULT 'toml_fallback',
            healing_fingerprint TEXT,
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sessions_ingestion_event_id
        ON sessions (ingestion_event_id)
        WHERE ingestion_event_id IS NOT NULL
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

    # Generic secrets store
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

    # Session process logs (TTL-managed diagnostics)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS session_process_logs (
            session_id UUID PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            pid INTEGER,
            exit_code INTEGER,
            command TEXT,
            stderr TEXT,
            runtime_type TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '{_DEFAULT_TTL_INTERVAL}'
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_process_logs_expires_at
        ON session_process_logs (expires_at)
        """
    )

    # Corrections audit trail
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corrections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            correction_type TEXT NOT NULL CHECK (correction_type IN (
                'data_correction', 'memory_deletion', 'misroute', 'action_reversal'
            )),
            target_session_id UUID NOT NULL REFERENCES sessions(id),
            correcting_session_id UUID NOT NULL REFERENCES sessions(id),
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'applied', 'partially_applied', 'failed'
            )),
            summary TEXT NOT NULL,
            original_data_snapshot JSONB,
            correction_details JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_corrections_target_session_id
        ON corrections (target_session_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_corrections_correcting_session_id_created_at
        ON corrections (correcting_session_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_corrections_target_session_id_created_at
        ON corrections (target_session_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_corrections_correction_type
        ON corrections (correction_type)
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

    _execute_best_effort(
        f"GRANT CONNECT ON DATABASE {quoted_db} TO {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        (
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_db} "
            f"SET search_path = {quoted_own}, public"
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


def _grant_public_schema_read_privileges(role_name: str) -> None:
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA public TO {quoted_role}",
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_PUBLIC_TABLE_PRIVILEGES} "
            f"ON ALL TABLES IN SCHEMA public TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_PUBLIC_SEQUENCE_PRIVILEGES} "
            f"ON ALL SEQUENCES IN SCHEMA public TO {quoted_role}"
        ),
        role_name=role_name,
    )


def _revoke_cross_schema_privileges(own_schema: str, role_name: str) -> None:
    quoted_role = _quote_ident(role_name)

    for other_schema in _ROLE_SCHEMAS:
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

    # Default privileges on public schema: read-only for butler roles.
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT {_PUBLIC_TABLE_PRIVILEGES} ON TABLES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT {_PUBLIC_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
        ),
        role_name=role_name,
    )

    for other_schema in _ROLE_SCHEMAS:
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


def _apply_connector_writer_role() -> None:
    """Create connector_writer role with USAGE+CREATE on connectors schema."""
    _create_runtime_role_best_effort(_CONNECTOR_ROLE)

    _execute_best_effort(
        f"GRANT USAGE, CREATE ON SCHEMA {_quote_ident('connectors')}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES"
        f" IN SCHEMA {_quote_ident('connectors')}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {_quote_ident('connectors')}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote_ident('connectors')}"
        f" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote_ident('connectors')}"
        f" GRANT EXECUTE ON FUNCTIONS TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA public TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        f" GRANT SELECT ON TABLES TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
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
        _grant_public_schema_read_privileges(role_name)
        _revoke_cross_schema_privileges(own_schema, role_name)
        _apply_default_privileges(own_schema, role_name)

    _apply_connector_writer_role()


def upgrade() -> None:
    # IMPORTANT: The following PostgreSQL extensions MUST be installed by a
    # superuser BEFORE running migrations:
    #
    #   CREATE EXTENSION IF NOT EXISTS "pgcrypto";
    #   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    #   CREATE EXTENSION IF NOT EXISTS "vector";      -- pgvector
    #   CREATE EXTENSION IF NOT EXISTS "pg_trgm";
    #
    # These are NOT created here because the migration user typically lacks
    # superuser privileges.  See scripts/init-db.sql or your DB provisioning
    # playbook.

    _create_required_schemas()
    _create_ingestion_events()
    _create_core_tables()
    _apply_runtime_acl()


def downgrade() -> None:
    current_db = _current_database_name()

    # Revoke connector_writer access.
    _revoke_role_access(current_db, _CONNECTOR_ROLE)

    # Revoke butler role access.
    for role_name in _RUNTIME_ROLES.values():
        _revoke_role_access(current_db, role_name)

    # Drop per-butler tables (reverse creation order).
    op.execute("DROP INDEX IF EXISTS idx_corrections_correction_type")
    op.execute("DROP INDEX IF EXISTS idx_corrections_target_session_id_created_at")
    op.execute("DROP INDEX IF EXISTS idx_corrections_correcting_session_id_created_at")
    op.execute("DROP INDEX IF EXISTS idx_corrections_target_session_id")
    op.execute("DROP TABLE IF EXISTS corrections")

    op.execute("DROP INDEX IF EXISTS idx_session_process_logs_expires_at")
    op.execute("DROP TABLE IF EXISTS session_process_logs")

    op.execute("DROP INDEX IF EXISTS ix_butler_secrets_category")
    op.execute("DROP TABLE IF EXISTS butler_secrets")

    op.execute("DROP INDEX IF EXISTS idx_route_inbox_lifecycle_state")
    op.execute("DROP TABLE IF EXISTS route_inbox")

    op.execute("DROP INDEX IF EXISTS ix_sessions_ingestion_event_id")
    op.execute("DROP INDEX IF EXISTS idx_sessions_request_id")
    op.execute("DROP TABLE IF EXISTS sessions")

    op.execute("DROP INDEX IF EXISTS ix_scheduled_tasks_calendar_event_id")
    op.execute("DROP TABLE IF EXISTS scheduled_tasks")

    op.execute("DROP TABLE IF EXISTS state")

    # Drop public.ingestion_events.
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_status")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_source_channel")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_received_at")
    op.execute("DROP TABLE IF EXISTS public.ingestion_events")

    # Drop schemas when empty; keep if module objects are present.
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
