"""Remember the last applied backfill progress token so a retried report is a no-op.

Revision ID: sw_054
Revises: sw_053

``backfill.progress`` adds per-batch deltas. Connectors call it through
``CachedMCPClient``, which resends a call once after a transport drop, so a
report the server already applied could be added twice (and trip
``cost_capped`` early). Each report now carries a ``progress_token``; the job
row keeps the last applied one, and a report whose token matches it changes
nothing. Connectors report sequentially per job, so only the latest token can
be replayed and one column suffices.
"""

from __future__ import annotations

from alembic import op

revision = "sw_054"
down_revision = "sw_053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE backfill_jobs ADD COLUMN IF NOT EXISTS last_progress_token TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE backfill_jobs DROP COLUMN IF EXISTS last_progress_token")
