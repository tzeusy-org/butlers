"""Correct merchant_mappings schema to raw_pattern/normalized_merchant design

PR #894 (finance_002) created merchant_mappings with (merchant, category,
confidence, sample_count) — a simplified schema that does not match the
application code in pattern_recognition.py. The canonical schema tracked in
the application uses raw_pattern, normalized_merchant, learned_from_count,
source, and metadata columns.

This migration drops the incorrectly-structured table and recreates it with
the full production schema, including a functional index on lower(raw_pattern)
for case-insensitive deduplication and lookup.

Revision ID: finance_003
Revises: finance_002
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_003"
down_revision = "finance_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the incorrectly-structured merchant_mappings table created by
    # finance_002 (002_merchant_mappings_trigram_index.py). That migration
    # used (merchant, category, confidence, sample_count) but the application
    # code requires (raw_pattern, normalized_merchant, ..., source, metadata).
    op.execute("DROP TABLE IF EXISTS merchant_mappings")

    # --- finance.merchant_mappings (correct schema) ---
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

    # Unique index on lower(raw_pattern) for case-insensitive deduplication
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mapping_pattern
            ON merchant_mappings (lower(raw_pattern))
            WHERE is_active = true
    """)

    # Functional index on lower(raw_pattern) for faster case-insensitive lookups
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
    # Restore the finance_002 state: drop corrected table, re-create the
    # simplified merchant_mappings that finance_002 originally installed.
    op.execute("DROP TABLE IF EXISTS merchant_mappings")

    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_mappings (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant     TEXT NOT NULL,
            category     TEXT NOT NULL,
            confidence   NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
            sample_count INTEGER NOT NULL DEFAULT 1,
            is_active    BOOLEAN NOT NULL DEFAULT true,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mappings_merchant
            ON merchant_mappings (lower(merchant))
            WHERE is_active = true
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_merchant_trgm
            ON merchant_mappings USING GIN (merchant gin_trgm_ops)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_is_active
            ON merchant_mappings (is_active)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_merchant_mappings_category
            ON merchant_mappings (category)
    """)
