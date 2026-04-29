"""typed_relationships_and_life_events

Add the relationship_types taxonomy and life event category/type tables.

Revision ID: rel_008
Revises: rel_007
Create Date: 2026-04-30 00:00:00.000000

Tables created:
  - relationship_types: typed relationship taxonomy with bidirectional labels
  - life_event_categories: top-level groupings for life event types
  - life_event_types: specific event types within a category

Also adds:
  - relationship_type_id FK column to the existing relationships table
  - Seed data for both taxonomies matching the canonical sets used by the
    relationship tools and tests
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_008"
down_revision = "rel_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # relationship_types: typed taxonomy with forward/reverse labels
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS relationship_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            "group" VARCHAR NOT NULL,
            forward_label VARCHAR NOT NULL,
            reverse_label VARCHAR NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (forward_label, reverse_label)
        )
    """)

    # Seed canonical relationship types
    op.execute("""
        INSERT INTO relationship_types ("group", forward_label, reverse_label) VALUES
            ('Love',   'spouse',       'spouse'),
            ('Love',   'partner',      'partner'),
            ('Love',   'ex-partner',   'ex-partner'),
            ('Family', 'parent',       'child'),
            ('Family', 'sibling',      'sibling'),
            ('Family', 'grandparent',  'grandchild'),
            ('Family', 'uncle/aunt',   'nephew/niece'),
            ('Family', 'cousin',       'cousin'),
            ('Family', 'in-law',       'in-law'),
            ('Friend', 'friend',       'friend'),
            ('Friend', 'best friend',  'best friend'),
            ('Work',   'colleague',    'colleague'),
            ('Work',   'boss',         'subordinate'),
            ('Work',   'mentor',       'protege'),
            ('Custom', 'custom',       'custom')
        ON CONFLICT (forward_label, reverse_label) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # Add relationship_type_id FK column to relationships
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE relationships
            ADD COLUMN IF NOT EXISTS relationship_type_id UUID
                REFERENCES relationship_types(id) ON DELETE SET NULL
    """)

    # ------------------------------------------------------------------
    # life_event_categories: top-level groupings
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS life_event_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # life_event_types: specific types within a category
    # ------------------------------------------------------------------
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

    # Seed life event categories
    op.execute("""
        INSERT INTO life_event_categories (name) VALUES
            ('Career'),
            ('Personal'),
            ('Social')
        ON CONFLICT (name) DO NOTHING
    """)

    # Seed Career types
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name
        FROM life_event_categories
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

    # Seed Personal types
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name
        FROM life_event_categories
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

    # Seed Social types
    op.execute("""
        INSERT INTO life_event_types (category_id, name)
        SELECT id, type_name
        FROM life_event_categories
        CROSS JOIN (VALUES
            ('met for first time'),
            ('reconnected')
        ) AS t(type_name)
        WHERE name = 'Social'
        ON CONFLICT (category_id, name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE relationships
            DROP COLUMN IF EXISTS relationship_type_id
    """)
    op.execute("DROP TABLE IF EXISTS life_event_types")
    op.execute("DROP TABLE IF EXISTS life_event_categories")
    op.execute("DROP TABLE IF EXISTS relationship_types")
