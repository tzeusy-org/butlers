"""Create connector heartbeat registry and log tables.

Revision ID: sw_013
Revises: sw_012
Create Date: 2026-02-16 00:00:00.000000

Migration notes:
- Upgrade creates connector_registry (current state) and connector_heartbeat_log
  (historical append-only log).
- connector_heartbeat_log is month-partitioned by received_at.
- Initial partition is created for the current month.
- Downgrade drops both tables.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_013"
down_revision = "sw_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create connector_registry table for current state tracking
    op.execute(
        """
        CREATE TABLE connector_registry (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            instance_id UUID,
            version TEXT,
            state TEXT NOT NULL DEFAULT 'unknown',
            error_message TEXT,
            uptime_s INTEGER,
            last_heartbeat_at TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            registered_via TEXT NOT NULL DEFAULT 'self',
            counter_messages_ingested BIGINT DEFAULT 0,
            counter_messages_failed BIGINT DEFAULT 0,
            counter_source_api_calls BIGINT DEFAULT 0,
            counter_checkpoint_saves BIGINT DEFAULT 0,
            counter_dedupe_accepted BIGINT DEFAULT 0,
            checkpoint_cursor TEXT,
            checkpoint_updated_at TIMESTAMPTZ,
            PRIMARY KEY (connector_type, endpoint_identity)
        )
        """
    )

    # Create indexes for connector_registry
    op.execute(
        """
        CREATE INDEX ix_connector_registry_last_heartbeat_at
        ON connector_registry (last_heartbeat_at DESC)
        WHERE last_heartbeat_at IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_registry_state_last_heartbeat
        ON connector_registry (state, last_heartbeat_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_registry_connector_type
        ON connector_registry (connector_type)
        """
    )

    # Create connector_heartbeat_log table (partitioned by received_at)
    op.execute(
        """
        CREATE TABLE connector_heartbeat_log (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            instance_id UUID,
            state TEXT NOT NULL,
            error_message TEXT,
            uptime_s INTEGER,
            counter_messages_ingested BIGINT,
            counter_messages_failed BIGINT,
            counter_source_api_calls BIGINT,
            counter_checkpoint_saves BIGINT,
            counter_dedupe_accepted BIGINT,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ,
            PRIMARY KEY (received_at, id)
        ) PARTITION BY RANGE (received_at)
        """
    )

    # Create indexes for connector_heartbeat_log
    op.execute(
        """
        CREATE INDEX ix_connector_heartbeat_log_connector_type_received_at
        ON connector_heartbeat_log (connector_type, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_heartbeat_log_endpoint_received_at
        ON connector_heartbeat_log (connector_type, endpoint_identity, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_heartbeat_log_state_received_at
        ON connector_heartbeat_log (state, received_at DESC)
        """
    )

    # Create partition management function
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
            partition_name := format('connector_heartbeat_log_p%s', to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
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

    # Create partition cleanup function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_connector_heartbeat_log_drop_expired_partitions(
            retention INTERVAL DEFAULT INTERVAL '7 days',
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS INTEGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
            partition_name TEXT;
            partition_month DATE;
            cutoff_month DATE;
            dropped_count INTEGER := 0;
        BEGIN
            cutoff_month := date_trunc('month', reference_ts - retention)::date;

            FOR partition_name IN
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_class child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                WHERE parent.relname = 'connector_heartbeat_log'
                AND ns.nspname = current_schema()
                AND child.relname ~ '^connector_heartbeat_log_p[0-9]{6}$'
            LOOP
                partition_month := to_date(substring(partition_name from '[0-9]{6}$'), 'YYYYMM');
                IF partition_month < cutoff_month THEN
                    EXECUTE format('DROP TABLE IF EXISTS %I', partition_name);
                    dropped_count := dropped_count + 1;
                END IF;
            END LOOP;

            RETURN dropped_count;
        END;
        $$
        """
    )

    # Create initial partition for current month
    op.execute("SELECT switchboard_connector_heartbeat_log_ensure_partition(now())")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_heartbeat_log CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS switchboard_connector_heartbeat_log_drop_expired_partitions("
        "INTERVAL, TIMESTAMPTZ)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)"
    )
    op.execute("DROP TABLE IF EXISTS connector_registry")
