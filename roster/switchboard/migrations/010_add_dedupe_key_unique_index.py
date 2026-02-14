"""Add unique index on message_inbox dedupe_key for idempotent deduplication.

Revision ID: sw_010
Revises: sw_009
Create Date: 2026-02-15 00:00:00.000000

Migration notes:
- Adds partial unique index on (request_context ->> 'dedupe_key', received_at)
  to enforce deduplication at the database level and prevent race conditions
- Must include received_at (partition key) per PostgreSQL partitioning requirements
- The partial index only applies WHERE dedupe_key IS NOT NULL
- This makes the UniqueViolationError handler in ingest_v1() functional for
  concurrent inserts within the same partition
- Application-level dedup query handles cross-partition duplicates (rare case)
- Dramatically improves performance of dedup queries (indexed lookup vs table scan)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_010"
down_revision = "sw_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_message_inbox_dedupe_key_received_at
        ON message_inbox ((request_context ->> 'dedupe_key'), received_at)
        WHERE request_context ->> 'dedupe_key' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_message_inbox_dedupe_key_received_at")
