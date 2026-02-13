"""
Add ingress deduplication/idempotency columns to message_inbox
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_006"
down_revision = "sw_005"
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_inbox
        ADD COLUMN IF NOT EXISTS source_endpoint_identity TEXT,
        ADD COLUMN IF NOT EXISTS source_sender_identity TEXT,
        ADD COLUMN IF NOT EXISTS source_thread_identity TEXT,
        ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
        ADD COLUMN IF NOT EXISTS dedupe_key TEXT,
        ADD COLUMN IF NOT EXISTS dedupe_strategy TEXT,
        ADD COLUMN IF NOT EXISTS dedupe_last_seen_at TIMESTAMPTZ
        """
    )

    op.execute(
        """
        UPDATE message_inbox
        SET source_endpoint_identity = source_channel
        WHERE source_endpoint_identity IS NULL
        """
    )
    op.execute(
        """
        UPDATE message_inbox
        SET source_sender_identity = sender_id
        WHERE source_sender_identity IS NULL
        """
    )
    op.execute(
        """
        UPDATE message_inbox
        SET dedupe_strategy = 'legacy'
        WHERE dedupe_strategy IS NULL
        """
    )
    op.execute(
        """
        UPDATE message_inbox
        SET dedupe_last_seen_at = received_at
        WHERE dedupe_last_seen_at IS NULL
        """
    )

    op.execute(
        """
        ALTER TABLE message_inbox
        ALTER COLUMN source_endpoint_identity SET NOT NULL,
        ALTER COLUMN source_sender_identity SET NOT NULL,
        ALTER COLUMN dedupe_strategy SET NOT NULL,
        ALTER COLUMN dedupe_last_seen_at SET NOT NULL
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_message_inbox_dedupe_key
        ON message_inbox (dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_idempotency_key
        ON message_inbox (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_idempotency_key")
    op.execute("DROP INDEX IF EXISTS uq_message_inbox_dedupe_key")
    op.execute(
        """
        ALTER TABLE message_inbox
        DROP COLUMN IF EXISTS dedupe_last_seen_at,
        DROP COLUMN IF EXISTS dedupe_strategy,
        DROP COLUMN IF EXISTS dedupe_key,
        DROP COLUMN IF EXISTS idempotency_key,
        DROP COLUMN IF EXISTS source_thread_identity,
        DROP COLUMN IF EXISTS source_sender_identity,
        DROP COLUMN IF EXISTS source_endpoint_identity
        """
    )
