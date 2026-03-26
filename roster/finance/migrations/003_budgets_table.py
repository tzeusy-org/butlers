"""budgets_table — category budget limits with deactivation-based versioning.

Revision ID: finance_003
Revises: finance_002
Create Date: 2026-03-26 00:00:00.000000

Adds:
  - finance.budgets : Per-category spending budget records.  Each active budget
                      specifies a (category, period) spending limit with optional
                      warn/alert threshold fractions.  Previous budgets for the
                      same (category, period) are soft-deactivated (is_active=false)
                      rather than deleted, preserving history.

  - A partial unique index prevents more than one active row per (category, period)
    at the database level, guarding against concurrent upsert races.
"""

from __future__ import annotations

from alembic import op

revision = "finance_003"
down_revision = "finance_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            category         TEXT NOT NULL,
            period           TEXT NOT NULL
                                 CHECK (period IN ('weekly', 'monthly', 'quarterly', 'yearly')),
            amount           NUMERIC(14, 2) NOT NULL,
            currency         CHAR(3) NOT NULL DEFAULT 'USD',
            warn_threshold   NUMERIC(5, 4) NOT NULL DEFAULT 0.8000,
            alert_threshold  NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
            is_active        BOOLEAN NOT NULL DEFAULT true,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Partial unique index: enforce at most one active budget per (category, period).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_budgets_active_category_period
            ON budgets (category, period)
            WHERE is_active = true
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_budgets_category
            ON budgets (category)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_budgets_period
            ON budgets (period)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_budgets_is_active
            ON budgets (is_active)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS budgets")
