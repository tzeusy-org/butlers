"""connectors: upgrade ensure_partition to proactively create next month's partition.

Revision ID: core_028
Revises: core_027
Create Date: 2026-03-12 00:00:00.000000

Replaces connectors_filtered_events_ensure_partition() with a version that
creates both the requested month's partition AND the next month's partition.

This prevents data loss at month boundaries: if the first call of a new month
fails (permission issue, transient error), the partition already exists from
the previous month's calls.

The function remains idempotent (CREATE TABLE IF NOT EXISTS).

Mirrors the same pattern applied to switchboard tables in sw_030.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_028"
down_revision = "core_027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace ensure_partition function to also create next month's partition
    op.execute(
        """
        CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start    TIMESTAMPTZ;
            month_end      TIMESTAMPTZ;
            partition_name TEXT;
            next_start     TIMESTAMPTZ;
            next_end       TIMESTAMPTZ;
            next_name      TEXT;
        BEGIN
            -- Current month partition
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('filtered_events_%s', to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS connectors.%I '
                'PARTITION OF connectors.filtered_events '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                month_start,
                month_end
            );

            -- Next month partition (proactive)
            next_start := month_end;
            next_end   := next_start + INTERVAL '1 month';
            next_name  := format('filtered_events_%s', to_char(next_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS connectors.%I '
                'PARTITION OF connectors.filtered_events '
                'FOR VALUES FROM (%L) TO (%L)',
                next_name,
                next_start,
                next_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )

    # Ensure current and next month partitions exist now
    op.execute("SELECT connectors_filtered_events_ensure_partition(now())")


def downgrade() -> None:
    # Restore single-month version from core_027
    op.execute(
        """
        CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start     TIMESTAMPTZ;
            month_end       TIMESTAMPTZ;
            partition_name  TEXT;
        BEGIN
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('filtered_events_%s', to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS connectors.%I '
                'PARTITION OF connectors.filtered_events '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                month_start,
                month_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )
