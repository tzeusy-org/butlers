"""drop_reminders_backup: drop relationship._reminders_backup (dead backup table).

The _reminders_backup table was created by migration 007_reminders_to_calendar_events.py
(rev rel_007), which renamed the live ``reminders`` table to ``_reminders_backup`` as a
safety net after migrating all active reminder facts to ``calendar_events``.

The table has 0 rows and no inbound foreign keys. It is confirmed dead with
zero runtime code references. Owner approved dropping it (bead bu-colrv).

Idempotency / cross-chain safety
---------------------------------
Uses ``IF EXISTS`` and a prior ``to_regclass(...)`` guard so the migration is a
safe no-op when the table is already absent. No external chain holds an FK
referencing this table, so DROP CASCADE is not needed.

downgrade()
-----------
The ``reminders`` table DDL is recreated empty on downgrade to match the shape
established by rel_007's rename (and the original create in rel_001). Row data
is not restored — the migration was a one-way cut-over with 0 live rows at
drop time. This matches the convention used by rel_010 and rel_007 itself.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision = "rel_020"
down_revision = "rel_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass('_reminders_backup')")).scalar() is None:
        logger.info("rel_020: _reminders_backup already absent — skipping")
        return
    op.execute("DROP TABLE IF EXISTS _reminders_backup")
    logger.info("rel_020: dropped relationship._reminders_backup")


def downgrade() -> None:
    # Recreate the empty reminders table (original DDL from rel_001 /
    # pre-007 shape). Row data is not restored — the table had 0 rows at
    # drop time and the source reminders table was already gone.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass('_reminders_backup')")).scalar() is not None:
        logger.info("rel_020 downgrade: _reminders_backup already present — skipping")
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS _reminders_backup (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL,
            type       TEXT NOT NULL DEFAULT 'one_time'
                           CHECK (type IN ('one_time', 'recurring_yearly', 'recurring_monthly')),
            content    TEXT NOT NULL,
            due_at     TIMESTAMPTZ,
            dismissed  BOOLEAN NOT NULL DEFAULT false,
            metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    logger.info("rel_020 downgrade: recreated empty _reminders_backup table")
