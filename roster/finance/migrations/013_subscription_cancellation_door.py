"""subscription_cancellation_door

Revision ID: finance_013
Revises: finance_012
Create Date: 2026-09-05 00:00:00.000000

bu-8cdl1.10 slice 1 of 3. The finance manifesto promises "no un-warned
renewal", but nothing in ``subscriptions`` records the door needed to act on a
warning: where to cancel, how much notice the provider requires, and the
actual date by which the owner must act. This slice adds those three columns
only -- the obligation ledger and warn-by derivation (slice 2) and the
insight payload/dashboard surfacing (slice 3) land separately.

All three columns are nullable with no default: existing subscriptions have
none of this metadata yet, and a missing door is a valid, explicit state
(surfaced as an enrichment prompt by a later slice) rather than an error.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_013"
down_revision = "finance_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE subscriptions
            ADD COLUMN IF NOT EXISTS cancellation_url TEXT
    """)
    op.execute("""
        ALTER TABLE subscriptions
            ADD COLUMN IF NOT EXISTS notice_period_days INTEGER
    """)
    op.execute("""
        ALTER TABLE subscriptions
            ADD COLUMN IF NOT EXISTS cancel_by DATE
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'subscriptions_notice_period_days_check'
                  AND conrelid = 'subscriptions'::regclass
            ) THEN
                ALTER TABLE subscriptions
                    ADD CONSTRAINT subscriptions_notice_period_days_check
                        CHECK (notice_period_days IS NULL OR notice_period_days >= 0);
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE subscriptions
            DROP CONSTRAINT IF EXISTS subscriptions_notice_period_days_check
    """)
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS cancel_by")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS notice_period_days")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS cancellation_url")
