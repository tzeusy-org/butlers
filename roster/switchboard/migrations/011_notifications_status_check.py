"""Add CHECK constraint enumerating valid notifications.status values.

Revision ID: sw_011
Revises: sw_010
Create Date: 2026-05-16 00:00:00.000000

The notifications table was created with ``status TEXT NOT NULL DEFAULT 'sent'``
but with no CHECK constraint documenting or enforcing the permitted values.
This made the contract implicit — a rename migration could silently hollow out
the dashboard briefing query without any DB-level error.

Valid notification status values (contract):
  sent    — notification was delivered successfully (initial / "unread" state)
  failed  — delivery attempt failed (error column will be populated)
  read    — user has acknowledged/dismissed the notification

The constraint is added via ALTER TABLE so it validates against all existing
rows before committing.  All existing rows must already carry one of these
three values since that is the full set written by deliver.py and
notifications.py.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_011"
down_revision = "sw_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notifications
            ADD CONSTRAINT chk_notifications_status
            CHECK (status IN ('sent', 'failed', 'read'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS chk_notifications_status")
