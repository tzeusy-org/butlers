"""Persist the model-resolution receipt on each dispatch attempt.

Revision ID: core_244
Revises: core_243
Create Date: 2026-09-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_244"
down_revision = "core_243"
branch_labels = None
depends_on = None

_LOCK = "core_244_model_resolution_receipt"


def upgrade() -> None:
    # The core chain is replayed once per butler schema while this table is
    # database-global. Serialize the shared DDL across concurrent replays.
    op.execute(f"SELECT pg_advisory_xact_lock(hashtext('{_LOCK}'))")
    op.execute(
        """
        ALTER TABLE public.model_dispatch_attempts
        ADD COLUMN IF NOT EXISTS resolution_receipt JSONB
        """
    )


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtext('{_LOCK}'))")
    op.execute(
        """
        ALTER TABLE public.model_dispatch_attempts
        DROP COLUMN IF EXISTS resolution_receipt
        """
    )
