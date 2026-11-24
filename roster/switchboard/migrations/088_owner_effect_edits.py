"""Bind a single-effect owner receipt to an immutable domain amendment (bu-mmdsl7).

Revision ID: sw_088
Revises: sw_087

sw_073 remains append-only. Its original args_digest never changes; the optional pair
names the domain-owned amendment and the digest authorized for dispatch. No payload is
copied into the goal ledger. Existing approve/reject and decision-set receipts are unchanged.
"""

from alembic import op

revision = "sw_088"
down_revision = "sw_087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.owner_decision_items
            ADD COLUMN IF NOT EXISTS amendment_id UUID,
            ADD COLUMN IF NOT EXISTS approved_digest TEXT;
        ALTER TABLE switchboard.owner_decision_items
            ADD CONSTRAINT owner_decision_amendment CHECK (
                (amendment_id IS NULL AND approved_digest IS NULL) OR
                (amendment_id IS NOT NULL AND approved_digest IS NOT NULL
                 AND approved_digest ~ '^[0-9a-f]{64}$'
                 AND approved_digest <> args_digest AND outcome = 'approved'));
        CREATE UNIQUE INDEX uq_owner_decision_amendment
            ON switchboard.owner_decision_items(amendment_id) WHERE amendment_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.owner_decision_items
                       WHERE amendment_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot discard owner effect amendments';
            END IF;
        END $$;
        DROP INDEX IF EXISTS switchboard.uq_owner_decision_amendment;
        ALTER TABLE switchboard.owner_decision_items
            DROP CONSTRAINT IF EXISTS owner_decision_amendment,
            DROP COLUMN IF EXISTS amendment_id,
            DROP COLUMN IF EXISTS approved_digest;
    """)
