"""Add ingestion_tier to message_inbox and create email_metadata_refs table.

Revision ID: sw_019
Revises: sw_018
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Adds optional TEXT column `ingestion_tier` to message_inbox (default 'full').
  Valid values: 'full' (Tier 1 standard pipeline), 'metadata' (Tier 2 bypass).
- Adds index on (ingestion_tier, received_at DESC) to support querying by tier.
- Creates `switchboard.email_metadata_refs` table for Tier 2 metadata storage.
  This table persists slim email envelopes that bypass LLM classification.

Per docs/connectors/email_ingestion_policy.md:
- Tier 1 ("full"): full ingest pipeline — classify + route + butler processing.
- Tier 2 ("metadata"): metadata-only — bypass LLM, persist reference only.
- Tier 3 ("skip"): dropped at connector level, never reaches Switchboard.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_019"
down_revision = "sw_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- message_inbox: add ingestion_tier column ---
    op.execute(
        """
        ALTER TABLE message_inbox
        ADD COLUMN IF NOT EXISTS ingestion_tier TEXT NOT NULL DEFAULT 'full'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN message_inbox.ingestion_tier IS
        'Ingestion tier from ingest.v1 control: full (Tier 1 standard pipeline) '
        'or metadata (Tier 2 bypass LLM classification, metadata ref only). '
        'Matches IngestControlV1.ingestion_tier semantics.'
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_ingestion_tier_received_at
        ON message_inbox (ingestion_tier, received_at DESC)
        """
    )

    # --- email_metadata_refs table ---
    # Stores Tier 2 slim email envelopes: sender/subject/date/labels/summary only.
    # Full body is fetched on demand from Gmail API by message_id.
    # See docs/connectors/email_ingestion_policy.md section 6.
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

    # Uniqueness: one metadata ref per (mailbox, message) pair
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_metadata_refs_endpoint_message
        ON email_metadata_refs (endpoint_identity, gmail_message_id)
        """
    )

    # Timeline query index: all metadata refs for a mailbox ordered by time
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_received_at_desc
        ON email_metadata_refs (received_at DESC)
        """
    )

    # Sender lookup index
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_sender_received_at
        ON email_metadata_refs (sender, received_at DESC)
        """
    )

    # message_inbox linkage index (optional FK ref for tracing)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_inbox_id
        ON email_metadata_refs (message_inbox_id)
        WHERE message_inbox_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_metadata_refs")
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_ingestion_tier_received_at")
    op.execute(
        """
        ALTER TABLE message_inbox
        DROP COLUMN IF EXISTS ingestion_tier
        """
    )
