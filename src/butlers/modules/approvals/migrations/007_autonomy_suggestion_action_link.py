"""Link autonomy suggestions back to their originating approval action.

Revision ID: approvals_007
Revises: approvals_006
Create Date: 2026-07-18 00:00:00.000000

The preceding approvals_006 migration is owned by the approval-request
notification change.  Keep this additive link after that revision so a
notification can land at an approval and a resulting autonomy suggestion can
lead the owner back to the same evidence.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_007"
down_revision = "approvals_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an optional, deletion-safe source-action link."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('autonomy_suggestions') IS NOT NULL THEN
                ALTER TABLE autonomy_suggestions
                    ADD COLUMN IF NOT EXISTS action_id UUID
                    REFERENCES pending_actions(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('autonomy_suggestions') IS NOT NULL THEN
                CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_action_id
                    ON autonomy_suggestions (action_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove only the additive source-action link."""
    op.execute("DROP INDEX IF EXISTS idx_autonomy_suggestions_action_id")
    op.execute("ALTER TABLE IF EXISTS autonomy_suggestions DROP COLUMN IF EXISTS action_id")
