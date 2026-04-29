"""backfill_delivery_tables_current_schema: create delivery tables in target schema.

Revision ID: core_082
Revises: core_081
Create Date: 2026-04-29 00:00:00.000000

``core_012`` created delivery preference and deferred notification tables
for a fixed schema list.  Later roster schemas, including ``chronicler``,
run the core chain with their own search path but were not in that list.
This revision backfills the delivery tables into whichever schema is being
migrated so schema-scoped core runs remain future-compatible.
"""

from __future__ import annotations

from alembic import op

revision = "core_082"
down_revision = "core_081"
branch_labels = None
depends_on = None


def _grant_current_schema_table(table_name: str, privilege: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            target_schema TEXT := current_schema();
            runtime_role TEXT := 'butler_' || target_schema || '_rw';
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
                EXECUTE format(
                    'GRANT {privilege} ON TABLE %I.%I TO %I',
                    target_schema,
                    {table_name!r},
                    runtime_role
                );
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS delivery_preferences (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name         TEXT NOT NULL UNIQUE,
            quiet_hours_start   TIME NOT NULL DEFAULT '22:00',
            quiet_hours_end     TIME NOT NULL DEFAULT '07:00',
            timezone            TEXT NOT NULL DEFAULT 'UTC',
            batch_low_priority  BOOLEAN NOT NULL DEFAULT true,
            batch_delivery_time TIME NOT NULL DEFAULT '07:00',
            override_channels   JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    _grant_current_schema_table(
        "delivery_preferences",
        "SELECT, INSERT, UPDATE, DELETE",
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS deferred_notifications (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name  TEXT NOT NULL,
            channel      TEXT NOT NULL,
            message      TEXT NOT NULL,
            priority     TEXT NOT NULL DEFAULT 'medium',
            envelope     JSONB NOT NULL,
            deferred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            deliver_at   TIMESTAMPTZ NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            delivered_at TIMESTAMPTZ,
            CONSTRAINT chk_deferred_notifications_status
                CHECK (status IN ('pending', 'delivered', 'expired', 'cancelled')),
            CONSTRAINT chk_deferred_notifications_priority
                CHECK (priority IN ('high', 'medium', 'low'))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_deferred_notifications_pending_deliver_at
        ON deferred_notifications (deliver_at ASC)
        WHERE status = 'pending'
    """)
    _grant_current_schema_table(
        "deferred_notifications",
        "SELECT, INSERT, UPDATE, DELETE",
    )


def downgrade() -> None:
    # Intentional no-op: this revision uses CREATE TABLE IF NOT EXISTS to
    # backfill schemas that core_012 missed, but in schemas where core_012
    # already created these tables (the original fixed schema list) a DROP
    # here would destroy live data. Leave the tables in place on downgrade
    # and let core_012's own downgrade handle removal where appropriate.
    pass
