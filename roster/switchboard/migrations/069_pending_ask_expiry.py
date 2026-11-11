"""Pending asks carry a 24 hour expiry and, for refused admissions, a source reference.

Revision ID: sw_069
Revises: sw_068

RFC 0038 S6 (bu-dhloi1.14, package 4a). ``expires_at`` bounds every ask: after 24 hours an
open ask becomes ``dropped_stale`` and is never replayed. ``original_ask`` is the only place a
refused admission is remembered, because a refused admission has no goal row. It holds a
versioned source reference and the closed refusal reason, not message text, and it exists only
while the ask is open: the CHECK forbids it on any other state, so every terminal transition
(Run, Drop, ack, the stale expiry) must clear it in the same update.

Existing rows get ``created_at + 24h``. The downgrade refuses while any ``original_ask`` exists,
because dropping the column would erase the only record of a refused owner request.
"""

from alembic import op

revision = "sw_069"
down_revision = "sw_068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.pending_asks ADD COLUMN IF NOT EXISTS original_ask JSONB;
        ALTER TABLE switchboard.pending_asks ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
        UPDATE switchboard.pending_asks
           SET expires_at = created_at + interval '24 hours' WHERE expires_at IS NULL;
        ALTER TABLE switchboard.pending_asks ALTER COLUMN expires_at SET NOT NULL;
        ALTER TABLE switchboard.pending_asks
            ALTER COLUMN expires_at SET DEFAULT clock_timestamp() + interval '24 hours';
        ALTER TABLE switchboard.pending_asks DROP CONSTRAINT IF EXISTS ck_pending_ask_original;
        ALTER TABLE switchboard.pending_asks ADD CONSTRAINT ck_pending_ask_original CHECK (
            original_ask IS NULL
            OR (state = 'open' AND jsonb_typeof(original_ask) = 'object'));
        CREATE INDEX IF NOT EXISTS ix_pending_asks_expiry
            ON switchboard.pending_asks (expires_at) WHERE state = 'open';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.pending_asks WHERE original_ask IS NOT NULL) THEN
                RAISE EXCEPTION
                    'sw_069 downgrade refused: original asks exist and are the only record of a refused owner request';
            END IF;
        END $$;
        DROP INDEX IF EXISTS switchboard.ix_pending_asks_expiry;
        ALTER TABLE switchboard.pending_asks DROP CONSTRAINT IF EXISTS ck_pending_ask_original;
        ALTER TABLE switchboard.pending_asks DROP COLUMN IF EXISTS original_ask;
        ALTER TABLE switchboard.pending_asks DROP COLUMN IF EXISTS expires_at;
    """)
