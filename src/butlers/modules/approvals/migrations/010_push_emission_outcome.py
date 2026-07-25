"""Track the terminal outcome of each approval-push reservation.

Revision ID: approvals_010
Revises: approvals_009
Create Date: 2026-07-25 00:00:00.000000

``approval_push_emissions`` previously recorded only that a reservation had
been claimed for an action, with no way to tell a delivered push apart from
one that never went out.  ``emit_approval_push`` reserved the row *before*
resolving the owner recipient / callback secret, so a push that failed after
reservation (e.g. because ``APPROVAL_CALLBACK_SECRET`` was unavailable) left
a permanent reservation behind: any later retry hit ``ON CONFLICT DO
NOTHING`` and was silently treated as a duplicate, so the action was never
re-pushed even after the underlying problem was fixed (bu-mda0r).

Adding ``outcome`` lets the reservation itself be retried when the prior
attempt did not actually reach delivery.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_010"
down_revision = "approvals_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE approval_push_emissions
            ADD COLUMN IF NOT EXISTS outcome TEXT
            CHECK (outcome IN ('delivered', 'deferred', 'collapsed', 'duplicate', 'failed'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE approval_push_emissions DROP COLUMN IF EXISTS outcome")
