"""Add cost_usd to public.ingestion_events for Spend sort.

Revision ID: core_126
Revises: core_125
Create Date: 2026-06-16 00:00:00.000000

Motivation
----------
The /ingestion "Spend" saved view was disabled because cost is stored per
session (in per-butler ``{schema}.sessions`` tables), not per event.  To
enable a real Spend sort we denormalize a cost_usd summary onto
``public.ingestion_events``.

Population mechanism
--------------------
``cost_usd`` is populated via a lazy write-through: the
``GET /api/ingestion/events/{id}/rollup`` endpoint already performs the
cross-butler fan-out to aggregate session costs.  After computing
``total_cost`` it now writes the result back to this column.  New events
start NULL; the column is updated the first time the event's rollup is
fetched.  ``ORDER BY cost_usd DESC NULLS LAST`` naturally surfaces
high-cost events first while pushing unresolved (NULL) events to the end.

The partial index (cost_usd IS NOT NULL) keeps the index small and fast —
only rows that have been costed benefit from the index; the NULLS LAST
ordering for NULL rows falls back to a seq-scan of the uncosted tail.

Reversibility
-------------
The downgrade path drops the column (and its index) cleanly.  No data
migration is needed on downgrade since cost_usd is a derived, nullable field.
"""

from __future__ import annotations

from alembic import op

revision = "core_126"
down_revision = "core_125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(18, 8)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_cost_usd
        ON public.ingestion_events (cost_usd DESC NULLS LAST)
        WHERE cost_usd IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_cost_usd")
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP COLUMN IF EXISTS cost_usd
        """
    )
