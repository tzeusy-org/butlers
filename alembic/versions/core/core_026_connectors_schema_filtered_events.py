"""connectors_schema: create connectors schema and filtered_events table

Revision ID: core_026
Revises: core_025
Create Date: 2026-03-11 00:00:00.000000

Creates the database foundation for the connectors subsystem:

  1. connectors schema — dedicated namespace for connector-owned persistent
     state, separate from switchboard schema.

  2. connectors.filtered_events — monthly-partitioned table that persists every
     message a connector observes but does not submit to the Switchboard. One
     row per filtered or errored message, with full payload for replay.
     Partition naming: filtered_events_YYYYMM.

  3. connectors_filtered_events_ensure_partition() — PL/pgSQL function that
     creates the monthly partition for any reference timestamp (idempotent,
     uses CREATE TABLE IF NOT EXISTS). Called for current + next month at
     migration time.

  4. Indexes:
     - (connector_type, endpoint_identity, status, received_at DESC) — drain
       queries and dashboard listing
     - (received_at DESC) — UNION timeline queries

  5. Grants:
     - connector_writer: USAGE + CREATE on connectors schema, SELECT on shared
       schema. Guarded — silently skips if the role does not exist (matches the
       best-effort pattern used throughout core migrations).

Downgrade drops the connectors schema entirely (CASCADE).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_026"
down_revision = "core_025"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CONNECTORS_SCHEMA = "connectors"
_SHARED_SCHEMA = "shared"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute SQL while tolerating privilege/role availability differences."""
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"

    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
            WHEN undefined_object THEN
                NULL;
        END
        $$;
        """
    )


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
    # 4. Partition management function
    # -------------------------------------------------------------------------
    op.execute("""
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
    """)

    # Create initial partitions for current month and next month.
    op.execute("SELECT connectors_filtered_events_ensure_partition(now())")
    op.execute("SELECT connectors_filtered_events_ensure_partition(now() + INTERVAL '1 month')")

    # -------------------------------------------------------------------------
    # 5. Grants for connector_writer role (best-effort; skipped if role absent)
    # -------------------------------------------------------------------------
    _execute_best_effort(
        f"GRANT USAGE, CREATE ON SCHEMA {_quote_ident(_CONNECTORS_SCHEMA)}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES"
        f" IN SCHEMA {_quote_ident(_CONNECTORS_SCHEMA)}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    # Ensure future partitions (created by ensure_partition()) also get DML grants.
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote_ident(_CONNECTORS_SCHEMA)}"
        f" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA {_quote_ident(_SHARED_SCHEMA)} TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT ON ALL TABLES"
        f" IN SCHEMA {_quote_ident(_SHARED_SCHEMA)}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    # Ensure future shared schema tables also get SELECT grant.
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote_ident(_SHARED_SCHEMA)}"
        f" GRANT SELECT ON TABLES TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS connectors_filtered_events_ensure_partition(TIMESTAMPTZ)")
    op.execute("DROP SCHEMA IF EXISTS connectors CASCADE")
