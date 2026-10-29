"""Reconcile budget period admission without rewriting legacy data.

Revision ID: finance_016
Revises: finance_015
Create Date: 2026-10-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "finance_016"
down_revision = "finance_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE budgets DROP CONSTRAINT IF EXISTS budgets_period_check")
    op.execute("""
        ALTER TABLE budgets ADD CONSTRAINT budgets_period_check
            CHECK (period IN ('daily', 'weekly', 'monthly', 'quarterly', 'yearly'))
    """)


def downgrade() -> None:
    # Hold the DDL lock before checking, so a concurrent quarterly insert cannot
    # slip between the refusal check and restoring the legacy constraint.
    op.execute("LOCK TABLE budgets IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM budgets WHERE period = 'quarterly') THEN
                RAISE EXCEPTION
                    'Cannot downgrade finance_016 while quarterly budgets exist; '
                    'retain the repair or explicitly resolve quarterly history first'
                    USING ERRCODE = '23514';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE budgets DROP CONSTRAINT IF EXISTS budgets_period_check")
    op.execute("""
        ALTER TABLE budgets ADD CONSTRAINT budgets_period_check
            CHECK (period IN ('monthly', 'weekly', 'yearly', 'daily'))
    """)
