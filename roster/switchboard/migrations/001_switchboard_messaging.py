"""Switchboard messaging tables: registry, routing, notifications, audit, inbox, extraction, fanout.

Revision ID: sw_001
Revises:
Create Date: 2026-03-26 00:00:00.000000

Collapsed migration covering original sw_001 through sw_016 (minus connector/operator tables).
Creates the core messaging pipeline: butler registry, routing log, notifications,
dashboard audit log, partitioned message inbox, extraction queue/log, and fanout log.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_001"
down_revision = None
branch_labels = ("switchboard",)
depends_on = None


def upgrade() -> None:
    # ── butler_registry (sw_001 + sw_009) ──────────────────────────────────
    op.execute(
        """
        CREATE TABLE butler_registry (
            name TEXT PRIMARY KEY,
            endpoint_url TEXT NOT NULL,
            description TEXT,
            modules JSONB NOT NULL DEFAULT '[]',
            last_seen_at TIMESTAMPTZ,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            eligibility_state TEXT NOT NULL DEFAULT 'active',
            liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
            quarantined_at TIMESTAMPTZ,
            quarantine_reason TEXT,
            route_contract_min INTEGER NOT NULL DEFAULT 1,
            route_contract_max INTEGER NOT NULL DEFAULT 1,
            capabilities JSONB NOT NULL DEFAULT '[]',
            eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT ck_butler_registry_eligibility_state
                CHECK (eligibility_state IN ('active', 'stale', 'quarantined')),
            CONSTRAINT ck_butler_registry_liveness_ttl_positive
                CHECK (liveness_ttl_seconds > 0),
            CONSTRAINT ck_butler_registry_route_contract_bounds
                CHECK (route_contract_min > 0 AND route_contract_max >= route_contract_min)
        )
        """
    )

    # ── butler_registry_eligibility_log (sw_009) ───────────────────────────
    op.execute(
        """
        CREATE TABLE butler_registry_eligibility_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name TEXT NOT NULL,
            previous_state TEXT NOT NULL,
            new_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            previous_last_seen_at TIMESTAMPTZ,
            new_last_seen_at TIMESTAMPTZ,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_registry_eligibility_log_butler_observed
        ON butler_registry_eligibility_log (butler_name, observed_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_registry_eligibility_log_observed
        ON butler_registry_eligibility_log (observed_at DESC)
        """
    )

    # ── routing_log (sw_001 + sw_021 thread_id/source_channel + sw_023 identity) ─
    op.execute(
        """
        CREATE TABLE routing_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_butler TEXT NOT NULL,
            target_butler TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            duration_ms INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            thread_id TEXT,
            source_channel TEXT,
            contact_id UUID,
            entity_id UUID,
            sender_roles TEXT[]
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_routing_log_thread_affinity
        ON routing_log (thread_id, created_at DESC)
        WHERE thread_id IS NOT NULL AND source_channel = 'email'
        """
    )

    op.execute(
        """
        CREATE INDEX idx_routing_log_contact_id
        ON routing_log (contact_id)
        WHERE contact_id IS NOT NULL
        """
    )

    # ── notifications (sw_003) ─────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_butler TEXT NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'sent',
            error TEXT,
            session_id UUID,
            trace_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_notifications_source_butler_created
        ON notifications (source_butler, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_notifications_channel_created
        ON notifications (channel, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_notifications_status
        ON notifications (status)
        """
    )

    # ── dashboard_audit_log (sw_004) ───────────────────────────────────────
    op.execute(
        """
        CREATE TABLE dashboard_audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler TEXT NOT NULL,
            operation TEXT NOT NULL,
            request_summary JSONB NOT NULL DEFAULT '{}',
            result TEXT NOT NULL DEFAULT 'success',
            error TEXT,
            user_context JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_audit_log_butler_created
        ON dashboard_audit_log (butler, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_audit_log_operation
        ON dashboard_audit_log (operation)
        """
    )

    # ── message_inbox (sw_008 canonical schema + sw_010 dedupe index
    #    + sw_013b thread index + sw_015 attachments + sw_016 direction
    #    + sw_019 ingestion_tier) ───────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE message_inbox (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            normalized_text TEXT NOT NULL,
            decomposition_output JSONB,
            dispatch_outcomes JSONB,
            response_summary TEXT,
            lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
            schema_version TEXT NOT NULL DEFAULT 'message_inbox.v2',
            processing_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            final_state_at TIMESTAMPTZ,
            trace_id TEXT,
            session_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            attachments JSONB DEFAULT NULL,
            direction TEXT NOT NULL DEFAULT 'inbound',
            ingestion_tier TEXT NOT NULL DEFAULT 'full',
            PRIMARY KEY (received_at, id)
        ) PARTITION BY RANGE (received_at)
        """
    )

    # sw_008 indexes
    op.execute(
        """
        CREATE INDEX ix_message_inbox_recent_received_at
        ON message_inbox (received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX ix_message_inbox_ctx_source_channel_received_at
        ON message_inbox ((request_context ->> 'source_channel'), received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX ix_message_inbox_ctx_source_sender_received_at
        ON message_inbox ((request_context ->> 'source_sender_identity'), received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX ix_message_inbox_lifecycle_received_at
        ON message_inbox (lifecycle_state, received_at DESC)
        """
    )

    # sw_010 dedupe unique index
    op.execute(
        """
        CREATE UNIQUE INDEX uq_message_inbox_dedupe_key_received_at
        ON message_inbox ((request_context ->> 'dedupe_key'), received_at)
        WHERE request_context ->> 'dedupe_key' IS NOT NULL
        """
    )

    # sw_013b thread identity index
    op.execute(
        """
        CREATE INDEX ix_message_inbox_thread_identity_received_at
        ON message_inbox ((request_context ->> 'source_thread_identity'), received_at DESC)
        WHERE request_context ->> 'source_thread_identity' IS NOT NULL
        """
    )

    # sw_016 thread+direction index
    op.execute(
        """
        CREATE INDEX ix_message_inbox_thread_direction_received_at
        ON message_inbox (
            (request_context ->> 'source_thread_identity'),
            direction,
            received_at DESC
        )
        WHERE request_context ->> 'source_thread_identity' IS NOT NULL
        """
    )

    # sw_019 ingestion tier index
    op.execute(
        """
        CREATE INDEX ix_message_inbox_ingestion_tier_received_at
        ON message_inbox (ingestion_tier, received_at DESC)
        """
    )

    # ── message_inbox partition functions (sw_008 + sw_030 proactive next-month) ─
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

    op.execute(
        """
        CREATE OR REPLACE FUNCTION switchboard_message_inbox_drop_expired_partitions(
            retention INTERVAL DEFAULT INTERVAL '1 month',
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
                WHERE parent.relname = 'message_inbox'
                AND ns.nspname = current_schema()
                AND child.relname ~ '^message_inbox_p[0-9]{6}$'
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
    op.execute("SELECT switchboard_message_inbox_ensure_partition(now())")

    # ── extraction_queue (sw_002) ──────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE extraction_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_message TEXT NOT NULL,
            extraction_type VARCHAR(100) NOT NULL,
            extraction_data JSONB NOT NULL DEFAULT '{}',
            confidence VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            ttl_days INTEGER NOT NULL DEFAULT 7,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ,
            resolved_by VARCHAR(100)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_extraction_queue_status
        ON extraction_queue (status)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_extraction_queue_created_at
        ON extraction_queue (created_at)
        """
    )

    # ── extraction_log (sw_002) ────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE extraction_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_message_preview TEXT,
            extraction_type VARCHAR(100) NOT NULL,
            tool_name VARCHAR(100) NOT NULL,
            tool_args JSONB NOT NULL,
            target_contact_id UUID,
            confidence VARCHAR(20),
            dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_channel VARCHAR(50)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_extraction_log_contact
        ON extraction_log (target_contact_id)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_extraction_log_type
        ON extraction_log (extraction_type)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_extraction_log_dispatched
        ON extraction_log (dispatched_at DESC)
        """
    )

    # ── fanout_execution_log (sw_007) ──────────────────────────────────────
    op.execute(
        """
        CREATE TABLE fanout_execution_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_channel TEXT NOT NULL,
            source_id TEXT,
            tool_name TEXT NOT NULL,
            fanout_mode TEXT NOT NULL,
            join_policy TEXT NOT NULL,
            abort_policy TEXT NOT NULL,
            plan_payload JSONB NOT NULL,
            execution_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_fanout_execution_log_created_at
        ON fanout_execution_log (created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fanout_execution_log")
    op.execute("DROP TABLE IF EXISTS extraction_log")
    op.execute("DROP TABLE IF EXISTS extraction_queue")
    op.execute("DROP TABLE IF EXISTS message_inbox CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS switchboard_message_inbox_drop_expired_partitions("
        "INTERVAL, TIMESTAMPTZ)"
    )
    op.execute("DROP FUNCTION IF EXISTS switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)")
    op.execute("DROP TABLE IF EXISTS dashboard_audit_log")
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS routing_log")
    op.execute("DROP TABLE IF EXISTS butler_registry_eligibility_log")
    op.execute("DROP TABLE IF EXISTS butler_registry")
