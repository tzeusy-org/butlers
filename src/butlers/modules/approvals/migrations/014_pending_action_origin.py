"""Add pending_actions.origin for prepared (never-pushed) actions.

Revision ID: approvals_014
Revises: approvals_013
Create Date: 2026-09-09 00:00:00.000000

bu-2jtfw.11: a "prepared action" is a proactive draft parked on the approval
spine by an insight-scan producer, bound to the insight candidate that
motivated it. Unlike every existing park path, a prepared action must never
call ``emit_approval_push`` — it is surfaced only through the insight digest's
door, not an owner push. ``origin`` distinguishes these rows so
``park_prepared_action`` (unlike ``park_pending_action``) can be identified
by API/query callers (``GET /api/approvals/actions?origin=prepared``) without
inferring intent from the absence of a push.

Nullable and additive: existing rows are all "not prepared" (NULL), and every
other park path is unaffected.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_014"
down_revision = "approvals_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable, enumerated origin column."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    ADD COLUMN IF NOT EXISTS origin TEXT;

                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_origin_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_origin_check
                    CHECK (origin IS NULL OR origin IN ('prepared'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop the origin column and its constraint."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_origin_check;
                ALTER TABLE pending_actions DROP COLUMN IF EXISTS origin;
            END IF;
        END $$;
        """
    )
