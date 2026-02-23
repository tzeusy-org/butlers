"""Add attachment_refs table and email_metadata_refs retention pruning.

Revision ID: sw_020
Revises: sw_019
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Creates `switchboard.attachment_refs` for lazy attachment fetching model.
  Stores metadata references for non-calendar attachments at ingest time;
  full payload is fetched on demand. See docs/connectors/attachment_handling.md §5.
- Adds a DB-level `switchboard_prune_email_metadata_refs(INTERVAL, TIMESTAMPTZ)`
  function for configurable Tier 2 retention pruning.
  Default retention: 90 days. See docs/connectors/email_ingestion_policy.md §10.
- `attachment_refs` uses a composite PK of (message_id, attachment_id) per spec §5.2.
- Required indexes: (fetched, created_at DESC) for lazy-fetch queueing;
  (media_type, created_at DESC) for analytics/policy audits.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_020"
down_revision = "sw_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- attachment_refs table ---
    # Stores metadata references for lazy-fetched email attachments.
    # Full attachment bytes are downloaded from Gmail API on demand.
    # See docs/connectors/attachment_handling.md section 5.2.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS attachment_refs (
            message_id TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            filename TEXT NULL,
            media_type TEXT NOT NULL,
            size_bytes BIGINT NOT NULL,
            fetched BOOLEAN NOT NULL DEFAULT FALSE,
            blob_ref TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (message_id, attachment_id)
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE attachment_refs IS
        'Lazy attachment fetch references for email messages. Stores attachment '
        'metadata at ingest time; full payload fetched on demand from Gmail API. '
        'text/calendar attachments are eagerly fetched and have blob_ref populated '
        'at ingest time. See docs/connectors/attachment_handling.md section 5.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN attachment_refs.message_id IS
        'Gmail message_id (part of PK). References the email message containing the attachment.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN attachment_refs.attachment_id IS
        'Gmail attachment_id (part of PK). Stable identifier for the attachment within the message.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN attachment_refs.fetched IS
        'TRUE when blob_ref is populated and attachment bytes have been stored in BlobStore.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN attachment_refs.blob_ref IS
        'BlobStore reference populated after lazy fetch. NULL until attachment is materialized.'
        """
    )

    # Index for lazy-fetch queueing and inspection: unfetched attachments ordered by age
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachment_refs_fetched_created_at
        ON attachment_refs (fetched, created_at DESC)
        """
    )

    # Index for analytics and policy audits: distribution by MIME type over time
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachment_refs_media_type_created_at
        ON attachment_refs (media_type, created_at DESC)
        """
    )

    # --- email_metadata_refs Tier 2 retention pruning function ---
    # Provides configurable DELETE-based pruning for Tier 2 metadata references.
    # Default retention: 90 days (per docs/connectors/email_ingestion_policy.md §10).
    # Accepts an INTERVAL so schedulers can call it with custom retention windows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_prune_email_metadata_refs(
            retention INTERVAL DEFAULT INTERVAL '90 days',
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS INTEGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
            deleted_count INTEGER;
        BEGIN
            WITH deleted AS (
                DELETE FROM email_metadata_refs
                WHERE created_at < (reference_ts - retention)
                RETURNING 1
            )
            SELECT COUNT(*) INTO deleted_count FROM deleted;

            RETURN deleted_count;
        END;
        $$
        """
    )

    op.execute(
        """
        COMMENT ON FUNCTION switchboard_prune_email_metadata_refs(INTERVAL, TIMESTAMPTZ) IS
        'Prune Tier 2 email metadata references older than the given retention interval. '
        'Default retention is 90 days. Returns number of rows deleted. '
        'Called by scheduled jobs; safe to call repeatedly (idempotent). '
        'See docs/connectors/email_ingestion_policy.md section 10.'
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS switchboard_prune_email_metadata_refs(INTERVAL, TIMESTAMPTZ)"
    )
    op.execute("DROP INDEX IF EXISTS ix_attachment_refs_media_type_created_at")
    op.execute("DROP INDEX IF EXISTS ix_attachment_refs_fetched_created_at")
    op.execute("DROP TABLE IF EXISTS attachment_refs")
