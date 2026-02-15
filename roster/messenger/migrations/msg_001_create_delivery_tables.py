"""create_delivery_tables

Revision ID: msg_001
Revises:
Create Date: 2026-02-15 00:00:00.000000

Creates the four core delivery persistence tables for the Messenger butler:
- delivery_requests: canonical normalized request, idempotency key, lineage
  metadata, terminal status
- delivery_attempts: each provider attempt with timestamp, outcome, latency,
  error class, retryability
- delivery_receipts: provider delivery ids, webhook confirmations/read
  receipts when available
- delivery_dead_letter: exhausted or manually quarantined deliveries with
  replay metadata

Idempotency invariant:
- DB uniqueness on idempotency_key prevents duplicate terminal side effects

See docs/roles/messenger_butler.md section 12 for requirements.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "msg_001"
down_revision = None
branch_labels = ("messenger",)
depends_on = None


def upgrade() -> None:
    # delivery_requests: canonical normalized request, lineage, status
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key TEXT NOT NULL UNIQUE,
            request_id UUID,
            origin_butler TEXT NOT NULL,
            channel TEXT NOT NULL,
            intent TEXT NOT NULL CHECK (intent IN ('send', 'reply')),
            target_identity TEXT NOT NULL,
            message_content TEXT NOT NULL,
            subject TEXT,
            request_envelope JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'delivered', 'failed', 'dead_lettered')),
            terminal_error_class TEXT,
            terminal_error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            terminal_at TIMESTAMPTZ
        )
        """
    )

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delivery_requests_request_id
            ON delivery_requests (request_id)
            WHERE request_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delivery_requests_origin_butler
            ON delivery_requests (origin_butler, created_at DESC)
    """)

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_delivery_requests_channel_status
            ON delivery_requests (channel, status, created_at DESC)
        """
    )

    # delivery_attempts: provider attempt log with outcomes
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            latency_ms INTEGER,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'retryable_error', 'non_retryable_error', 'timeout', 'in_progress')),
            error_class TEXT,
            error_message TEXT,
            provider_response JSONB,
            UNIQUE (delivery_request_id, attempt_number)
        )
        """
    )

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delivery_attempts_request_started
            ON delivery_attempts (delivery_request_id, started_at)
    """)

    # delivery_receipts: provider delivery ids and confirmations
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_receipts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
            provider_delivery_id TEXT,
            receipt_type TEXT NOT NULL CHECK (receipt_type IN ('sent', 'delivered', 'read', 'webhook_confirmation')),
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata JSONB DEFAULT '{}'
        )
        """
    )

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delivery_receipts_request
            ON delivery_receipts (delivery_request_id, received_at)
    """)

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_delivery_receipts_provider_id
            ON delivery_receipts (provider_delivery_id)
            WHERE provider_delivery_id IS NOT NULL
        """
    )

    # delivery_dead_letter: exhausted/quarantined deliveries with replay metadata
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_dead_letter (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
            quarantine_reason TEXT NOT NULL,
            error_class TEXT NOT NULL,
            error_summary TEXT NOT NULL,
            total_attempts INTEGER NOT NULL,
            first_attempt_at TIMESTAMPTZ NOT NULL,
            last_attempt_at TIMESTAMPTZ NOT NULL,
            original_request_envelope JSONB NOT NULL,
            all_attempt_outcomes JSONB NOT NULL DEFAULT '[]',
            replay_eligible BOOLEAN NOT NULL DEFAULT true,
            replay_count INTEGER NOT NULL DEFAULT 0,
            discarded_at TIMESTAMPTZ,
            discard_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (delivery_request_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_replay
            ON delivery_dead_letter (replay_eligible, created_at)
            WHERE replay_eligible = true AND discarded_at IS NULL
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_error_class
            ON delivery_dead_letter (error_class, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delivery_dead_letter")
    op.execute("DROP TABLE IF EXISTS delivery_receipts")
    op.execute("DROP TABLE IF EXISTS delivery_attempts")
    op.execute("DROP TABLE IF EXISTS delivery_requests")
