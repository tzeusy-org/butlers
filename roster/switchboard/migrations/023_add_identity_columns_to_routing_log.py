"""Add identity columns to routing_log for sender identity tracking.

Revision ID: sw_023
Revises: sw_022
Create Date: 2026-02-25 00:00:00.000000

Adds three columns to routing_log for identity-resolved sender context:
  - contact_id UUID      — resolved contact from shared.contacts (nullable)
  - entity_id  UUID      — linked memory entity_id (nullable)
  - sender_roles TEXT[]  — snapshot of the contact's roles at routing time (nullable)

No FK constraints are added because:
  1. routing_log is append-only telemetry; CASCADE deletes would be destructive.
  2. shared.contacts may not yet exist when the switchboard schema runs.
  3. entity_id is cross-schema (memory butler) — FK across schemas requires
     explicit schema qualification and role grants beyond the scope of this table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_023"
down_revision = "sw_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE routing_log
            ADD COLUMN IF NOT EXISTS contact_id UUID,
            ADD COLUMN IF NOT EXISTS entity_id UUID,
            ADD COLUMN IF NOT EXISTS sender_roles TEXT[]
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_routing_log_contact_id
            ON routing_log (contact_id)
            WHERE contact_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_routing_log_contact_id")
    op.execute("""
        ALTER TABLE routing_log
            DROP COLUMN IF EXISTS sender_roles,
            DROP COLUMN IF EXISTS entity_id,
            DROP COLUMN IF EXISTS contact_id
    """)
