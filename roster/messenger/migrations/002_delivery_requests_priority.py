"""Add priority column to delivery_requests.

Revision ID: msg_002
Revises: msg_001
Create Date: 2026-05-12 00:00:00.000000

Adds a TEXT ``priority`` column to ``delivery_requests`` with values
'high' | 'medium' | 'low', defaulting to 'medium'.  Backfills all
existing rows with the default.  Includes a partial index on
(priority, status) for the pending/in_progress subset that the
queue-depth endpoint groups by.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "msg_002"
down_revision = "msg_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column — nullable first so the backfill can run before the CHECK.
    op.execute("""
        ALTER TABLE delivery_requests
        ADD COLUMN IF NOT EXISTS priority TEXT
    """)

    # Backfill existing rows with the default.
    op.execute("""
        UPDATE delivery_requests
        SET priority = 'medium'
        WHERE priority IS NULL
    """)

    # Apply NOT NULL constraint now that all rows have a value.
    op.execute("""
        ALTER TABLE delivery_requests
        ALTER COLUMN priority SET NOT NULL
    """)

    # Apply DEFAULT so future inserts without an explicit priority get 'medium'.
    op.execute("""
        ALTER TABLE delivery_requests
        ALTER COLUMN priority SET DEFAULT 'medium'
    """)

    # Add CHECK constraint.
    op.execute("""
        ALTER TABLE delivery_requests
        ADD CONSTRAINT delivery_requests_priority_check
            CHECK (priority IN ('high', 'medium', 'low'))
    """)

    # Partial index for queue-depth GROUP BY priority on in-flight rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delivery_requests_priority_status
            ON delivery_requests (priority, status)
            WHERE status IN ('pending', 'in_progress')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_delivery_requests_priority_status")
    op.execute("""
        ALTER TABLE delivery_requests
        DROP CONSTRAINT IF EXISTS delivery_requests_priority_check
    """)
    op.execute("""
        ALTER TABLE delivery_requests
        DROP COLUMN IF EXISTS priority
    """)
