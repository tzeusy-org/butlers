"""align_contacts_schema

Add columns to the relationship-schema contacts table that were introduced
by core_002_identity on public.contacts.  When the butler runs with
search_path = "relationship,public", the unqualified ``contacts`` reference
resolves to ``relationship.contacts`` — which until now lacked first_name,
last_name, entity_id, and other fields used by the sync backfill and the
dashboard API.  This migration brings the two tables into alignment so that
both runtime queries and sync writes succeed against the schema-local table.

Revision ID: rel_002
Revises: rel_001
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002"
down_revision = "rel_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns that public.contacts has but relationship.contacts lacks.
    # Each uses IF NOT EXISTS / DO $$ guard so the migration is idempotent
    # (safe to re-run if columns were added manually).
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS first_name VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS last_name VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS nickname VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS company VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS job_title VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS gender VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS pronouns VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS avatar_url VARCHAR
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS listed BOOLEAN DEFAULT true
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS metadata JSONB
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS stay_in_touch_days INTEGER
    """)
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS preferred_channel VARCHAR
    """)

    # entity_id FK — create column + index, then add FK only if missing.
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS entity_id UUID
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_rel_contacts_entity_id'
            ) THEN
                CREATE INDEX ix_rel_contacts_entity_id
                ON contacts (entity_id)
                WHERE entity_id IS NOT NULL;
            END IF;
        END
        $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'rel_contacts_entity_id_fkey'
                  AND table_name = 'contacts'
            ) THEN
                ALTER TABLE contacts
                    ADD CONSTRAINT rel_contacts_entity_id_fkey
                    FOREIGN KEY (entity_id) REFERENCES public.entities(id)
                    ON DELETE SET NULL;
            END IF;
        END
        $$;
    """)

    # preferred_channel CHECK — add only if missing.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'rel_contacts_preferred_channel_check'
                  AND table_name = 'contacts'
            ) THEN
                ALTER TABLE contacts
                    ADD CONSTRAINT rel_contacts_preferred_channel_check
                    CHECK (preferred_channel IN ('telegram', 'email'));
            END IF;
        END
        $$;
    """)

    # Composite name index for search queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rel_contacts_first_last
        ON contacts (first_name, last_name)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_rel_contacts_first_last")
    op.execute(
        "ALTER TABLE contacts DROP CONSTRAINT IF EXISTS rel_contacts_preferred_channel_check"
    )
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS rel_contacts_entity_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_rel_contacts_entity_id")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS entity_id")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS preferred_channel")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS stay_in_touch_days")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS listed")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS avatar_url")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS pronouns")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS gender")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS job_title")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS company")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS nickname")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS last_name")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS first_name")
