"""Partition message_inbox lifecycle store and add canonical payload schema.

Revision ID: sw_008
Revises: sw_007
Create Date: 2026-02-14 00:00:00.000000

Migration notes:
- Upgrade rewrites message_inbox into a month-partitioned lifecycle store.
- Downgrade reconstructs the legacy sw_007 table shape from sw_008 data.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_008"
down_revision = "sw_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve legacy data during table rewrite.
    op.execute("ALTER TABLE message_inbox RENAME TO message_inbox_sw007_backup")

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
            PRIMARY KEY (received_at, id)
        ) PARTITION BY RANGE (received_at)
        """
    )

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
            partition_name := format('message_inbox_p%s', to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
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

    # Ensure active and near-future partitions exist, then backfill historical partitions.
    op.execute("SELECT switchboard_message_inbox_ensure_partition(now())")
    op.execute("SELECT switchboard_message_inbox_ensure_partition(now() + INTERVAL '1 month')")
    op.execute(
        """
        DO $$
        DECLARE
            month_start TIMESTAMPTZ;
        BEGIN
            FOR month_start IN
                SELECT DISTINCT date_trunc('month', received_at)
                FROM message_inbox_sw007_backup
            LOOP
                PERFORM switchboard_message_inbox_ensure_partition(month_start);
            END LOOP;
        END;
        $$
        """
    )

    op.execute(
        """
        INSERT INTO message_inbox (
            id,
            received_at,
            request_context,
            raw_payload,
            normalized_text,
            decomposition_output,
            dispatch_outcomes,
            response_summary,
            lifecycle_state,
            schema_version,
            processing_metadata,
            final_state_at,
            trace_id,
            session_id,
            created_at,
            updated_at
        )
        SELECT
            id,
            received_at,
            jsonb_strip_nulls(
                jsonb_build_object(
                    'request_id', id::text,
                    'received_at', to_jsonb(received_at),
                    'source_channel', source_channel,
                    'source_endpoint_identity',
                    COALESCE(source_endpoint_identity, source_channel || '.legacy'),
                    'source_sender_identity', COALESCE(source_sender_identity, sender_id),
                    'source_thread_identity', source_thread_identity,
                    'idempotency_key', idempotency_key,
                    'dedupe_key', dedupe_key,
                    'dedupe_strategy', dedupe_strategy,
                    'trace_context', jsonb_strip_nulls(
                        jsonb_build_object(
                            'trace_id', trace_id
                        )
                    )
                )
            ),
            jsonb_build_object(
                'content', raw_content,
                'metadata', COALESCE(raw_metadata, '{}'::jsonb)
            ),
            raw_content,
            classification,
            routing_results,
            response_summary,
            CASE
                WHEN completed_at IS NULL THEN 'accepted'
                WHEN COALESCE(response_summary, '') ILIKE '%failed%' THEN 'failed'
                ELSE 'completed'
            END,
            'message_inbox.v2',
            jsonb_strip_nulls(
                jsonb_build_object(
                    'classified_at', to_jsonb(classified_at),
                    'classification_duration_ms', classification_duration_ms
                )
            ),
            completed_at,
            trace_id,
            session_id,
            received_at,
            COALESCE(completed_at, received_at)
        FROM message_inbox_sw007_backup
        """
    )

    # Enforce one-month default retention after data migration.
    op.execute("SELECT switchboard_message_inbox_drop_expired_partitions()")
    op.execute("DROP TABLE IF EXISTS message_inbox_sw007_backup")


def downgrade() -> None:
    # Rollback guidance: reconstruct sw_007 schema from sw_008 canonical records.
    op.execute("ALTER TABLE message_inbox RENAME TO message_inbox_sw008_backup")

    op.execute(
        """
        CREATE TABLE message_inbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_channel TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            raw_content TEXT NOT NULL,
            raw_metadata JSONB DEFAULT '{}',
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            classification JSONB,
            classified_at TIMESTAMPTZ,
            classification_duration_ms INTEGER,
            routing_results JSONB,
            response_summary TEXT,
            completed_at TIMESTAMPTZ,
            trace_id TEXT,
            session_id UUID,
            source_endpoint_identity TEXT NOT NULL,
            source_sender_identity TEXT NOT NULL,
            source_thread_identity TEXT,
            idempotency_key TEXT,
            dedupe_key TEXT,
            dedupe_strategy TEXT NOT NULL,
            dedupe_last_seen_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_message_inbox_source_channel_received_at
        ON message_inbox (source_channel, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_message_inbox_sender_id_received_at
        ON message_inbox (sender_id, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_message_inbox_dedupe_key
        ON message_inbox (dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_message_inbox_idempotency_key
        ON message_inbox (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )

    op.execute(
        """
        INSERT INTO message_inbox (
            id,
            source_channel,
            sender_id,
            raw_content,
            raw_metadata,
            received_at,
            classification,
            classified_at,
            classification_duration_ms,
            routing_results,
            response_summary,
            completed_at,
            trace_id,
            session_id,
            source_endpoint_identity,
            source_sender_identity,
            source_thread_identity,
            idempotency_key,
            dedupe_key,
            dedupe_strategy,
            dedupe_last_seen_at
        )
        SELECT
            id,
            COALESCE(request_context ->> 'source_channel', 'unknown'),
            COALESCE(
                request_context ->> 'source_sender_identity',
                request_context ->> 'source_thread_identity',
                'unknown'
            ),
            COALESCE(raw_payload ->> 'content', normalized_text),
            COALESCE(raw_payload -> 'metadata', '{}'::jsonb),
            received_at,
            decomposition_output,
            NULLIF(processing_metadata ->> 'classified_at', '')::TIMESTAMPTZ,
            NULLIF(processing_metadata ->> 'classification_duration_ms', '')::INTEGER,
            dispatch_outcomes,
            response_summary,
            final_state_at,
            trace_id,
            session_id,
            COALESCE(request_context ->> 'source_endpoint_identity', 'unknown'),
            COALESCE(request_context ->> 'source_sender_identity', 'unknown'),
            request_context ->> 'source_thread_identity',
            request_context ->> 'idempotency_key',
            request_context ->> 'dedupe_key',
            COALESCE(request_context ->> 'dedupe_strategy', 'legacy'),
            received_at
        FROM message_inbox_sw008_backup
        """
    )

    op.execute("DROP TABLE IF EXISTS message_inbox_sw008_backup CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS switchboard_message_inbox_drop_expired_partitions("
        "INTERVAL, TIMESTAMPTZ)"
    )
    op.execute("DROP FUNCTION IF EXISTS switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)")
