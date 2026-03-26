"""create_autonomy_tables: add autonomy_approval_history and autonomy_suggestions

Revision ID: approvals_004
Revises: approvals_003
Create Date: 2026-03-26 00:00:00.000000

Adds two tables for the progressive autonomy ladder:
- autonomy_approval_history: tracks every manual approval for frequency counting
  and velocity tracking.
- autonomy_suggestions: stores pending/confirmed/dismissed promotion and demotion
  suggestions with their lifecycle state.

Also extends the approval_events_type_check constraint to include the seven new
autonomy lifecycle event types, and relaxes approval_events_link_check to allow
suggestion lifecycle events to have both action_id and rule_id as NULL.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_004"
down_revision = "approvals_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- autonomy_approval_history -------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS autonomy_approval_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pattern_fingerprint VARCHAR(64) NOT NULL,
            tool_name TEXT NOT NULL,
            tool_args JSONB NOT NULL,
            action_id UUID REFERENCES pending_actions(id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            time_to_decision_seconds DOUBLE PRECISION
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint
            ON autonomy_approval_history (pattern_fingerprint)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint_approved_at
            ON autonomy_approval_history (pattern_fingerprint, approved_at)
    """)

    # --- autonomy_suggestions -------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS autonomy_suggestions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_type VARCHAR NOT NULL DEFAULT 'promotion',
            pattern_fingerprint VARCHAR(64) NOT NULL,
            tool_name TEXT NOT NULL,
            representative_args JSONB NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'pending',
            approval_count_at_creation INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_at TIMESTAMPTZ,
            decided_by TEXT,
            resulting_rule_id UUID REFERENCES approval_rules(id) ON DELETE SET NULL,
            cooldown_until TIMESTAMPTZ,
            dismissal_reason TEXT,
            CONSTRAINT autonomy_suggestions_type_check
                CHECK (suggestion_type IN ('promotion', 'demotion')),
            CONSTRAINT autonomy_suggestions_status_check
                CHECK (status IN ('pending', 'confirmed', 'dismissed', 'superseded'))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_fingerprint
            ON autonomy_suggestions (pattern_fingerprint)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_status_created
            ON autonomy_suggestions (status, created_at)
    """)

    # --- extend approval_events CHECK constraints with autonomy event types ---
    # PostgreSQL does not support ALTER CONSTRAINT directly; we drop and recreate.

    # 1. Extend approval_events_type_check to include the seven new autonomy types.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'approval_events'
                  AND constraint_name = 'approval_events_type_check'
            ) THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT approval_events_type_check;
            END IF;
        END;
        $$;
    """)
    op.execute("""
        ALTER TABLE approval_events
            ADD CONSTRAINT approval_events_type_check
                CHECK (event_type IN (
                    'action_queued',
                    'action_auto_approved',
                    'action_approved',
                    'action_rejected',
                    'action_expired',
                    'action_execution_succeeded',
                    'action_execution_failed',
                    'rule_created',
                    'rule_revoked',
                    'promotion_suggested',
                    'promotion_confirmed',
                    'promotion_dismissed',
                    'promotion_superseded',
                    'demotion_suggested',
                    'demotion_confirmed',
                    'demotion_dismissed'
                ))
    """)

    # 2. Relax approval_events_link_check to allow suggestion lifecycle events
    #    (promotion_*/demotion_*) to carry NULL action_id AND NULL rule_id.
    #    The original constraint required at least one FK to be non-NULL, which
    #    breaks when recording events such as promotion_suggested / demotion_dismissed
    #    that are not tied to a specific action or rule.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'approval_events'
                  AND constraint_name = 'approval_events_link_check'
            ) THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT approval_events_link_check;
            END IF;
        END;
        $$;
    """)
    op.execute("""
        ALTER TABLE approval_events
            ADD CONSTRAINT approval_events_link_check
                CHECK (
                    action_id IS NOT NULL
                    OR rule_id IS NOT NULL
                    OR event_type IN (
                        'promotion_suggested',
                        'promotion_confirmed',
                        'promotion_dismissed',
                        'promotion_superseded',
                        'demotion_suggested',
                        'demotion_confirmed',
                        'demotion_dismissed'
                    )
                )
    """)


def downgrade() -> None:
    # Drop new tables before restoring constraints (tables may hold FK references).
    op.execute("DROP TABLE IF EXISTS autonomy_suggestions")
    op.execute("DROP TABLE IF EXISTS autonomy_approval_history")

    # Restore the original approval_events_type_check (sans autonomy types).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'approval_events'
                  AND constraint_name = 'approval_events_type_check'
            ) THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT approval_events_type_check;
            END IF;
        END;
        $$;
    """)
    op.execute("""
        ALTER TABLE approval_events
            ADD CONSTRAINT approval_events_type_check
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
    """)

    # Restore the original approval_events_link_check (requiring action_id or rule_id).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'approval_events'
                  AND constraint_name = 'approval_events_link_check'
            ) THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT approval_events_link_check;
            END IF;
        END;
        $$;
    """)
    op.execute("""
        ALTER TABLE approval_events
            ADD CONSTRAINT approval_events_link_check
                CHECK (action_id IS NOT NULL OR rule_id IS NOT NULL)
    """)
