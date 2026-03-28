"""seasonal_periods: create per-butler seasonal period configuration table.

Revision ID: core_041
Revises: core_040
Create Date: 2026-03-28 00:00:00.000000

Creates the ``seasonal_periods`` table in each butler's own schema (accessed
via the active search_path, exactly like ``sessions`` and ``state``).

Seasonal periods define recurring calendar windows (e.g., tax season, academic
terms) that butlers query during task dispatch to inject contextual awareness
into prompts.

Schema:

  seasonal_periods
  ----------------
  id           UUID PK
  name         TEXT UNIQUE per butler (enforced by UNIQUE constraint)
  period_type  TEXT NOT NULL  CHECK (annual | academic | fiscal | custom)
  start_month  INTEGER NOT NULL  CHECK (1-12)
  start_day    INTEGER NOT NULL  CHECK (1-31)
  end_month    INTEGER NOT NULL  CHECK (1-12)
  end_day      INTEGER NOT NULL  CHECK (1-31)
  timezone     TEXT NOT NULL DEFAULT 'UTC'
  metadata     JSONB (optional -- context hints, priority modifiers, etc.)
  butler_name  TEXT NOT NULL
  enabled      BOOLEAN NOT NULL DEFAULT true
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes:
  idx_seasonal_periods_butler_name  — filter by butler (primary access pattern)
  idx_seasonal_periods_enabled      — partial index on enabled for active queries
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_041"
down_revision = "core_040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create the seasonal_periods table (per-butler, via search_path).
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'annual' CHECK (period_type IN (
                'annual', 'academic', 'fiscal', 'custom'
            )),
            start_month INTEGER NOT NULL CHECK (start_month BETWEEN 1 AND 12),
            start_day   INTEGER NOT NULL CHECK (start_day   BETWEEN 1 AND 31),
            end_month   INTEGER NOT NULL CHECK (end_month   BETWEEN 1 AND 12),
            end_day     INTEGER NOT NULL CHECK (end_day     BETWEEN 1 AND 31),
            timezone    TEXT NOT NULL DEFAULT 'UTC',
            metadata    JSONB,
            butler_name TEXT NOT NULL,
            enabled     BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT seasonal_periods_name_butler_unique UNIQUE (name, butler_name)
        )
    """)

    # -------------------------------------------------------------------------
    # 2. Indexes.
    # -------------------------------------------------------------------------

    # Primary lookup: all seasonal periods for a given butler.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_seasonal_periods_butler_name
            ON seasonal_periods (butler_name)
    """)

    # Active-period query: skip disabled rows early.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_seasonal_periods_enabled
            ON seasonal_periods (butler_name, enabled)
        WHERE enabled = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_seasonal_periods_enabled")
    op.execute("DROP INDEX IF EXISTS idx_seasonal_periods_butler_name")
    op.execute("DROP TABLE IF EXISTS seasonal_periods")
