"""uuid_generate_v7: create shared PL/pgSQL function and sweep gen_random_uuid() defaults

Revision ID: core_019
Revises: core_018
Create Date: 2026-03-06 00:00:00.000000

Creates a pure-SQL UUIDv7 generator ``shared.uuid_generate_v7()`` (no pg_uuidv7
extension required) then sweeps all column defaults across all butler schemas,
replacing ``gen_random_uuid()`` with ``shared.uuid_generate_v7()``.

UUIDv7 benefits over v4:
- Time-ordered: B-tree index insertions are sequential, reducing page splits.
- Timestamp embedded: useful for debugging and log correlation.
- Globally unique: still 128 bits, same collision guarantees.

The function builds a valid UUIDv7 string by:
  1. Taking a 48-bit Unix ms timestamp.
  2. Generating 10 random bytes via gen_random_bytes().
  3. Setting version nibble to 7 and variant bits to 10.

The sweep targets every column whose pg_get_expr() contains gen_random_uuid(),
across all non-system schemas.

Downgrade removes the function; column defaults are NOT restored on downgrade
(restoring all defaults would require knowing the original expression, and
reverting a non-breaking optimisation is not worth the complexity).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_019"
down_revision = "core_018"
branch_labels = None
depends_on = None

# Pure-SQL UUIDv7 generator using gen_random_bytes (available via pgcrypto/pg_crypto or
# the built-in random functions in PostgreSQL 13+).
#
# UUID v7 bit layout (128 bits):
#   [0..47]   48-bit Unix ms timestamp
#   [48..51]  version = 7 (4 bits)
#   [52..63]  rand_a (12 bits)
#   [64..65]  variant = 10b (2 bits)
#   [66..127] rand_b (62 bits)
#
# Implementation strategy (avoids BIGINT overflow in bit shifts):
#   - Build 12-char ts_hex from timestamp.
#   - Generate 10 random bytes; encode as 20-char hex string.
#   - Override byte 2 of random data to set variant bits (10xxxxxx).
#   - Assemble UUID string directly from hex substrings.
_CREATE_FUNCTION_SQL = """\
CREATE OR REPLACE FUNCTION shared.uuid_generate_v7()
RETURNS UUID
LANGUAGE plpgsql
AS $func$
DECLARE
    ts_ms    BIGINT;
    ts_hex   TEXT;
    rnd      BYTEA;
    rnd_hex  TEXT;
    var_byte TEXT;
BEGIN
    -- 48-bit Unix timestamp in milliseconds
    ts_ms   := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT;
    ts_hex  := LPAD(TO_HEX(ts_ms & 281474976710655::BIGINT), 12, '0');

    -- 10 random bytes (80 bits) for version + rand_a + rand_b
    rnd     := gen_random_bytes(10);
    rnd_hex := encode(rnd, 'hex');  -- 20 hex chars

    -- Byte 2 (chars 5-6 in rnd_hex): set top 2 bits to 10 (variant)
    var_byte := LPAD(TO_HEX((get_byte(rnd, 2) & 63) | 128), 2, '0');

    RETURN (
        substring(ts_hex, 1, 8) || '-' ||                   -- time_high  (32 bits)
        substring(ts_hex, 9, 4) || '-' ||                   -- time_mid   (16 bits)
        '7' || substring(rnd_hex, 1, 3) || '-' ||           -- ver=7 + rand_a (12 bits)
        var_byte || substring(rnd_hex, 7, 2) || '-' ||      -- variant byte + rand_b byte
        substring(rnd_hex, 9, 12)                            -- rand_b low  (48 bits)
    )::UUID;
END;
$func$;
"""

_DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS shared.uuid_generate_v7();"

# Sweep: replace gen_random_uuid() defaults with shared.uuid_generate_v7() across all
# non-system schemas.  Skips views (relkind != 'r') and system schemas.
_SWEEP_SQL = """\
DO $$
DECLARE
    r   RECORD;
    sql TEXT;
BEGIN
    FOR r IN
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            a.attname AS column_name
        FROM pg_attrdef   d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        JOIN pg_class     c ON c.oid = d.adrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE pg_get_expr(d.adbin, d.adrelid) LIKE '%gen_random_uuid%'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND c.relkind = 'r'
    LOOP
        sql := format(
            'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT shared.uuid_generate_v7()',
            r.schema_name, r.table_name, r.column_name
        );
        EXECUTE sql;
    END LOOP;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_CREATE_FUNCTION_SQL)
    op.execute(_SWEEP_SQL)


def downgrade() -> None:
    op.execute(_DROP_FUNCTION_SQL)
