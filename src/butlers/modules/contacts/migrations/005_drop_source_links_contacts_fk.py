"""contacts_005 — dedup + drop contacts_source_links → public.contacts FK (bu-vcfyg).

Revision ID: contacts_005
Revises: contacts_004
Create Date: 2026-06-20

Context (Phase 7.3a-3b — DROP precondition for bu-y6o7q)
--------------------------------------------------------
``public.contacts_source_links.local_contact_id`` is the **last** FK referencing
``public.contacts(id)`` (``ON DELETE SET NULL``) — the contacts module owns this
table (contacts_001/003).  It must be dropped before ``public.contacts`` can be
dropped (bu-y6o7q).  Siblings: ``core_133`` (priority_contacts + ha_persons dedup)
and ``rel_030`` (the eight relationship-schema FK constraints).

What this migration does
------------------------
1. **Dedup** — where 2+ ``public.contacts`` rows share one ``entity_id``, repoint
   ``local_contact_id`` from the superseded duplicates onto the **canonical**
   contact (oldest by ``created_at``, ``id`` tie-break — identical rule to
   core_133 / rel_030, so all three pick the same canonical contact).
2. **Drop the FK** ``contacts_source_links_local_contact_id_fkey``.  The
   ``local_contact_id`` *column* is kept — ``_entity_resolve`` still reads it to
   find ``local_entity_id`` — only the constraint to ``public.contacts`` is removed.

Safety / doctrine (cross-chain-migration-drop-hazard)
-----------------------------------------------------
- ``to_regclass`` guards ``public.contacts`` and ``public.contacts_source_links``;
  the dedup no-ops if either is absent.
- Snapshot-before-mutate (``contacts_source_links_dedup_bak_contacts_005``) +
  parity raise.
- Idempotent: snapshot taken only when absent; repoint no-op on re-run; FK drop
  is ``IF EXISTS``.  Safe to run once per butler that enables the contacts module.
- NO ``public.contacts`` row is deleted (bu-y6o7q drops the whole table).
- Reversible: ``downgrade`` restores from the snapshot and re-adds the FK.
"""

from __future__ import annotations

from alembic import op

revision = "contacts_005"
down_revision = "contacts_004"
branch_labels = None
depends_on = None

_FK = "contacts_source_links_local_contact_id_fkey"

_SNAPSHOT_AND_DEDUP_SQL = r"""
DO $$
DECLARE
    _src_refs bigint := 0;
    _lost     bigint := 0;
    _n        bigint;
BEGIN
    IF to_regclass('public.contacts') IS NULL
       OR to_regclass('public.contacts_source_links') IS NULL THEN
        RAISE NOTICE 'contacts_005: contacts/source_links absent — skipping dedup';
        RETURN;
    END IF;

    -- 1. Snapshot (only if not already snapshotted).
    IF to_regclass('public.contacts_source_links_dedup_bak_contacts_005') IS NULL THEN
        CREATE TABLE public.contacts_source_links_dedup_bak_contacts_005 AS
            SELECT * FROM public.contacts_source_links;
    END IF;

    -- 2. source -> canonical map (oldest contact per entity_id).
    DROP TABLE IF EXISTS _contacts005_ddm;
    CREATE TEMP TABLE _contacts005_ddm ON COMMIT DROP AS
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

    -- 3. Repoint local_contact_id (nullable; PK is provider/account/external —
    --    no collision possible).
    UPDATE public.contacts_source_links sl
    SET local_contact_id = m.canonical_id
    FROM _contacts005_ddm m
    WHERE sl.local_contact_id = m.source_id;

    -- 4. Parity guards.
    SELECT count(*) INTO _src_refs
    FROM public.contacts_source_links sl
    JOIN _contacts005_ddm m ON sl.local_contact_id = m.source_id;
    IF _src_refs <> 0 THEN
        RAISE EXCEPTION 'contacts_005 parity: % links still reference a non-canonical contact',
            _src_refs;
    END IF;

    SELECT count(*) INTO _lost
    FROM public.contacts_source_links_dedup_bak_contacts_005 s
    WHERE s.local_contact_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM public.contacts_source_links sl
          WHERE sl.provider = s.provider
            AND sl.account_id = s.account_id
            AND sl.external_contact_id = s.external_contact_id
            AND sl.local_contact_id = COALESCE(
                (SELECT canonical_id FROM _contacts005_ddm m
                 WHERE m.source_id = s.local_contact_id),
                s.local_contact_id));
    IF _lost <> 0 THEN
        RAISE EXCEPTION 'contacts_005 parity: % snapshot source links did not survive', _lost;
    END IF;
END
$$;
"""

_DROP_FK_SQL = f"""
DO $$
BEGIN
    IF to_regclass('public.contacts_source_links') IS NOT NULL THEN
        ALTER TABLE public.contacts_source_links DROP CONSTRAINT IF EXISTS {_FK};
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_SNAPSHOT_AND_DEDUP_SQL)
    op.execute(_DROP_FK_SQL)


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.contacts_source_links_dedup_bak_contacts_005') IS NOT NULL THEN
                DELETE FROM public.contacts_source_links;
                INSERT INTO public.contacts_source_links
                    SELECT * FROM public.contacts_source_links_dedup_bak_contacts_005;
                DROP TABLE public.contacts_source_links_dedup_bak_contacts_005;
            END IF;

            IF to_regclass('public.contacts') IS NOT NULL
               AND to_regclass('public.contacts_source_links') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.table_constraints
                   WHERE table_name = 'contacts_source_links'
                     AND constraint_name = '{_FK}'
                     AND constraint_type = 'FOREIGN KEY'
               ) THEN
                ALTER TABLE public.contacts_source_links
                    ADD CONSTRAINT {_FK}
                    FOREIGN KEY (local_contact_id)
                    REFERENCES public.contacts(id) ON DELETE SET NULL;
            END IF;
        END
        $$;
        """
    )
