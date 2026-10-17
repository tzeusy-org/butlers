"""Admit superseded Decision Desk history without reopening an old intent.

Revision ID: sw_046
Revises: sw_044

bu-c58oek. The positive live-index predicate already excludes this terminal
status. Applied audit history is preserved, so downgrade refuses rows that the
old status vocabulary cannot represent.
"""

from alembic import op

revision = "sw_046"
down_revision = "sw_044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decision_intents DROP CONSTRAINT ck_decision_intents_status")
    op.execute(
        "ALTER TABLE decision_intents ADD CONSTRAINT ck_decision_intents_status "
        "CHECK (status IN ('pending', 'applying', 'applied', 'failed', 'superseded'))"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE decision_intents IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM decision_intents WHERE status = 'superseded') THEN
                RAISE EXCEPTION 'Cannot downgrade sw_046 while superseded intent history exists';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE decision_intents DROP CONSTRAINT ck_decision_intents_status")
    op.execute(
        "ALTER TABLE decision_intents ADD CONSTRAINT ck_decision_intents_status "
        "CHECK (status IN ('pending', 'applying', 'applied', 'failed'))"
    )
