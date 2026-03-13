"""connectors: move ensure_partition function into connectors schema.

Revision ID: core_031
Revises: core_030
Create Date: 2026-03-13 00:00:00.000000

core_027 and core_028 created connectors_filtered_events_ensure_partition()
without schema qualification, so it landed in whichever butler schema was
active during migration.  The gmail connector runs with search_path
"shared,public" and cannot see the function, causing UndefinedFunctionError
on every filtered-event flush.

This migration creates the function in the connectors schema where the
table it manages lives, making it visible to any pool that can reach the
connectors schema (including schema-qualified calls).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_031"
down_revision = "core_030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION connectors.connectors_filtered_events_ensure_partition(
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

    # Verify it works
    op.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition(now())"
    )


def downgrade() -> None:
    # Drop the connectors-schema copy; butler-schema copies from core_027/028
    # remain and continue to work for butler-scoped pools.
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " connectors.connectors_filtered_events_ensure_partition(TIMESTAMPTZ)"
    )
