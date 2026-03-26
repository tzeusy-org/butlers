"""Switchboard operations tables: dead letter queue, operator audit log, connector registry, heartbeat log.

Revision ID: sw_002
Revises: sw_001
Create Date: 2026-03-26 00:00:00.000000

Collapsed migration covering original sw_011 (dead_letter_queue), sw_012 (operator_audit_log),
sw_013 (connector_registry + connector_heartbeat_log), sw_022 (capabilities column),
sw_030 (proactive next-month partition), and sw_031 (settings column).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_002"
down_revision = "sw_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── dead_letter_queue (sw_011) ────────────────────────────────────────────
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

    # ── operator_audit_log (sw_012) ───────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE operator_audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_type TEXT NOT NULL,
            target_request_id UUID NOT NULL,
            target_table TEXT NOT NULL,
            operator_identity TEXT NOT NULL,
            reason TEXT NOT NULL,
            action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            outcome TEXT NOT NULL,
            outcome_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            performed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_action_type CHECK (
                action_type IN (
                    'manual_reroute',
                    'cancel_request',
                    'abort_request',
                    'controlled_replay',
                    'controlled_retry',
                    'force_complete'
                )
            ),
            CONSTRAINT valid_outcome CHECK (
                outcome IN ('success', 'failed', 'rejected', 'partial')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_performed_at
        ON operator_audit_log (performed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_action_type_performed_at
        ON operator_audit_log (action_type, performed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_target_request_id
        ON operator_audit_log (target_request_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_operator_identity_performed_at
        ON operator_audit_log (operator_identity, performed_at DESC)
        """
    )

    # ── connector_registry (sw_013 + sw_022 capabilities + sw_031 settings) ──
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
            capabilities JSONB DEFAULT NULL,
            settings JSONB DEFAULT NULL,
            PRIMARY KEY (connector_type, endpoint_identity)
        )
        """
    )

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

    # ── connector_heartbeat_log (sw_013 + sw_030 proactive next-month) ───────
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

    # Partition management function (sw_030 proactive version — creates current + next month)
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

    # Partition cleanup function (sw_013)
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

    # Create initial partitions for current and next month
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
    op.execute("DROP TABLE IF EXISTS operator_audit_log")
    op.execute("DROP TABLE IF EXISTS dead_letter_queue")
