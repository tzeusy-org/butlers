"""Create switchboard.backfill_jobs table for MCP-mediated connector backfill orchestration.

Revision ID: sw_017
Revises: sw_016
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Creates backfill_jobs table with full lifecycle state (pending/active/paused/completed/cancelled/cost_capped/error).
- Includes cursor (JSONB) for resume semantics, progress counters, cost tracking, and lifecycle timestamps.
- Adds status constraint and two operational indexes (by status; by connector_type+endpoint_identity).
- Table is owned by the Switchboard schema; connectors access via MCP tools only (no direct DB).
- See docs/connectors/email_backfill.md (Section 4) and docs/roles/switchboard_butler.md for contract details.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_017"
down_revision = "sw_016"
branch_labels = None
depends_on = None

# All allowed status values, matching the spec contract.
_VALID_STATUSES = ("pending", "active", "paused", "completed", "cancelled", "cost_capped", "error")


def upgrade() -> None:
    # Create backfill_jobs table
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

    # Index for polling and status-based queries (connector polling for pending jobs)
    op.execute(
        """
        CREATE INDEX idx_backfill_jobs_status
            ON backfill_jobs (status)
        """
    )

    # Index for connector-scoped queries (lookup jobs by connector_type+endpoint_identity)
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


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_backfill_jobs_connector")
    op.execute("DROP INDEX IF EXISTS idx_backfill_jobs_status")
    op.execute("DROP TABLE IF EXISTS backfill_jobs")
