"""Converge event chains in the actual current core schema.

Revision ID: core_280
Revises: core_279

core_013 and core_044 used a fixed roster which omitted QA and later schemas.
The scheduler now reads its actual schema directly. Install the same table,
constraints and indexes there, including core_044's expanded status domain.
Existing rows, including inert historical calendar chains, are never rewritten.

The predecessor is the adopted head in this source snapshot. Actual public
chain composition must be reconciled before publication if another core
revision lands first; an unlanded migration is not a dependency.
"""

from __future__ import annotations

from alembic import op

revision = "core_280"
down_revision = "core_279"
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
                CREATE TABLE IF NOT EXISTS %I.event_chains (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_reference TEXT,
                    actions JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    butler_name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT chk_event_chains_trigger_type CHECK (
                        trigger_type IN (
                            'calendar_event_end', 'deadline_passed', 'deadline_threshold'
                        )
                    ),
                    CONSTRAINT chk_event_chains_status CHECK (
                        status IN ('active', 'paused', 'fired', 'failed', 'disabled')
                    )
                )
            $table$, own_schema);

            -- Widen an existing core_013 status constraint without changing a row.
            -- Reapplication has the same constraint and leaves all data untouched.
            EXECUTE format(
                'ALTER TABLE %I.event_chains '
                'DROP CONSTRAINT IF EXISTS chk_event_chains_status', own_schema
            );
            EXECUTE format(
                'ALTER TABLE %I.event_chains '
                'ADD CONSTRAINT chk_event_chains_status CHECK '
                '(status IN (''active'', ''paused'', ''fired'', ''failed'', ''disabled''))',
                own_schema
            );
            EXECUTE format(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_event_chains_name_butler '
                'ON %I.event_chains (name, butler_name)', own_schema
            );
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_event_chains_trigger_type '
                'ON %I.event_chains (trigger_type) WHERE status = ''active''', own_schema
            );
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_event_chains_butler_name '
                'ON %I.event_chains (butler_name)', own_schema
            );

            -- Reuse only the current schema's real runtime role. Public-only and
            -- future-schema migrations do not invent roles or grant foreign access.
            IF own_schema <> 'public'
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = own_role)
            THEN
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.event_chains TO %I',
                    own_schema, own_role
                );
            END IF;
        END
        $migration$;
    """)


def downgrade() -> None:
    # Like core_082, this is a data-safe convergence migration. The table or
    # expanded statuses may predate this revision; dropping/coercing them would
    # destroy historical chains or invalidate an older scheduler's data.
    pass
