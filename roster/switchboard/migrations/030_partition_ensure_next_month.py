"""Upgrade ensure_partition functions to proactively create next month's partition.

Revision ID: sw_030
Revises: sw_029
Create Date: 2026-03-11 00:00:00.000000

Migration notes:
- Replaces switchboard_connector_heartbeat_log_ensure_partition() and
  switchboard_message_inbox_ensure_partition() with versions that create
  both the requested month's partition AND the next month's partition.
- This prevents data loss at month boundaries: if the first call of a new
  month fails (permission issue, transient error), the partition already
  exists from the previous month's calls.
- Both functions remain idempotent (CREATE TABLE IF NOT EXISTS).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_030"
down_revision = "sw_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace connector_heartbeat_log ensure_partition to also create next month
    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_connector_heartbeat_log_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start   TIMESTAMPTZ;
            month_end     TIMESTAMPTZ;
            partition_name TEXT;
            next_start    TIMESTAMPTZ;
            next_end      TIMESTAMPTZ;
            next_name     TEXT;
        BEGIN
            -- Current month partition
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('connector_heartbeat_log_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            -- Next month partition (proactive)
            next_start := month_end;
            next_end   := next_start + INTERVAL '1 month';
            next_name  := format('connector_heartbeat_log_p%s',
                                  to_char(next_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                next_name, next_start, next_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )

    # Replace message_inbox ensure_partition to also create next month
    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_message_inbox_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start   TIMESTAMPTZ;
            month_end     TIMESTAMPTZ;
            partition_name TEXT;
            next_start    TIMESTAMPTZ;
            next_end      TIMESTAMPTZ;
            next_name     TEXT;
        BEGIN
            -- Current month partition
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('message_inbox_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            -- Next month partition (proactive)
            next_start := month_end;
            next_end   := next_start + INTERVAL '1 month';
            next_name  := format('message_inbox_p%s',
                                  to_char(next_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                'FOR VALUES FROM (%L) TO (%L)',
                next_name, next_start, next_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )

    # Ensure April 2026 partitions exist now (proactive)
    op.execute("SELECT switchboard_connector_heartbeat_log_ensure_partition(now())")
    op.execute("SELECT switchboard_message_inbox_ensure_partition(now())")


def downgrade() -> None:
    # Restore single-month versions from sw_013 and sw_008
    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_connector_heartbeat_log_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start TIMESTAMPTZ;
            month_end TIMESTAMPTZ;
            partition_name TEXT;
        BEGIN
            month_start := date_trunc('month', reference_ts);
            month_end := month_start + INTERVAL '1 month';
            partition_name := format('connector_heartbeat_log_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_message_inbox_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start TIMESTAMPTZ;
            month_end TIMESTAMPTZ;
            partition_name TEXT;
        BEGIN
            month_start := date_trunc('month', reference_ts);
            month_end := month_start + INTERVAL '1 month';
            partition_name := format('message_inbox_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            RETURN partition_name;
        END;
        $$
        """
    )
