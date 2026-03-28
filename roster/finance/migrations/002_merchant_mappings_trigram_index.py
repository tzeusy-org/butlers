"""Add merchant_mappings table with GIN trigram index for ILIKE performance

Revision ID: finance_002
Revises: finance_001
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_002"
down_revision = "finance_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pg_trgm extension for trigram support (required for GIN trigram indexes)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- finance.merchant_mappings ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_mappings (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant             TEXT NOT NULL,
            category             TEXT NOT NULL,
            confidence           NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
            sample_count         INTEGER NOT NULL DEFAULT 1,
            is_active            BOOLEAN NOT NULL DEFAULT true,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Create unique index on merchant for deduplication (case-insensitive)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mappings_merchant
            ON merchant_mappings (lower(merchant))
            WHERE is_active = true
    """)

    # Create GIN trigram index on merchant column for ILIKE performance
    # This allows efficient substring matching in queries like:
    #   WHERE merchant ILIKE '%pattern%'
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_merchant_trgm
            ON merchant_mappings USING GIN (merchant gin_trgm_ops)
    """)

    # Create index on is_active for filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_is_active
            ON merchant_mappings (is_active)
    """)

    # Create index on category for filtering/lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_category
            ON merchant_mappings (category)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS merchant_mappings")
    # NOTE: pg_trgm extension is intentionally NOT dropped here.
    # It is a shared, system-level extension also used by the memory module
    # (predicate_registry GIN trigram index). Dropping it here would break
    # other features. Extensions created with IF NOT EXISTS are shared resources;
    # only the objects that specifically depend on this migration are removed.
