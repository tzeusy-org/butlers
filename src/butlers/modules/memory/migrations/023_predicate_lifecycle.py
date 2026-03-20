"""predicate_lifecycle — mem_023

Add lifecycle management columns to predicate_registry:

  status          TEXT DEFAULT 'active'
                  Valid values: 'active', 'deprecated', 'proposed'
                  - active:     ready for general use
                  - deprecated: superseded; writes succeed with a warning
                  - proposed:   auto-registered from a novel write; not yet curated

  superseded_by   TEXT (FK to predicate_registry.name)
                  When status='deprecated', names the canonical replacement predicate.
                  NULL for active/proposed predicates.

  deprecated_at   TIMESTAMPTZ
                  Timestamp when the predicate was deprecated.
                  NULL for active/proposed predicates.

Existing rows get status='active' via the column DEFAULT.

A CHECK constraint restricts status to the three valid values.

Revision ID: mem_023
Revises: mem_022
Create Date: 2026-03-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_023"
down_revision = "mem_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add all three lifecycle columns atomically in a single ALTER TABLE.
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
        ADD COLUMN IF NOT EXISTS superseded_by TEXT,
        ADD COLUMN IF NOT EXISTS deprecated_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE predicate_registry
        ADD CONSTRAINT IF NOT EXISTS predicate_registry_status_check
        CHECK (status IN ('active', 'deprecated', 'proposed'))
    """)
    # FK-like constraint: superseded_by must name an existing predicate.
    # Soft FK via CHECK is not possible cross-row; leave as free text for
    # flexibility (the replacement may be seeded in the same migration).
    # An index speeds up lookups when filtering by status.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_status
        ON predicate_registry (status)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_status")
    op.execute("""
        ALTER TABLE predicate_registry
        DROP CONSTRAINT IF EXISTS predicate_registry_status_check
    """)
    # Drop all three lifecycle columns atomically in a single ALTER TABLE.
    op.execute("""
        ALTER TABLE predicate_registry
        DROP COLUMN IF EXISTS deprecated_at,
        DROP COLUMN IF EXISTS superseded_by,
        DROP COLUMN IF EXISTS status
    """)
