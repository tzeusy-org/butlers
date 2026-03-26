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

  5. Grants:
     - connector_writer: USAGE + CREATE on connectors schema, DML on all tables,
       default privileges for future partitions, SELECT on public schema.
     - All butler roles: USAGE on connectors schema, SELECT on connectors tables.

Downgrade drops the connectors schema entirely (CASCADE).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_007"
down_revision = "core_006"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CONNECTORS_SCHEMA = "connectors"
_PUBLIC_SCHEMA = "public"

# All butler roles that need read access to connectors tables.
_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)


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
    op.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition(now())"
    )

    # -------------------------------------------------------------------------
    # 6. Grants for connector_writer role (best-effort; skipped if role absent)
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
        f"GRANT USAGE ON SCHEMA {_quote_ident(_PUBLIC_SCHEMA)} TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT ON ALL TABLES"
        f" IN SCHEMA {_quote_ident(_PUBLIC_SCHEMA)}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    # Ensure future public schema tables also get SELECT grant.
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote_ident(_PUBLIC_SCHEMA)}"
        f" GRANT SELECT ON TABLES TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )

    # -------------------------------------------------------------------------
    # 7. Grants for butler roles (read access to connectors schema)
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _execute_best_effort(
            f"GRANT USAGE ON SCHEMA {_quote_ident(_CONNECTORS_SCHEMA)}"
            f" TO {_quote_ident(role)}",
            role_name=role,
        )
        _execute_best_effort(
            f"GRANT SELECT ON ALL TABLES"
            f" IN SCHEMA {_quote_ident(_CONNECTORS_SCHEMA)}"
            f" TO {_quote_ident(role)}",
            role_name=role,
        )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " connectors_filtered_events_ensure_partition(TIMESTAMPTZ)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " connectors.connectors_filtered_events_ensure_partition(TIMESTAMPTZ)"
    )
    op.execute("DROP SCHEMA IF EXISTS connectors CASCADE")
