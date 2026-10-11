"""Establish compatible structured logs in the configured owning schema.

Revision ID: core_266
Revises: core_265

Historical core_089/core_090 remain unchanged. A core-only invocation has no
owning target and deliberately creates no public substitute. Existing stores
are validated rather than adopted solely because their name exists. Downgrade
retains compatible rows and their relation, indexes and sequence.
"""

from __future__ import annotations

import re

from alembic import op

revision = "core_266"
down_revision = "core_265"
branch_labels = None
depends_on = None


def _target_schema() -> str | None:
    raw = op.get_context().config.get_main_option("butlers.target_schema")
    if raw is None or not raw.strip():
        return None
    target = raw.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target) is None:
        raise ValueError("Invalid structured-log owning migration schema")
    # Explicit public is the framework's core-only target, never a butler store.
    return None if target == "public" else target


def upgrade() -> None:
    target = _target_schema()
    if target is None:
        return
    quoted = '"' + target + '"'
    # Transaction-scoped per-target coordination covers the absent-table race;
    # unrelated schemas have separate lock keys. No role or ownership changes.
    op.execute(
        f"SELECT pg_advisory_xact_lock(hashtext('butlers.owned-log-storage'), hashtext('{target}'))"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quoted}.butler_logs (
            id         BIGSERIAL PRIMARY KEY,
            ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
            level      VARCHAR NOT NULL
                           CHECK (level IN ('DEBUG','INFO','WARN','ERROR')),
            msg        TEXT NOT NULL,
            source     VARCHAR,
            request_id UUID,
            metadata   JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        DO $owned_log_shape$
        DECLARE
            relation_oid oid := '{quoted}.butler_logs'::regclass;
            serial_name text;
            actual_columns jsonb;
            expected_columns jsonb := '[
                ["id","bigint",true], ["ts","timestamp with time zone",true],
                ["level","character varying",true], ["msg","text",true],
                ["source","character varying",false], ["request_id","uuid",false],
                ["metadata","jsonb",false], ["created_at","timestamp with time zone",true]
            ]'::jsonb;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = relation_oid
                           AND relkind = 'r' AND relpersistence = 'p'
                           AND NOT relrowsecurity AND NOT relforcerowsecurity) THEN
                RAISE EXCEPTION 'Incompatible owning structured-log relation';
            END IF;
            SELECT jsonb_agg(jsonb_build_array(attname, format_type(atttypid, atttypmod),
                                              attnotnull) ORDER BY attnum)
              INTO actual_columns
              FROM pg_attribute WHERE attrelid = relation_oid
                AND attnum > 0 AND NOT attisdropped;
            IF actual_columns IS DISTINCT FROM expected_columns THEN
                RAISE EXCEPTION 'Incompatible owning structured-log columns';
            END IF;
            IF (SELECT count(*) FROM pg_constraint WHERE conrelid = relation_oid) <> 2
               OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = relation_oid
                              AND contype = 'p' AND conkey = ARRAY[1]::smallint[])
               OR NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conrelid = relation_oid AND contype = 'c'
                      AND convalidated AND conkey = ARRAY[3]::smallint[]
                      AND regexp_replace(pg_get_expr(conbin, conrelid),
                                         '[[:space:]()]', '', 'g') =
                          'level::text=ANYARRAY[''DEBUG''::charactervarying,'
                          '''INFO''::charactervarying,''WARN''::charactervarying,'
                          '''ERROR''::charactervarying]::text[]'
               ) THEN
                RAISE EXCEPTION 'Incompatible owning structured-log constraints';
            END IF;
            serial_name := pg_get_serial_sequence('{quoted}.butler_logs', 'id');
            IF serial_name IS NULL OR NOT EXISTS (
                SELECT 1 FROM pg_class s JOIN pg_class t ON t.oid = relation_oid
                WHERE s.oid = serial_name::regclass AND s.relkind = 'S'
                  AND s.relowner = t.relowner AND s.relnamespace = t.relnamespace
            ) OR (SELECT count(*) FROM pg_attrdef WHERE adrelid = relation_oid) <> 3
               OR NOT EXISTS (
                   SELECT 1 FROM pg_attrdef WHERE adrelid = relation_oid AND adnum = 1
                     AND pg_get_expr(adbin, adrelid) = format('nextval(%L::regclass)', serial_name)
               ) OR (SELECT count(*) FROM pg_attrdef WHERE adrelid = relation_oid
                       AND adnum IN (2, 8) AND pg_get_expr(adbin, adrelid) = 'now()') <> 2 THEN
                RAISE EXCEPTION 'Incompatible owning structured-log defaults or sequence';
            END IF;
        END
        $owned_log_shape$;
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS butler_logs_ts ON {quoted}.butler_logs (ts DESC)")
    op.execute(f"CREATE INDEX IF NOT EXISTS butler_logs_level ON {quoted}.butler_logs (level)")
    op.execute(
        f"""
        DO $owned_log_indexes$
        BEGIN
            IF (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                  JOIN pg_am am ON am.oid = c.relam
                WHERE i.indrelid = '{quoted}.butler_logs'::regclass
                  AND am.amname = 'btree' AND i.indisvalid AND i.indisready
                  AND NOT i.indisunique AND i.indnkeyatts = 1 AND i.indnatts = 1
                  AND i.indexprs IS NULL AND i.indpred IS NULL
                  AND ((c.relname = 'butler_logs_ts' AND i.indkey[0] = 2 AND i.indoption[0] = 3)
                    OR (c.relname = 'butler_logs_level' AND i.indkey[0] = 3
                        AND i.indoption[0] = 0))) <> 2 THEN
                RAISE EXCEPTION 'Incompatible owning structured-log indexes';
            END IF;
        END
        $owned_log_indexes$;
        """
    )


def downgrade() -> None:
    """Keep all compatible storage/data for the previous logger and reader."""
    # A destructive removal is an independent administrative operation. This
    # forward repair cannot distinguish newly emitted rows from prior rows.
    return
