"""add relationship tables: contact_info, addresses, life_events

Revision ID: rel_002c
Revises: rel_002a
Create Date: 2026-02-09 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002c"
down_revision = "rel_002a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create contact_info table
    op.execute("""
        CREATE TABLE IF NOT EXISTS contact_info (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type VARCHAR NOT NULL,
            value TEXT NOT NULL,
            label VARCHAR,
            is_primary BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_info_type_value
            ON contact_info (type, value)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_info_contact_id
            ON contact_info (contact_id)
    """)

    # Create addresses table
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

    # Create life event tables
    # Life event categories (Career, Personal, Social)
    op.execute("""
        CREATE TABLE IF NOT EXISTS life_event_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # Life event types (nested under categories)
    op.execute("""
        CREATE TABLE IF NOT EXISTS life_event_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            category_id UUID NOT NULL REFERENCES life_event_categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (category_id, name)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_event_types_category
            ON life_event_types (category_id)
    """)

    # Life events (significant milestones for contacts)
    op.execute("""
        CREATE TABLE IF NOT EXISTS life_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            life_event_type_id UUID NOT NULL REFERENCES life_event_types(id),
            summary TEXT NOT NULL,
            description TEXT,
            happened_at DATE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_events_contact_happened
            ON life_events (contact_id, happened_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_events_type
            ON life_events (life_event_type_id)
    """)

    # Seed categories
    op.execute("""
        INSERT INTO life_event_categories (name) VALUES
            ('Career'),
            ('Personal'),
            ('Social')
        ON CONFLICT (name) DO NOTHING
    """)

    # Seed types for Career category
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name FROM life_event_categories
        CROSS JOIN (VALUES
            ('new job'),
            ('promotion'),
            ('quit'),
            ('retired'),
            ('graduated')
        ) AS t(type_name)
        WHERE name = 'Career'
        ON CONFLICT (category_id, name) DO NOTHING
    """)

    # Seed types for Personal category
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name FROM life_event_categories
        CROSS JOIN (VALUES
            ('married'),
            ('divorced'),
            ('had a child'),
            ('moved'),
            ('passed away')
        ) AS t(type_name)
        WHERE name = 'Personal'
        ON CONFLICT (category_id, name) DO NOTHING
    """)

    # Seed types for Social category
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name FROM life_event_categories
        CROSS JOIN (VALUES
            ('met for first time'),
            ('reconnected')
        ) AS t(type_name)
        WHERE name = 'Social'
        ON CONFLICT (category_id, name) DO NOTHING
    """)


def downgrade() -> None:
    # Drop tables in reverse order of creation
    op.execute("DROP TABLE IF EXISTS life_events")
    op.execute("DROP TABLE IF EXISTS life_event_types")
    op.execute("DROP TABLE IF EXISTS life_event_categories")
    op.execute("DROP TABLE IF EXISTS addresses")
    op.execute("DROP TABLE IF EXISTS contact_info")
