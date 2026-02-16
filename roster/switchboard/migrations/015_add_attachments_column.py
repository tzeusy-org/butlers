"""Add attachments column to message_inbox table.

Revision ID: sw_015
Revises: sw_014
Create Date: 2026-02-16 00:00:00.000000

Migration notes:
- Adds nullable JSONB column for storing attachment metadata from ingest.v1 envelopes
- Column mirrors IngestPayloadV1.attachments field structure
- Backwards-compatible with existing rows (NULL default, no backfill needed)
- ALTER TABLE on parent partition propagates to all child partitions
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_015"
down_revision = "sw_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_inbox
        ADD COLUMN attachments JSONB DEFAULT NULL
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN message_inbox.attachments IS
        'List of IngestAttachment objects: [{media_type, storage_ref, size_bytes, filename, width, height}]'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_inbox
        DROP COLUMN IF EXISTS attachments
        """
    )
