"""ingestion_replay_pending: allow replay_pending status on ingestion_events.

Revision ID: core_049
Revises: core_048
Create Date: 2026-04-04 00:00:00.000000

Widens the CHECK constraint on ``public.ingestion_events.status`` to include
``'replay_pending'`` so that the dashboard replay button can set this status
for already-ingested events.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_049"
down_revision = "core_048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP CONSTRAINT IF EXISTS ck_ingestion_events_status
        """
    )
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        ADD CONSTRAINT ck_ingestion_events_status
            CHECK (status IN ('ingested', 'failed', 'replay_pending'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP CONSTRAINT IF EXISTS ck_ingestion_events_status
        """
    )
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        ADD CONSTRAINT ck_ingestion_events_status
            CHECK (status IN ('ingested', 'failed'))
        """
    )
