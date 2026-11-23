"""sw_086: the message_inbox and connector_heartbeat_log partition-drop functions refuse.

Revision ID: sw_086
Revises: sw_085
Create Date: 2026-11-23 00:00:00.000000

bu-uhbhzq.4.  For several sources ``message_inbox`` holds the only verbatim copy of what
arrived, and the heartbeat log is evidence of source liveness. Both drop functions ship with a
short default retention (1 month and 7 days), and one call would destroy the data. Nothing
calls them today. Until a per-source retention contract exists (bu-uhbhzq.7) they refuse
unconditionally, with or without arguments.

Signatures are unchanged (Postgres cannot drop argument defaults through ``CREATE OR
REPLACE``), and the definer attributes and the pinned ``pg_catalog, pg_temp`` search path from
sw_039 are re-specified, so grants and the sw_039 guarantees hold. The downgrade restores the
sw_039 pinned bodies.
"""

from alembic import op

revision = "sw_086"
down_revision = "sw_085"
branch_labels = None
depends_on = None

_PINNED = "pg_catalog, pg_temp"

_REFUSAL_BODY = """
BEGIN
    RAISE EXCEPTION
        '% is disabled: no retention contract exists for %; see bu-uhbhzq.7',
        '__NAME__', '__PARENT__'
        USING ERRCODE = '55000';
END;
"""

# The sw_039 pinned body, restored on downgrade.
_DROP_EXPIRED_BODY = """
DECLARE
    c_schema CONSTANT text := __SCHEMA_LITERAL__;
    partition_name TEXT;
    partition_month DATE;
    cutoff_month DATE;
    dropped_count INTEGER := 0;
BEGIN
    cutoff_month := date_trunc('month', reference_ts - retention)::date;

    FOR partition_name IN
        SELECT child.relname
        FROM pg_catalog.pg_inherits
        JOIN pg_catalog.pg_class parent ON parent.oid = pg_inherits.inhparent
        JOIN pg_catalog.pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_catalog.pg_class child ON child.oid = pg_inherits.inhrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = child.relnamespace
        WHERE parent.relname = '__PARENT__'
        AND parent_ns.nspname = c_schema
        AND ns.nspname = c_schema
        AND child.relname ~ '^__PARENT___p[0-9]{6}$'
    LOOP
        partition_month := to_date(substring(partition_name from '[0-9]{6}$'), 'YYYYMM');
        IF partition_month < cutoff_month THEN
            EXECUTE format('DROP TABLE IF EXISTS %I.%I', c_schema, partition_name);
            dropped_count := dropped_count + 1;
        END IF;
    END LOOP;

    RETURN dropped_count;
END;
"""

_TARGETS = (("message_inbox", "1 month"), ("connector_heartbeat_log", "7 days"))


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _schema() -> str:
    return str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())


def _replace(schema: str, parent: str, retention: str, body: str) -> None:
    name = f"switchboard_{parent}_drop_expired_partitions"
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_quote_ident(schema)}.{name}(
            retention INTERVAL DEFAULT INTERVAL '{retention}',
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS INTEGER
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = {_PINNED}
        AS $fn${body}$fn$
        """
    )


def upgrade() -> None:
    schema = _schema()
    for parent, retention in _TARGETS:
        name = f"switchboard_{parent}_drop_expired_partitions"
        body = _REFUSAL_BODY.replace("__NAME__", name).replace("__PARENT__", parent)
        _replace(schema, parent, retention, body)


def downgrade() -> None:
    schema = _schema()
    for parent, retention in _TARGETS:
        body = _DROP_EXPIRED_BODY.replace("__SCHEMA_LITERAL__", _quote_literal(schema)).replace(
            "__PARENT__", parent
        )
        _replace(schema, parent, retention, body)
