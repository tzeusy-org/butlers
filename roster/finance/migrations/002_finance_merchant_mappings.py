"""finance_merchant_mappings

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
    # --- finance.merchant_mappings ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_mappings (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            raw_pattern         TEXT NOT NULL,
            normalized_merchant TEXT NOT NULL,
            category            TEXT NOT NULL,
            confidence          FLOAT NOT NULL DEFAULT 1.0,
            learned_from_count  INTEGER NOT NULL DEFAULT 0,
            source              TEXT NOT NULL DEFAULT 'learned'
                                    CHECK (source IN ('learned', 'manual', 'import')),
            is_active           BOOLEAN NOT NULL DEFAULT true,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Unique index on lower(raw_pattern) for case-insensitive lookups
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mapping_pattern
            ON merchant_mappings (lower(raw_pattern))
            WHERE is_active = true
    """)

    # Functional index on lower(raw_pattern) for faster pattern matching
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mapping_pattern_lower
            ON merchant_mappings (lower(raw_pattern))
    """)

    # Index on normalized_merchant for lookups by cleaned name
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mapping_normalized
            ON merchant_mappings (normalized_merchant)
    """)

    # Index on category for filtering by category
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mapping_category
            ON merchant_mappings (category)
    """)

    # Index on is_active for active mapping queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mapping_active
            ON merchant_mappings (is_active)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS merchant_mappings")
