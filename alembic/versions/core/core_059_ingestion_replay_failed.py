"""ingestion_replay_failed: allow replay_failed status on ingestion_events.

Revision ID: core_059
Revises: core_058
Create Date: 2026-04-06 00:00:00.000000

Widens the CHECK constraint on ``public.ingestion_events.status`` to include
``'replay_failed'`` so that failed replay attempts are correctly recorded
instead of leaving rows stuck in ``'replay_pending'`` forever.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_059"
down_revision = "core_058"
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
            CHECK (status IN ('ingested', 'failed', 'replay_pending', 'replay_failed'))
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
            CHECK (status IN ('ingested', 'failed', 'replay_pending'))
        """
    )
