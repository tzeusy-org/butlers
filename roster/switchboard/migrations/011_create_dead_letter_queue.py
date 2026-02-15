"""Create dead_letter_queue table for exhausted/failed requests.

Revision ID: sw_011
Revises: sw_010
Create Date: 2026-02-15 00:00:00.000000

Migration notes:
- Upgrade creates dead_letter_queue table for failed requests beyond retry policy.
- Includes replay support with original lineage preservation.
- Downgrade drops the table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_011"
down_revision = "sw_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dead_letter_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            original_request_id UUID NOT NULL,
            source_table TEXT NOT NULL,
            failure_reason TEXT NOT NULL,
            failure_category TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_retry_at TIMESTAMPTZ,
            original_payload JSONB NOT NULL,
            request_context JSONB NOT NULL,
            error_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            replay_eligible BOOLEAN NOT NULL DEFAULT true,
            replayed_at TIMESTAMPTZ,
            replayed_request_id UUID,
            replay_outcome TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_failure_category CHECK (
                failure_category IN (
                    'timeout',
                    'retry_exhausted',
                    'circuit_open',
                    'policy_violation',
                    'validation_error',
                    'downstream_failure',
                    'unknown'
                )
            ),
            CONSTRAINT valid_replay_outcome CHECK (
                replay_outcome IS NULL OR replay_outcome IN (
                    'success',
                    'failed',
                    'rejected'
                )
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_dead_letter_queue_created_at
        ON dead_letter_queue (created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_dead_letter_queue_failure_category_created_at
        ON dead_letter_queue (failure_category, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_dead_letter_queue_replay_eligible
        ON dead_letter_queue (replay_eligible, created_at DESC)
        WHERE replay_eligible = true
        """
    )
    op.execute(
        """
        CREATE INDEX ix_dead_letter_queue_original_request_id
        ON dead_letter_queue (original_request_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dead_letter_queue")
