"""Admit the terminal recorded-feed disposition without changing ledger authority.

Revision ID: sw_051
Revises: sw_050

Only the existing reason vocabulary changes. Runtime RLS, immutable decisions,
source/attempt bindings, rows and every other constraint remain untouched.
"""

from alembic import op

revision = "sw_051"
down_revision = "sw_050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The lock and replacement share Alembic's transaction. Repeating this
    # installer is safe; no writer observes a constraint-free table.
    op.execute("""
        LOCK TABLE switchboard.admission_decisions IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE switchboard.admission_decisions
            DROP CONSTRAINT IF EXISTS admission_decisions_reason_check;
        ALTER TABLE switchboard.admission_decisions
            ADD CONSTRAINT admission_decisions_reason_check CHECK (reason IN (
                'source_evidence_unavailable','limits_unavailable','cause_invalid',
                'creator_unavailable','causal_chain_limit','reply_joined','quota_exhausted','new_goal',
                'recorded_without_goal'));
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE switchboard.admission_decisions IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.admission_decisions
                       WHERE reason='recorded_without_goal') THEN
                RAISE EXCEPTION 'Cannot discard durable record-only admission decisions';
            END IF;
        END $$;
        ALTER TABLE switchboard.admission_decisions
            DROP CONSTRAINT IF EXISTS admission_decisions_reason_check;
        ALTER TABLE switchboard.admission_decisions
            ADD CONSTRAINT admission_decisions_reason_check CHECK (reason IN (
                'source_evidence_unavailable','limits_unavailable','cause_invalid',
                'creator_unavailable','causal_chain_limit','reply_joined','quota_exhausted','new_goal'));
    """)
