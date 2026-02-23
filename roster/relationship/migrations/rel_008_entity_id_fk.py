"""rel_008_entity_id_fk

Revision ID: rel_008
Revises: rel_007
Create Date: 2026-02-23 00:00:00.000000

Adds nullable entity_id column to the contacts table, referencing the
memory module's entities table via a cross-schema foreign key constraint.
"""

from __future__ import annotations

from alembic import op

revision = "rel_008"
down_revision = "rel_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS entity_id UUID")

    # Cross-schema FK: relationship.contacts -> memory.entities
    # The constraint is advisory; memory schema must exist for this to resolve.
    op.execute(
        """
        ALTER TABLE contacts
        ADD CONSTRAINT contacts_entity_id_fkey
        FOREIGN KEY (entity_id)
        REFERENCES memory.entities(id)
        ON DELETE SET NULL
        NOT VALID
        """
    )

    op.execute("ALTER TABLE contacts VALIDATE CONSTRAINT contacts_entity_id_fkey")

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_contacts_entity_id
        ON contacts (entity_id)
        WHERE entity_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contacts_entity_id")
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_entity_id_fkey")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS entity_id")
