"""drop_measurements_table

Revision ID: health_002
Revises: health_001
Create Date: 2026-05-11 00:00:00.000000

Background
----------
The `health.measurements` table was the original write surface for health
measurements (migration health_001).  The `measurement_log` MCP tool was
subsequently migrated to write measurement facts to the shared `facts` table
(predicate ``measurement_{type}``, scope ``health``).  The ``GET
/measurements`` dashboard API endpoint has been updated (bu-3uzhk) to read
from ``facts`` as well.

Nothing writes to ``health.measurements`` any more.  This migration drops the
table and its associated index to remove the dead code surface.

Downgrade
---------
The downgrade recreates the table and index in the same shape as health_001 so
the migration is reversible.  It does **not** backfill data from ``facts``
because no data loss occurred (the tool was the only write path and it already
wrote to ``facts``).
"""

from __future__ import annotations

from alembic import op

revision = "health_002"
down_revision = "health_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the index first (it references the table).
    op.execute("DROP INDEX IF EXISTS idx_measurements_type_measured_at")
    op.execute("DROP TABLE IF EXISTS measurements")


def downgrade() -> None:
    # Recreate the table and index in their original shape so this migration
    # is safely reversible.  No data backfill is performed; the measurements
    # table was a dead write surface by the time this migration was applied.
    op.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            type TEXT NOT NULL,
            value JSONB NOT NULL,
            measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_type_measured_at
            ON measurements (type, measured_at)
    """)
