"""qa/self-healing: switch id PK defaults from UUIDv4 to UUIDv7.

Revision ID: core_148
Revises: core_147
Create Date: 2026-06-28 00:00:00.000000

``public.qa_findings.id`` and ``public.healing_attempts.id`` were created with a
``DEFAULT gen_random_uuid()`` (UUIDv4) even though their intent has always been
UUIDv7:

  * core_052's docstring labels ``qa_findings.id`` a "UUIDv7 PK".
  * core_051's docstring labels ``qa_patrols.id`` a "UUIDv7 PK
    (gen_random_uuid() as fallback before UUIDv7 extension)".
  * The sibling QA journal table ``qa_investigation_events`` already gets v7 ids
    via the application (``core.utils.generate_uuid7_string``).

The v4 default contradicted that documented intent and the journal convention.
These are append-heavy audit tables, so time-sortable v7 keys also give better
index locality than random v4 keys.

This migration installs a self-contained ``public.uuid_generate_v7()`` SQL
function (PostgreSQL 17 has no native ``uuidv7()`` — that arrives in PG 18) and
repoints both column defaults at it. The function mirrors the Python generator:
a 48-bit big-endian millisecond timestamp prefix, version nibble ``0111``, and
the RFC-4122 ``10`` variant bits (inherited from ``gen_random_uuid()``).

Existing rows keep their original v4 ids — only rows inserted after this
migration receive v7 ids. No backfill is performed (PKs are referenced by FKs
and must not be rewritten).
"""

from __future__ import annotations

from alembic import op

revision = "core_148"
down_revision = "core_147"
branch_labels = None
depends_on = None


_CREATE_UUID_V7_FN = """
CREATE OR REPLACE FUNCTION public.uuid_generate_v7()
RETURNS uuid
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    unix_ts_ms bytea;
    uuid_bytes bytea;
BEGIN
    -- 48-bit big-endian Unix timestamp in milliseconds (drop the 2 high zero
    -- bytes of the 8-byte int8send output).
    unix_ts_ms := substring(
        int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint)
        FROM 3
    );

    -- Start from a random v4 UUID; its byte 8 already carries the RFC-4122
    -- variant bits (10xx) that UUIDv7 also requires.
    uuid_bytes := uuid_send(gen_random_uuid());

    -- Overlay the 6 timestamp bytes into the leading positions.
    uuid_bytes := overlay(uuid_bytes PLACING unix_ts_ms FROM 1 FOR 6);

    -- Force the version nibble (high 4 bits of byte 6) to 0111 = 7, keep the
    -- low nibble random.
    uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);

    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_CREATE_UUID_V7_FN)

    op.execute(
        "ALTER TABLE IF EXISTS public.qa_findings "
        "ALTER COLUMN id SET DEFAULT public.uuid_generate_v7()"
    )
    op.execute(
        "ALTER TABLE IF EXISTS public.healing_attempts "
        "ALTER COLUMN id SET DEFAULT public.uuid_generate_v7()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS public.healing_attempts "
        "ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )
    op.execute(
        "ALTER TABLE IF EXISTS public.qa_findings ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )
    op.execute("DROP FUNCTION IF EXISTS public.uuid_generate_v7()")
