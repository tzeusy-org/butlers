"""A goal records the Gmail thread that admitted it (RFC 0038 D5, Gmail exposure rule).

Revision ID: sw_083
Revises: sw_082

bu-kqme7f.10. The owner's Gmail exposure rule lets an exposed or third-party goal change only the
thread that triggered it without a per-event approval. Admission writes that thread here, once,
from the stored envelope the trusted source reader verified: the connected account (lowercased)
and the Gmail thread id. Both are set or both are null; only a message source can carry them.

No guard change is needed: sw_063's ``goal_ledger_immutable()`` freezes every goals column it
does not name, so these two are immutable from the insert. The downgrade refuses while any goal
records a thread, because dropping the columns would widen what those goals may do.
"""

from alembic import op

revision = "sw_083"
down_revision = "sw_082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.goals
            ADD COLUMN IF NOT EXISTS trigger_gmail_account TEXT,
            ADD COLUMN IF NOT EXISTS trigger_gmail_thread TEXT
    """)
    op.execute("""
        ALTER TABLE switchboard.goals ADD CONSTRAINT ck_goal_trigger_gmail CHECK (
            (trigger_gmail_account IS NULL AND trigger_gmail_thread IS NULL)
            OR (source_kind = 'message'
                AND trigger_gmail_account IS NOT NULL
                AND trigger_gmail_thread IS NOT NULL
                AND length(trigger_gmail_account) BETWEEN 1 AND 320
                AND trigger_gmail_account = lower(btrim(trigger_gmail_account))
                AND trigger_gmail_thread ~ '^[A-Za-z0-9_-]{1,64}$'))
    """)


def downgrade() -> None:
    op.execute("""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goals WHERE trigger_gmail_thread IS NOT NULL) THEN
                RAISE EXCEPTION 'goals record a triggering Gmail thread; refusing to drop it';
            END IF;
        END
        $guard$
    """)
    op.execute("ALTER TABLE switchboard.goals DROP CONSTRAINT IF EXISTS ck_goal_trigger_gmail")
    op.execute("""
        ALTER TABLE switchboard.goals
            DROP COLUMN IF EXISTS trigger_gmail_account,
            DROP COLUMN IF EXISTS trigger_gmail_thread
    """)
