"""addresses_table

Create the addresses table that was referenced by the contact detail endpoint
and address tools but never had a migration.

Revision ID: rel_005
Revises: rel_004
Create Date: 2026-04-04 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_005"
down_revision = "rel_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS addresses (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            label VARCHAR NOT NULL DEFAULT 'Home',
            line_1 TEXT NOT NULL,
            line_2 TEXT,
            city VARCHAR,
            province VARCHAR,
            postal_code VARCHAR,
            country VARCHAR(2),
            is_current BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_addresses_contact_id
            ON addresses (contact_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS addresses")
