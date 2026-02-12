"""rel_005_reminders_rework

Revision ID: rel_005
Revises: rel_004
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_005"
down_revision = "rel_004"
branch_labels = ("relationship",)
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS label TEXT")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS type VARCHAR")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS next_trigger_at TIMESTAMPTZ")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS last_triggered_at TIMESTAMPTZ")

    op.execute("""
        UPDATE reminders
        SET label = COALESCE(label, message),
            type = COALESCE(
                type,
                CASE
                    WHEN reminder_type = 'one_time' THEN 'one_time'
                    ELSE 'recurring_monthly'
                END
            ),
            next_trigger_at = COALESCE(
                next_trigger_at,
                CASE WHEN dismissed = false THEN due_at ELSE NULL END
            )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS last_triggered_at")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS next_trigger_at")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS type")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS label")
