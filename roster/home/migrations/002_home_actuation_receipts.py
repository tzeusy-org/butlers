"""Add durable physical-actuation receipts to the HA command ledger.

Revision ID: home_002
Revises: home_001
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "home_002"
down_revision = "home_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS attempt_id UUID")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS risk TEXT")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS actor TEXT")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS session_id UUID")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS approval_id UUID")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS requested_state JSONB")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS observed_state JSONB")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS status TEXT")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS rollback_hint JSONB")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS failure_reason TEXT")
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_ha_command_log_actuation_risk'
                  AND conrelid = 'ha_command_log'::regclass
            ) THEN
                ALTER TABLE ha_command_log
                    ADD CONSTRAINT ck_ha_command_log_actuation_risk
                    CHECK (risk IS NULL OR risk IN (
                        'safe', 'reversible', 'consequential', 'protected'
                    ));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_ha_command_log_actuation_status'
                  AND conrelid = 'ha_command_log'::regclass
            ) THEN
                ALTER TABLE ha_command_log
                    ADD CONSTRAINT ck_ha_command_log_actuation_status
                    CHECK (status IS NULL OR status IN (
                        'attempting', 'succeeded', 'failed', 'unverified'
                    ));
            END IF;
        END
        $$;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ha_command_log_attempt_id
            ON ha_command_log (attempt_id)
            WHERE attempt_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ha_command_log_status_issued_at
            ON ha_command_log (status, issued_at DESC)
            WHERE status IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ha_command_log_status_issued_at")
    op.execute("DROP INDEX IF EXISTS ux_ha_command_log_attempt_id")
    op.execute(
        "ALTER TABLE ha_command_log DROP CONSTRAINT IF EXISTS ck_ha_command_log_actuation_status"
    )
    op.execute(
        "ALTER TABLE ha_command_log DROP CONSTRAINT IF EXISTS ck_ha_command_log_actuation_risk"
    )
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS completed_at")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS failure_reason")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS rollback_hint")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS observed_state")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS requested_state")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS approval_id")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS session_id")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS actor")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS risk")
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS attempt_id")
