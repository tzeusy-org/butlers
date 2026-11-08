"""Index the cross-connector content-hash dedupe lookup on message_inbox.

Revision ID: sw_061
Revises: sw_060

``ingest_v1`` looks a new message up by ``request_context ->> 'content_hash_key'`` twice
(before and inside the advisory lock). Nothing indexed that expression, so each lookup
walked every partition of ``message_inbox``: 14.2s of execution on dev at ~200k rows,
which was the whole of an owner Telegram message's poll-to-submit gap (bu-dhloi1.178).
The index mirrors ``uq_message_inbox_dedupe_key_received_at`` (migration 001) so the
lookup and its ``ORDER BY received_at DESC LIMIT 1`` are served per partition. Created
on the partitioned parent, it covers every partition, including ones made later.
"""

from alembic import op

revision = "sw_061"
down_revision = "sw_060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_content_hash_received_at
        ON message_inbox ((request_context ->> 'content_hash_key'), received_at DESC)
        WHERE request_context ->> 'content_hash_key' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_content_hash_received_at")
