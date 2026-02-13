"""create_approval_events

Revision ID: approvals_002
Revises: approvals_001
Create Date: 2026-02-14 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_002"
down_revision = "approvals_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_id UUID REFERENCES pending_actions(id),
            rule_id UUID REFERENCES approval_rules(id),
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT,
            event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approval_events_link_check
                CHECK (action_id IS NOT NULL OR rule_id IS NOT NULL),
            CONSTRAINT approval_events_type_check
                CHECK (event_type IN (
                    'action_queued',
                    'action_auto_approved',
                    'action_approved',
                    'action_rejected',
                    'action_expired',
                    'action_execution_succeeded',
                    'action_execution_failed',
                    'rule_created',
                    'rule_revoked'
                ))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approval_events_action_id
            ON approval_events (action_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approval_events_rule_id
            ON approval_events (rule_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approval_events_occurred_at
            ON approval_events (occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approval_events_event_type
            ON approval_events (event_type)
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_approval_events_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'approval_events is append-only: % is not allowed', TG_OP;
        END;
        $$;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_approval_events_immutable ON approval_events
    """)
    op.execute("""
        CREATE TRIGGER trg_approval_events_immutable
        BEFORE UPDATE OR DELETE ON approval_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_approval_events_mutation()
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS trg_approval_events_immutable ON approval_events
    """)
    op.execute("DROP FUNCTION IF EXISTS prevent_approval_events_mutation")
    op.execute("DROP TABLE IF EXISTS approval_events")
