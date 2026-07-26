"""domain_event_deliveries: attempt_count + failed_permanent terminal status.

Revision ID: core_190
Revises: core_189
Create Date: 2026-07-26 00:00:00.000000

bu-1yw6d (PR #3585 review follow-up): the periodic reconciliation sweep
(``run_domain_event_reconciliation_sweep``, ``src/butlers/core_tools/
_domain_events.py``) needs to (a) bound how many times it retries a
``failed`` delivery before giving up, and (b) surface a permanently-failed
delivery as a distinct terminal state rather than an indefinitely-retryable
``failed`` -- see that module's ``mark_delivery_failed`` for the transition
logic and ``src/butlers/core/domain_events.py`` for the candidate-selection
reads this new column/status back.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_190"
down_revision = "core_189"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0
    """)

    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        DROP CONSTRAINT IF EXISTS chk_domain_event_deliveries_status
    """)
    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        ADD CONSTRAINT chk_domain_event_deliveries_status
        CHECK (status IN ('pending', 'delivered', 'conflict', 'failed', 'failed_permanent'))
    """)

    # Reconciliation sweep's candidate-selection read path (status +
    # updated_at together) -- mirrors idx_domain_event_deliveries_subscriber_
    # status's rationale but scoped to the sweep's own filter shape (status,
    # staleness), not the dashboard's per-subscriber read.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_event_deliveries_status_updated_at
        ON public.domain_event_deliveries (status, updated_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_domain_event_deliveries_status_updated_at")
    # Reclassify failed_permanent rows to failed before re-narrowing the constraint
    op.execute(
        "UPDATE public.domain_event_deliveries "
        "SET status = 'failed' WHERE status = 'failed_permanent'"
    )
    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        DROP CONSTRAINT IF EXISTS chk_domain_event_deliveries_status
    """)
    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        ADD CONSTRAINT chk_domain_event_deliveries_status
        CHECK (status IN ('pending', 'delivered', 'conflict', 'failed'))
    """)
    op.execute("""
        ALTER TABLE public.domain_event_deliveries
        DROP COLUMN IF EXISTS attempt_count
    """)
