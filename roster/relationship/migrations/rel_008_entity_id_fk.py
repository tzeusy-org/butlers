"""rel_008_entity_id_fk

Revision ID: rel_008
Revises: rel_007
Create Date: 2026-02-23 00:00:00.000000

Adds nullable entity_id column to the contacts table, referencing the
entities table via a foreign key constraint.  The reference uses no schema
prefix so that PostgreSQL resolves it via search_path, which is set to the
butler's own schema (plus shared) by the Alembic env before migrations run.
"""

from __future__ import annotations

from alembic import op

revision = "rel_008"
down_revision = "rel_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS entity_id UUID")

    # FK resolves via search_path (butler schema, shared, public) — no hardcoded schema prefix.
    op.execute(
        """
        ALTER TABLE contacts
        ADD CONSTRAINT contacts_entity_id_fkey
        FOREIGN KEY (entity_id)
        REFERENCES entities(id)
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
