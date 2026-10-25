"""connectors: create connectors schema and filtered_events partitioned table

Revision ID: core_007
Revises: core_006
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_027_connectors_schema_filtered_events,
                core_028_filtered_events_ensure_partition_next_month,
                core_031_ensure_partition_to_connectors_schema

Creates the database foundation for the connectors subsystem:

  1. connectors schema — dedicated namespace for connector-owned persistent
     state, separate from switchboard schema.

  2. connectors.filtered_events — monthly-partitioned table that persists every
     message a connector observes but does not submit to the Switchboard. One
     row per filtered or errored message, with full payload for replay.
     Partition naming: filtered_events_YYYYMM.

  3. connectors.connectors_filtered_events_ensure_partition() — PL/pgSQL function
     (schema-qualified in connectors) that creates the monthly partition for any
     reference timestamp AND the next month's partition (proactive).  Idempotent,
     uses CREATE TABLE IF NOT EXISTS.  Also creates an unqualified backward-compat
     wrapper in the default search_path.

  4. Indexes:
     - (connector_type, endpoint_identity, status, received_at DESC) — drain
       queries and dashboard listing
     - (received_at DESC) — UNION timeline queries

  5. Serving access is installed by the locked source-owned final manifest;
     replay never widens schema CREATE, unrelated tables, or default privileges.

Downgrade drops the connectors schema entirely (CASCADE).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_007"
down_revision = "core_006"
branch_labels = None
depends_on = None


# All butler roles that need read access to connectors tables.


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create connectors schema
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS connectors")

    # -------------------------------------------------------------------------
    # 2. Create connectors.filtered_events (partitioned by received_at)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS connectors.filtered_events (
            id                  UUID NOT NULL DEFAULT gen_random_uuid(),
            received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            connector_type      TEXT NOT NULL,
            endpoint_identity   TEXT NOT NULL,
            external_message_id TEXT NOT NULL,
            source_channel      TEXT NOT NULL,
            sender_identity     TEXT NOT NULL,
            subject_or_preview  TEXT,
            filter_reason       TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'filtered',
            full_payload        JSONB NOT NULL,
            error_detail        TEXT,
            replay_requested_at TIMESTAMPTZ,
            replay_completed_at TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (received_at, id),
            CONSTRAINT chk_filtered_events_status CHECK (status IN (
                'filtered', 'error', 'replay_pending', 'replay_complete', 'replay_failed'
            ))
        ) PARTITION BY RANGE (received_at)
    """)

    # -------------------------------------------------------------------------
    # 3. Create indexes on the parent table
    #    (indexes propagate automatically to child partitions in PG 11+)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_filtered_events_drain
        ON connectors.filtered_events (connector_type, endpoint_identity, status, received_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_filtered_events_timeline
        ON connectors.filtered_events (received_at DESC)
    """)

    # -------------------------------------------------------------------------
    # 4. Schema-qualified partition management function (connectors schema).
    #    Proactively creates both the requested month AND the next month.
    # -------------------------------------------------------------------------
    op.execute("""
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
    """)

    # -------------------------------------------------------------------------
    # 5. Unqualified backward-compat wrapper (for butler-scoped search_path).
    # -------------------------------------------------------------------------
    op.execute("""
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
    """)

    # Create initial partitions for current month and next month.
    op.execute("SELECT connectors.connectors_filtered_events_ensure_partition(now())")

    # Serving privileges are finalized from the source-owned object manifest.
    # No schema CREATE, ALL TABLES, or future-object default ACL is replayed here.


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS connectors_filtered_events_ensure_partition(TIMESTAMPTZ)")
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " connectors.connectors_filtered_events_ensure_partition(TIMESTAMPTZ)"
    )
    op.execute("DROP SCHEMA IF EXISTS connectors CASCADE")
