"""relationship_types taxonomy

Revision ID: rel_002d
Revises: rel_002c
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002d"
down_revision = "rel_002c"
branch_labels = None
depends_on = None

# Seed data: (group, forward_label, reverse_label)
_SEED_TYPES = [
    # Love
    ("Love", "spouse", "spouse"),
    ("Love", "partner", "partner"),
    ("Love", "ex-partner", "ex-partner"),
    # Family
    ("Family", "parent", "child"),
    ("Family", "sibling", "sibling"),
    ("Family", "grandparent", "grandchild"),
    ("Family", "uncle/aunt", "nephew/niece"),
    ("Family", "cousin", "cousin"),
    ("Family", "in-law", "in-law"),
    # Friend
    ("Friend", "friend", "friend"),
    ("Friend", "best friend", "best friend"),
    # Work
    ("Work", "colleague", "colleague"),
    ("Work", "boss", "subordinate"),
    ("Work", "mentor", "protege"),
    # Custom (catch-all for freetext migration)
    ("Custom", "custom", "custom"),
]


def upgrade() -> None:
    # 1. Create relationship_types table
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

    # 2. Seed relationship types
    for group, forward, reverse in _SEED_TYPES:
        op.execute(f"""
            INSERT INTO relationship_types ("group", forward_label, reverse_label)
            VALUES ('{group}', '{forward}', '{reverse}')
            ON CONFLICT (forward_label, reverse_label) DO NOTHING
        """)

    # 3. Add relationship_type_id column to relationships (nullable first for migration)
    op.execute("""
        ALTER TABLE relationships
        ADD COLUMN IF NOT EXISTS relationship_type_id UUID
            REFERENCES relationship_types(id) ON DELETE SET NULL
    """)

    # 4. Migrate existing freetext type values to closest match
    # Try exact match on forward_label first, then fall back to 'custom'
    op.execute("""
        UPDATE relationships r
        SET relationship_type_id = COALESCE(
            (SELECT rt.id FROM relationship_types rt
             WHERE rt.forward_label = LOWER(r.type) LIMIT 1),
            (SELECT rt.id FROM relationship_types rt
             WHERE rt.reverse_label = LOWER(r.type) LIMIT 1),
            (SELECT rt.id FROM relationship_types rt
             WHERE rt.forward_label = 'custom' LIMIT 1)
        )
        WHERE r.relationship_type_id IS NULL
    """)

    # 5. Create index on relationship_type_id for efficient lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_relationships_type_id
            ON relationships (relationship_type_id)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS relationship_type_id")
    op.execute("DROP INDEX IF EXISTS idx_relationships_type_id")
    op.execute("DROP TABLE IF EXISTS relationship_types")
