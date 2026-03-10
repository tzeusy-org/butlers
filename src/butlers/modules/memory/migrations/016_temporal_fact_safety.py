"""temporal_fact_safety — mem_016

Add temporal idempotency and bitemporal columns to the facts table.

New columns:
- idempotency_key (TEXT nullable): dedup key for temporal facts
- observed_at (TIMESTAMPTZ DEFAULT now()): when the system first learned this fact
- invalid_at (TIMESTAMPTZ nullable): when the fact was known to be no longer true

New index:
- idx_facts_temporal_idempotency: partial unique index on
  (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL

Revision ID: mem_016
Revises: mem_015
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_016"
down_revision = "mem_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # Add temporal safety columns to facts
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE facts
            ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
            ADD COLUMN IF NOT EXISTS observed_at      TIMESTAMPTZ DEFAULT now(),
            ADD COLUMN IF NOT EXISTS invalid_at       TIMESTAMPTZ
    """)

    # Partial unique index — enforces idempotency for temporal fact writes.
    # Only covers rows where idempotency_key IS NOT NULL (property facts are
    # excluded; they use the existing supersession mechanism).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_temporal_idempotency
        ON facts (tenant_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_facts_temporal_idempotency")
    op.execute("""
        ALTER TABLE facts
            DROP COLUMN IF EXISTS invalid_at,
            DROP COLUMN IF EXISTS observed_at,
            DROP COLUMN IF EXISTS idempotency_key
    """)
