"""Converge butler_logs in the actual current core schema.

Revision ID: core_281
Revises: core_280

core_089 used a fixed roster and core_090 patched in chronicler alone, so later
schemas (concierge, calendar) never received ``butler_logs`` and every log write
there fails. Install the same table and indexes in the schema this core chain
runs in, plus the schema's own runtime-role grants. Existing tables and rows are
never rewritten.
"""

from __future__ import annotations

from alembic import op

revision = "core_281"
down_revision = "core_280"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $migration$
        DECLARE
            own_schema text := current_schema();
            own_role text := 'butler_' || own_schema || '_rw';
        BEGIN
            EXECUTE format($table$
                CREATE TABLE IF NOT EXISTS %I.butler_logs (
                    id         BIGSERIAL PRIMARY KEY,
                    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    level      VARCHAR NOT NULL
                                   CHECK (level IN ('DEBUG','INFO','WARN','ERROR')),
                    msg        TEXT NOT NULL,
                    source     VARCHAR,
                    request_id UUID,
                    metadata   JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            $table$, own_schema);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS butler_logs_ts ON %I.butler_logs (ts DESC)',
                own_schema
            );
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS butler_logs_level ON %I.butler_logs (level)',
                own_schema
            );
            -- Reuse only the current schema's real runtime role. Public-only and
            -- future-schema migrations do not invent roles or grant foreign access.
            IF own_schema <> 'public'
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = own_role)
            THEN
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.butler_logs TO %I',
                    own_schema, own_role
                );
                EXECUTE format(
                    'GRANT USAGE, SELECT, UPDATE ON SEQUENCE %I.butler_logs_id_seq TO %I',
                    own_schema, own_role
                );
            END IF;
        END
        $migration$;
    """)


def downgrade() -> None:
    # Like core_280, a data-safe convergence migration: the table usually
    # predates this revision (core_089/core_090), so dropping it would destroy
    # historical logs.
    pass
