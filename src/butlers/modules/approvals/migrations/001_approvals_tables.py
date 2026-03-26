"""approvals_tables — collapsed from approvals_001 through approvals_004

Revision ID: approvals_001
Revises:
Create Date: 2026-03-26 00:00:00.000000

Creates the full approvals subsystem in its final state:

  - approval_rules          — standing rules that auto-approve matching actions
  - pending_actions         — queued tool invocations awaiting human decision
  - approval_events         — append-only audit log with immutability trigger
  - autonomy_approval_history — per-fingerprint approval frequency tracking
  - autonomy_suggestions    — promotion/demotion lifecycle for the autonomy ladder

Note: approval_rules.created_from references pending_actions(id) and
pending_actions.approval_rule_id references approval_rules(id).  This circular
FK is handled by creating approval_rules.created_from as DEFERRABLE INITIALLY
DEFERRED.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_001"
down_revision = None
branch_labels = ("approvals",)
depends_on = None


def upgrade() -> None:
    # --- approval_rules ------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tool_name TEXT NOT NULL,
            arg_constraints JSONB NOT NULL,
            description TEXT NOT NULL,
            created_from UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ,
            max_uses INT,
            use_count INT NOT NULL DEFAULT 0,
            active BOOL NOT NULL DEFAULT true
        )
    """)

    # --- pending_actions -----------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_actions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tool_name TEXT NOT NULL,
            tool_args JSONB NOT NULL,
            agent_summary TEXT,
            session_id UUID,
            status VARCHAR NOT NULL DEFAULT 'pending',
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ,
            decided_by TEXT,
            decided_at TIMESTAMPTZ,
            execution_result JSONB,
            approval_rule_id UUID REFERENCES approval_rules(id),
            CONSTRAINT pending_actions_status_check
                CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed'))
        )
    """)

    # Circular FK: approval_rules.created_from -> pending_actions(id)
    # Created DEFERRABLE so both rows can be inserted in a single transaction.
    op.execute("""
        ALTER TABLE approval_rules
        ADD CONSTRAINT approval_rules_created_from_fk
            FOREIGN KEY (created_from) REFERENCES pending_actions(id)
            DEFERRABLE INITIALLY DEFERRED
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_actions_status_requested
            ON pending_actions (status, requested_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_actions_session_id
            ON pending_actions (session_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approval_rules_tool_active
            ON approval_rules (tool_name, active)
    """)

    # --- approval_events (append-only audit log) -----------------------------
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
                    'rule_revoked',
                    'promotion_suggested',
                    'promotion_confirmed',
                    'promotion_dismissed',
                    'promotion_superseded',
                    'demotion_suggested',
                    'demotion_confirmed',
                    'demotion_dismissed'
                )),
            CONSTRAINT approval_events_link_check
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

    # Immutability trigger: prevent UPDATE/DELETE on approval_events.
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

    # --- autonomy_suggestions ------------------------------------------------
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS autonomy_suggestions")
    op.execute("DROP TABLE IF EXISTS autonomy_approval_history")
    op.execute("DROP TRIGGER IF EXISTS trg_approval_events_immutable ON approval_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_approval_events_mutation")
    op.execute("DROP TABLE IF EXISTS approval_events")
    op.execute("""
        ALTER TABLE approval_rules
        DROP CONSTRAINT IF EXISTS approval_rules_created_from_fk
    """)
    op.execute("DROP TABLE IF EXISTS pending_actions")
    op.execute("DROP TABLE IF EXISTS approval_rules")
