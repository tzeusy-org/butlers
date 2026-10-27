"""Writer-owned parked-notification review correlation.

Revision ID: sw_044
Revises: sw_043
"""

from alembic import op

revision = "sw_044"
down_revision = "sw_043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN approval_review JSONB")
    op.execute("""ALTER TABLE notifications ADD CONSTRAINT notifications_approval_review_shape
        CHECK (approval_review IS NULL OR (
            jsonb_typeof(approval_review) = 'object'
            AND approval_review ?& ARRAY['action_id', 'butler']
            AND approval_review - 'action_id' - 'butler' = '{}'::jsonb
            AND jsonb_typeof(approval_review->'action_id') = 'string'
            AND approval_review->>'action_id' ~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            AND approval_review->>'butler' = 'messenger'
        ) IS TRUE)""")


def downgrade() -> None:
    op.execute("LOCK TABLE notifications IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM notifications WHERE approval_review IS NOT NULL) THEN
            RAISE EXCEPTION 'Refusing to discard recorded approval review correlation';
        END IF;
    END $$""")
    op.execute("ALTER TABLE notifications DROP COLUMN approval_review")
