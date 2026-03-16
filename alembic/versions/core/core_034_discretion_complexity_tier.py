"""discretion_complexity_tier: widen CHECK constraints to include 'discretion' tier

Revision ID: core_034
Revises: core_033
Create Date: 2026-03-16 00:00:00.000000

Adds 'discretion' as a valid complexity_tier value in the two tables that
carry CHECK constraints on that column:

  - shared.model_catalog.complexity_tier
  - shared.butler_model_overrides.complexity_tier

PostgreSQL does not support ALTER … ADD/DROP CHECK directly; the migration
drops and recreates each constraint in-place.  The change is backward-
compatible: existing rows are untouched, the CHECK only applies to future
writes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_034"
down_revision = "core_033"
branch_labels = None
depends_on = None

_OLD_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high')"
_NEW_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion')"

_OLD_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high')"
)
_NEW_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN "
    "('trivial', 'medium', 'high', 'extra_high', 'discretion')"
)


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Widen shared.model_catalog CHECK constraint
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.model_catalog
            DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.model_catalog
            ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_NEW_CATALOG_CHECK})
    """)

    # -------------------------------------------------------------------------
    # 2. Widen shared.butler_model_overrides CHECK constraint
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.butler_model_overrides
            DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.butler_model_overrides
            ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK ({_NEW_OVERRIDES_CHECK})
    """)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Revert to the original four-tier constraints.
    # Note: this will FAIL if any rows already contain 'discretion' — the caller
    # must remove those rows first.
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.butler_model_overrides
            DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.butler_model_overrides
            ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK ({_OLD_OVERRIDES_CHECK})
    """)

    op.execute("""
        ALTER TABLE shared.model_catalog
            DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.model_catalog
            ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_OLD_CATALOG_CHECK})
    """)
