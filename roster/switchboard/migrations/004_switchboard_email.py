"""Switchboard email tables: email metadata refs, attachment refs, backfill jobs.

Revision ID: sw_004
Revises: sw_003
Create Date: 2026-03-26 00:00:00.000000

Collapsed migration covering original sw_018 (backfill_jobs), sw_019 (email_metadata_refs),
and sw_020 (attachment_refs + email metadata pruning function).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_004"
down_revision = "sw_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── email_metadata_refs (sw_019) ──────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_metadata_refs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint_identity TEXT NOT NULL,
            gmail_message_id TEXT NOT NULL,
            thread_id TEXT,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL,
            labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            summary TEXT NOT NULL,
            tier INTEGER NOT NULL DEFAULT 2 CHECK (tier = 2),
            message_inbox_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE email_metadata_refs IS
        'Tier 2 email metadata references. Stores slim email envelopes that '
        'bypass LLM classification. Full body is fetched on demand from Gmail '
        'API by gmail_message_id. See docs/connectors/email_ingestion_policy.md section 6.'
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_metadata_refs_endpoint_message
        ON email_metadata_refs (endpoint_identity, gmail_message_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_received_at_desc
        ON email_metadata_refs (received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_sender_received_at
        ON email_metadata_refs (sender, received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_inbox_id
        ON email_metadata_refs (message_inbox_id)
        WHERE message_inbox_id IS NOT NULL
        """
    )

    # ── attachment_refs (sw_020) ──────────────────────────────────────────────
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

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachment_refs_fetched_created_at
        ON attachment_refs (fetched, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachment_refs_media_type_created_at
        ON attachment_refs (media_type, created_at DESC)
        """
    )

    # ── backfill_jobs (sw_018) ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE backfill_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            target_categories JSONB NOT NULL DEFAULT '[]',
            date_from DATE NOT NULL,
            date_to DATE NOT NULL,
            rate_limit_per_hour INTEGER NOT NULL DEFAULT 100,
            daily_cost_cap_cents INTEGER NOT NULL DEFAULT 500,
            status TEXT NOT NULL DEFAULT 'pending',
            cursor JSONB,
            rows_processed INTEGER NOT NULL DEFAULT 0,
            rows_skipped INTEGER NOT NULL DEFAULT 0,
            cost_spent_cents INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT backfill_jobs_status_check CHECK (
                status IN ('pending', 'active', 'paused', 'completed', 'cancelled', 'cost_capped', 'error')
            )
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE backfill_jobs IS
        'Durable lifecycle and progress state for MCP-mediated connector backfill jobs. '
        'Connectors interact exclusively via Switchboard MCP tools (backfill.poll, backfill.progress). '
        'See docs/connectors/email_backfill.md section 4 for contract.'
        """
    )

    op.execute(
        """
        CREATE INDEX idx_backfill_jobs_status
            ON backfill_jobs (status)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_backfill_jobs_connector
            ON backfill_jobs (connector_type, endpoint_identity)
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN backfill_jobs.status IS
        'Lifecycle state: pending|active|paused|completed|cancelled|cost_capped|error'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN backfill_jobs.cursor IS
        'Opaque JSONB resume cursor updated by backfill.progress. Used by connector to resume after pause/restart.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN backfill_jobs.daily_cost_cap_cents IS
        'Maximum allowed spend in cents per day. When cost_spent_cents reaches this, status transitions to cost_capped.'
        """
    )

    # ── switchboard_prune_email_metadata_refs function (sw_020) ───────────────
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
    op.execute("DROP INDEX IF EXISTS idx_backfill_jobs_connector")
    op.execute("DROP INDEX IF EXISTS idx_backfill_jobs_status")
    op.execute("DROP TABLE IF EXISTS backfill_jobs")
    op.execute("DROP INDEX IF EXISTS ix_attachment_refs_media_type_created_at")
    op.execute("DROP INDEX IF EXISTS ix_attachment_refs_fetched_created_at")
    op.execute("DROP TABLE IF EXISTS attachment_refs")
    op.execute("DROP TABLE IF EXISTS email_metadata_refs")
