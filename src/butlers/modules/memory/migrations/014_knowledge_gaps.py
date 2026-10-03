"""Add owner knowledge gaps (bu-q7vx1q.9).

Revision ID: mem_014
Revises: mem_013

``knowledge_gaps`` records an owner question the butler could not answer: the
(entity, predicate) it was about and its lifecycle (``open`` -> ``answerable`` ->
``delivered``, or ``dismissed`` / ``expired``).  ``knowledge_gap_origins`` holds one
row per asking thread, so two declines merged into one open gap each keep their
own thread and each receive exactly one notice.

Additive-only: two new tables, no existing table is altered.
"""

from __future__ import annotations

from alembic import op

revision = "mem_014"
down_revision = "mem_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_gaps (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            predicate TEXT NOT NULL,
            question_summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'answerable', 'delivered', 'dismissed', 'expired')),
            asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '90 days',
            answered_at TIMESTAMPTZ,
            answered_by_ref TEXT,
            answered_value TEXT,
            answered_authority TEXT,
            answered_authority_entity_id UUID,
            delivered_at TIMESTAMPTZ,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ,
            last_error TEXT,
            dismissed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_gaps_open
        ON knowledge_gaps (entity_id, predicate)
        WHERE status = 'open'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_gaps_status_asked
        ON knowledge_gaps (status, asked_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_gap_origins (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            gap_id UUID NOT NULL REFERENCES knowledge_gaps (id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL,
            request_id UUID,
            channel TEXT NOT NULL,
            asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivered_at TIMESTAMPTZ,
            UNIQUE (gap_id, conversation_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_gap_origins")
    op.execute("DROP TABLE IF EXISTS knowledge_gaps")
