"""Bind connector expected signals to exact endpoint identity.

Revision ID: core_211
Revises: core_210
Create Date: 2026-09-03 08:20:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_211"
down_revision = "core_210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock("
        "hashtextextended('butlers:core_211:expected_signal_endpoint_identity', 0))"
    )
    op.execute(
        """
        ALTER TABLE public.expected_signals
            ADD COLUMN IF NOT EXISTS producer_endpoint_identity TEXT
        """
    )
    op.execute(
        """
        UPDATE public.expected_signals
        SET measurability = 'unmeasurable',
            unmeasurable_reason = 'producer_endpoint_missing',
            updated_at = now()
        WHERE producer LIKE 'connector:%'
          AND NULLIF(BTRIM(producer_endpoint_identity), '') IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE public.expected_signals
            DROP CONSTRAINT IF EXISTS expected_signals_endpoint_identity_check
        """
    )
    op.execute(
        """
        ALTER TABLE public.expected_signals
            ADD CONSTRAINT expected_signals_endpoint_identity_check CHECK (
                (producer = 'owner' AND producer_endpoint_identity IS NULL)
                OR (
                    producer LIKE 'connector:%'
                    AND (
                        NULLIF(BTRIM(producer_endpoint_identity), '') IS NOT NULL
                        OR (
                            producer_endpoint_identity IS NULL
                            AND measurability = 'unmeasurable'
                            AND unmeasurable_reason = 'producer_endpoint_missing'
                        )
                    )
                )
                OR (
                    producer <> 'owner'
                    AND producer NOT LIKE 'connector:%'
                    AND producer_endpoint_identity IS NULL
                    AND measurability = 'unmeasurable'
                )
            )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_expected_signals_producer_endpoint
            ON public.expected_signals (producer, producer_endpoint_identity)
        """
    )


def downgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock("
        "hashtextextended('butlers:core_211:expected_signal_endpoint_identity', 0))"
    )
    op.execute(
        "DROP INDEX IF EXISTS public.ix_expected_signals_producer_endpoint"
    )
    op.execute(
        "ALTER TABLE public.expected_signals "
        "DROP CONSTRAINT IF EXISTS expected_signals_endpoint_identity_check"
    )
    op.execute(
        "ALTER TABLE public.expected_signals "
        "DROP COLUMN IF EXISTS producer_endpoint_identity"
    )
