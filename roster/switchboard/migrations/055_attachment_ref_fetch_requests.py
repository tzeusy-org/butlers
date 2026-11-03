"""Let Switchboard ask the owning Gmail connector to fetch a lazy attachment ref.

Revision ID: sw_055
Revises: sw_054

``attachment_materialize`` (bu-jv1moa.48) marks an ``attachment_refs`` row
requested; the Gmail connector for the row's account claims it, downloads the
bytes and records ``blob_ref``. Only the connector holds Gmail credentials, so
the request travels through this row rather than a new Switchboard egress.

- ``endpoint_identity``: the connector account that wrote the ref, so only
  that account fetches it. NULL on rows written before this revision; any
  account may claim those.
- ``fetch_requested_at`` / ``fetch_claimed_at`` / ``fetch_error``: the request
  state. A claim older than the connector's stale window is reclaimed.
"""

from __future__ import annotations

from alembic import op

revision = "sw_055"
down_revision = "sw_054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE attachment_refs
            ADD COLUMN IF NOT EXISTS endpoint_identity TEXT NULL,
            ADD COLUMN IF NOT EXISTS fetch_requested_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS fetch_claimed_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS fetch_error TEXT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachment_refs_fetch_pending
        ON attachment_refs (fetch_requested_at)
        WHERE fetch_requested_at IS NOT NULL AND NOT fetched AND fetch_error IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attachment_refs_fetch_pending")
    op.execute(
        """
        ALTER TABLE attachment_refs
            DROP COLUMN IF EXISTS fetch_error,
            DROP COLUMN IF EXISTS fetch_claimed_at,
            DROP COLUMN IF EXISTS fetch_requested_at,
            DROP COLUMN IF EXISTS endpoint_identity
        """
    )
