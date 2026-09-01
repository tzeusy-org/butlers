"""Serialize connector heartbeat partition creation.

Revision ID: sw_032
Revises: sw_031
Create Date: 2026-09-01 00:00:00.000000

``CREATE TABLE IF NOT EXISTS`` does not make concurrent DDL atomic. At a month
boundary, simultaneous first heartbeats can both observe a missing partition
and then race to create the same relation. Serialize this short maintenance
function with a transaction-scoped advisory lock so every caller observes any
partition committed by the preceding caller before issuing DDL.
"""

from __future__ import annotations

from alembic import op

revision = "sw_032"
down_revision = "sw_031"
branch_labels = None
depends_on = None


_ADVISORY_LOCK_SQL = """
            PERFORM pg_advisory_xact_lock(
                hashtext(current_schema()::text),
                hashtext('switchboard_connector_heartbeat_log_ensure_partition')
            );
"""


def _replace_ensure_partition(*, advisory_lock_sql: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION switchboard_connector_heartbeat_log_ensure_partition(
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
            -- One lock covers both the requested and proactively-created next
            -- partition, including overlapping calls for adjacent months.
{advisory_lock_sql}
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('connector_heartbeat_log_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

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

    # CREATE OR REPLACE resets unspecified function properties. Restore the
    # sw_008 privilege boundary and injection-safe search path in the same
    # migration transaction.
    op.execute(
        """
        DO $$
        DECLARE
            target_schema text := current_schema();
        BEGIN
            EXECUTE format(
                'ALTER FUNCTION %I.switchboard_connector_heartbeat_log_ensure_partition('
                'TIMESTAMPTZ) SECURITY DEFINER',
                target_schema
            );
            EXECUTE format(
                'ALTER FUNCTION %I.switchboard_connector_heartbeat_log_ensure_partition('
                'TIMESTAMPTZ) SET search_path TO %I, pg_temp',
                target_schema,
                target_schema
            );
        END
        $$;
        """
    )


def upgrade() -> None:
    _replace_ensure_partition(advisory_lock_sql=_ADVISORY_LOCK_SQL)


def downgrade() -> None:
    _replace_ensure_partition(advisory_lock_sql="")
