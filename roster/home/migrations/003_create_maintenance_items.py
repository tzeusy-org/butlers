"""create_maintenance_items

Revision ID: home_maintenance_001
Revises: home_assistant_002
Create Date: 2026-03-25 00:00:00.000000

Creates Home Butler maintenance scheduling table:

  - maintenance_items — recurring maintenance items with interval-based
                        due-date computation (filter replacements, HVAC
                        service, appliance warranties, etc.)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_maintenance_001"
down_revision = "home_assistant_002"
branch_labels = ("home_maintenance",)
depends_on = None


def upgrade() -> None:
    # maintenance_items: one row per named maintenance item.
    # next_due_at is computed by the application as
    #   last_completed_at + interval_days * interval '1 day'
    # and is NULL when the item has never been completed.
    op.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_items (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            name               TEXT        NOT NULL UNIQUE,
            category           TEXT        NOT NULL
                               CHECK (category IN (
                                   'filter', 'hvac', 'appliance',
                                   'plumbing', 'electrical', 'general'
                               )),
            interval_days      INTEGER     NOT NULL,
            last_completed_at  TIMESTAMPTZ,
            next_due_at        TIMESTAMPTZ,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Index on next_due_at to support the schedule check job's
    # "items due or overdue" query efficiently.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_maintenance_items_next_due_at
            ON maintenance_items (next_due_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_maintenance_items_next_due_at")
    op.execute("DROP TABLE IF EXISTS maintenance_items")
