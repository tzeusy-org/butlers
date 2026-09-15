"""Add the isolated approval-recovery handoff ledger.

Revision ID: msg_004
Revises: msg_003
Create Date: 2026-09-13 00:00:00.000000

This migration adds reconciliation storage only.  It activates no provider
path and stores no rendered envelope, recipient, callback, or raw error.
"""

from __future__ import annotations

from alembic import op

revision = "msg_004"
down_revision = "msg_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_delivery_handoffs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            issuer TEXT NOT NULL CHECK (issuer ~ '^[a-z][a-z0-9_-]{0,62}$'),
            owning_schema TEXT NOT NULL CHECK (owning_schema ~ '^[a-z][a-z0-9_]{0,62}$'),
            subject_key TEXT NOT NULL CHECK (
                subject_key ~ '^approval(-cohort)?:[a-z][a-z0-9_]{0,62}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            ),
            presentation_key TEXT NOT NULL CHECK (
                presentation_key = subject_key || ':p:' || presentation_generation::text
            ),
            presentation_generation INTEGER NOT NULL
                CHECK (presentation_generation BETWEEN 1 AND 1000),
            presentation_mode TEXT NOT NULL
                CHECK (presentation_mode IN ('single', 'burst_digest')),
            handoff_class TEXT CHECK (handoff_class IN ('confirmed', 'safe_retry', 'ambiguous')),
            reason_code TEXT CHECK (reason_code IN (
                'owner_recipient_unavailable', 'callback_secret_unavailable',
                'transport_unavailable', 'provider_preflight_failed',
                'provider_outcome_unknown'
            )),
            provider_reference TEXT CHECK (
                provider_reference IS NULL OR length(provider_reference) BETWEEN 1 AND 256
            ),
            provider_started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (issuer, owning_schema, presentation_key, presentation_mode),
            CONSTRAINT approval_delivery_handoffs_binding_check CHECK (
                (subject_key LIKE 'approval:' || owning_schema || ':%'
                    AND presentation_mode = 'single')
                OR
                (subject_key LIKE 'approval-cohort:' || owning_schema || ':%'
                    AND presentation_mode = 'burst_digest')
            ),
            CHECK (completed_at IS NULL OR provider_started_at IS NULL
                OR completed_at >= provider_started_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approval_delivery_handoffs_reconcile
        ON approval_delivery_handoffs (updated_at, id)
        WHERE provider_started_at IS NOT NULL AND handoff_class IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_delivery_handoffs') IS NOT NULL THEN
                LOCK TABLE approval_delivery_handoffs IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM approval_delivery_handoffs) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade msg_004 while approval delivery handoff data exists';
                END IF;
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE IF EXISTS approval_delivery_handoffs")
