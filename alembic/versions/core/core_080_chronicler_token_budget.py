"""chronicler_token_budget: backfill max_token_budget on chronicler.scheduled_tasks.

Revision ID: core_080
Revises: core_079
Create Date: 2026-04-25 00:00:00.000000

``core_050`` added ``max_token_budget`` to a hardcoded list of butler
schemas, but the ``chronicler`` schema was added to the roster after
that migration shipped and was never included.  As a result, chronicler
fails at boot when the scheduler reconciles ``[[butler.schedule]]``
entries against ``scheduled_tasks`` and references the missing column.

This migration backfills the column for chronicler only.  It is
idempotent (``ADD COLUMN IF NOT EXISTS``) and a no-op on environments
where chronicler's ``scheduled_tasks`` table is absent.
"""

from __future__ import annotations

from alembic import op

revision = "core_080"
down_revision = "core_079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'scheduled_tasks'
            ) THEN
                ALTER TABLE chronicler.scheduled_tasks
                    ADD COLUMN IF NOT EXISTS max_token_budget INTEGER;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'scheduled_tasks'
            ) THEN
                ALTER TABLE chronicler.scheduled_tasks
                    DROP COLUMN IF EXISTS max_token_budget;
            END IF;
        END
        $$;
        """
    )
