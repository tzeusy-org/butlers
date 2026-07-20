"""Permit entity-only Home Assistant person mappings after contacts removal.

Revision ID: core_179
Revises: core_178
Create Date: 2026-07-20 00:00:00.000000

``core_132`` introduced ``entity_id`` as the direct Home Assistant person
mapping and removed the ``contact_id`` foreign key.  ``core_134`` subsequently
removed ``public.contacts``, but its legacy ``contact_id`` column remained
``NOT NULL``.  That contradicts the direct mapping contract by rejecting an
otherwise valid ``(ha_entity_id, entity_id)`` row.

This forward-only repair changes no mapping data.  Existing legacy contact IDs
remain intact; it only permits the canonical entity-only representation once
the contacts table is gone and ``entity_id`` has a verified direct foreign key
to ``public.entities(id)``.
"""

from __future__ import annotations

from alembic import op

revision = "core_179"
down_revision = "core_178"
branch_labels = None
depends_on = None


_MAKE_CONTACT_ID_NULLABLE_SQL = """
DO $$
BEGIN
    IF to_regclass('connectors.home_assistant_persons') IS NULL THEN
        RAISE NOTICE 'core_179: connectors.home_assistant_persons absent — skipping';
        RETURN;
    END IF;

    -- This is the post-core_134 repair only.  If a caller has intentionally
    -- restored public.contacts, preserve the pre-cutover shape instead.
    IF to_regclass('public.contacts') IS NOT NULL THEN
        RAISE NOTICE 'core_179: public.contacts present — preserving legacy contact requirement';
        RETURN;
    END IF;

    -- A malformed or pre-core_132 table cannot safely claim entity-only
    -- support, so leave it untouched rather than changing its old contract.
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'connectors'
          AND table_name = 'home_assistant_persons'
          AND column_name = 'entity_id'
    ) THEN
        RAISE NOTICE 'core_179: entity_id absent — skipping legacy constraint repair';
        RETURN;
    END IF;

    -- A column alone is not an entity anchor.  Relax the legacy requirement
    -- only after the direct, validated entity FK proves that a new mapping
    -- cannot accept an arbitrary UUID.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS fk
        JOIN pg_class AS source_table ON source_table.oid = fk.conrelid
        JOIN pg_namespace AS source_schema ON source_schema.oid = source_table.relnamespace
        JOIN pg_class AS target_table ON target_table.oid = fk.confrelid
        JOIN pg_namespace AS target_schema ON target_schema.oid = target_table.relnamespace
        JOIN pg_attribute AS source_column
          ON source_column.attrelid = fk.conrelid
         AND source_column.attnum = fk.conkey[1]
        JOIN pg_attribute AS target_column
          ON target_column.attrelid = fk.confrelid
         AND target_column.attnum = fk.confkey[1]
        WHERE fk.contype = 'f'
          AND fk.convalidated
          AND cardinality(fk.conkey) = 1
          AND cardinality(fk.confkey) = 1
          AND source_schema.nspname = 'connectors'
          AND source_table.relname = 'home_assistant_persons'
          AND source_column.attname = 'entity_id'
          AND target_schema.nspname = 'public'
          AND target_table.relname = 'entities'
          AND target_column.attname = 'id'
    ) THEN
        RAISE NOTICE '%',
            'core_179: entity_id lacks a validated FK to public.entities(id) — '
            || 'skipping legacy constraint repair';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'connectors'
          AND table_name = 'home_assistant_persons'
          AND column_name = 'contact_id'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE connectors.home_assistant_persons
            ALTER COLUMN contact_id DROP NOT NULL;
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Allow direct entity mappings without rewriting legacy mapping rows."""
    op.execute(_MAKE_CONTACT_ID_NULLABLE_SQL)


def downgrade() -> None:
    """Keep nullable legacy data valid; re-adding NOT NULL could lose mappings."""
