"""Dedup duplicate-entity contacts on the core public/connectors contact refs
(bu-vcfyg).

Revision ID: core_133
Revises: core_132
Create Date: 2026-06-20 00:00:00.000000

Context (Phase 7.3a-3b — DROP precondition for bu-y6o7q)
--------------------------------------------------------
The core-owned half of the ``public.contacts`` dedup. Sibling migrations:
``contacts_005`` (contacts module) handles ``public.contacts_source_links`` (the
last FK to ``public.contacts``); ``rel_030`` (relationship chain) handles the
eight relationship-schema FK constraints. Split by chain ownership because each
table is created by a different chain (``contacts_source_links`` does not exist
yet when the core chain runs).

This migration dedups the two **core-owned** contact references — both of whose
FKs to ``public.contacts`` were already dropped (core_131 / core_132), so there
is no FK to drop here, only the dedup:

- ``public.priority_contacts.contact_id``       (PK contact_id — collision-merge)
- ``connectors.home_assistant_persons.contact_id`` (PK ha_entity_id — plain repoint)

Dedup collision policy (owner-approved, bu-oluyt)
-------------------------------------------------
Where 2+ ``public.contacts`` rows share one ``entity_id``, dependent rows are
repointed onto the **canonical** contact = oldest by ``created_at`` (``id``
tie-break) — identical to rel_030 / contacts_005, so all three pick the SAME
canonical contact (``public.contacts`` is unchanged until the bu-y6o7q DROP).
``public.contacts`` has no ``roles`` column; owner-role is an entity property,
uniform across one entity's contact set, so ``created_at`` governs.

Safety / doctrine
-----------------
- ``to_regclass`` guards every table; the dedup no-ops if ``public.contacts`` (or
  a target table) is absent — including the fresh/schema-scoped core run before
  any runtime contact rows exist.
- Snapshot-before-mutate (``<table>_dedup_bak_core_133``) + parity raise.
- Idempotent: snapshots taken only when absent; repoint/deletes no-op on re-run.
- NO ``public.contacts`` row is deleted (bu-y6o7q drops the whole table).
- Reversible: ``downgrade`` restores from snapshots.
"""

from __future__ import annotations

from alembic import op

revision = "core_133"
down_revision = "core_132"
branch_labels = None
depends_on = None

_SNAPSHOT_AND_DEDUP_SQL = r"""
DO $$
DECLARE
    _src_refs  bigint := 0;
    _lost      bigint := 0;
    _n         bigint;
BEGIN
    IF to_regclass('public.contacts') IS NULL THEN
        RAISE NOTICE 'core_133: public.contacts absent — skipping dedup';
        RETURN;
    END IF;

    -- 1. Snapshot present tables (only if not already snapshotted).
    IF to_regclass('public.priority_contacts') IS NOT NULL
       AND to_regclass('public.priority_contacts_dedup_bak_core_133') IS NULL THEN
        CREATE TABLE public.priority_contacts_dedup_bak_core_133 AS
            SELECT * FROM public.priority_contacts;
    END IF;
    IF to_regclass('connectors.home_assistant_persons') IS NOT NULL
       AND to_regclass('connectors.home_assistant_persons_dedup_bak_core_133') IS NULL THEN
        CREATE TABLE connectors.home_assistant_persons_dedup_bak_core_133 AS
            SELECT * FROM connectors.home_assistant_persons;
    END IF;

    -- 2. source -> canonical map (oldest contact per entity_id).
    DROP TABLE IF EXISTS _core133_ddm;
    CREATE TEMP TABLE _core133_ddm ON COMMIT DROP AS
        SELECT id AS source_id, canonical_id
        FROM (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY entity_id ORDER BY created_at ASC, id ASC
                   ) AS canonical_id
            FROM public.contacts
            WHERE entity_id IS NOT NULL
        ) ranked
        WHERE id <> canonical_id;

    -- 3a. priority_contacts PK (contact_id): collision-delete then repoint.
    IF to_regclass('public.priority_contacts') IS NOT NULL THEN
        DELETE FROM public.priority_contacts pc
        USING _core133_ddm m
        WHERE pc.contact_id = m.source_id
          AND EXISTS (SELECT 1 FROM public.priority_contacts x WHERE x.contact_id = m.canonical_id);
        UPDATE public.priority_contacts pc
        SET contact_id = m.canonical_id
        FROM _core133_ddm m
        WHERE pc.contact_id = m.source_id;

        SELECT count(*) INTO _n FROM public.priority_contacts pc JOIN _core133_ddm m ON pc.contact_id = m.source_id;
        _src_refs := _src_refs + _n;
        SELECT count(*) INTO _n FROM public.priority_contacts_dedup_bak_core_133 s
            WHERE NOT EXISTS (
                SELECT 1 FROM public.priority_contacts pc
                WHERE pc.contact_id = COALESCE(
                    (SELECT canonical_id FROM _core133_ddm m WHERE m.source_id = s.contact_id), s.contact_id));
        _lost := _lost + _n;
    END IF;

    -- 3b. home_assistant_persons PK (ha_entity_id): plain repoint.
    IF to_regclass('connectors.home_assistant_persons') IS NOT NULL THEN
        UPDATE connectors.home_assistant_persons hap
        SET contact_id = m.canonical_id
        FROM _core133_ddm m
        WHERE hap.contact_id = m.source_id;

        SELECT count(*) INTO _n FROM connectors.home_assistant_persons hap JOIN _core133_ddm m ON hap.contact_id = m.source_id;
        _src_refs := _src_refs + _n;
        SELECT count(*) INTO _n FROM connectors.home_assistant_persons_dedup_bak_core_133 s
            WHERE NOT EXISTS (SELECT 1 FROM connectors.home_assistant_persons hap WHERE hap.ha_entity_id = s.ha_entity_id);
        _lost := _lost + _n;
    END IF;

    -- 4. Parity guards.
    IF _src_refs <> 0 THEN
        RAISE EXCEPTION 'core_133 parity: % core rows still reference a non-canonical contact', _src_refs;
    END IF;
    IF _lost <> 0 THEN
        RAISE EXCEPTION 'core_133 parity: % snapshot rows did not survive onto a canonical contact', _lost;
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_SNAPSHOT_AND_DEDUP_SQL)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.priority_contacts_dedup_bak_core_133') IS NOT NULL THEN
                DELETE FROM public.priority_contacts;
                INSERT INTO public.priority_contacts SELECT * FROM public.priority_contacts_dedup_bak_core_133;
                DROP TABLE public.priority_contacts_dedup_bak_core_133;
            END IF;
            IF to_regclass('connectors.home_assistant_persons_dedup_bak_core_133') IS NOT NULL THEN
                DELETE FROM connectors.home_assistant_persons;
                INSERT INTO connectors.home_assistant_persons SELECT * FROM connectors.home_assistant_persons_dedup_bak_core_133;
                DROP TABLE connectors.home_assistant_persons_dedup_bak_core_133;
            END IF;
        END
        $$;
        """
    )
