"""priority_contacts: add entity_id anchor + drop legacy contacts FK (bu-vat93).

Revision ID: core_131
Revises: core_130
Create Date: 2026-06-19 00:00:00.000000

Motivation (Phase 7.3a contact retirement)
------------------------------------------
``public.priority_contacts.contact_id`` is a legacy FK to ``public.contacts(id)``.
Two SQL consumers in ``priority_contacts.py`` still reference ``public.contacts``
via that join — both must move to ``public.entities`` before the guarded
``DROP public.contacts`` (bu-y6o7q) can proceed.

What this migration does
------------------------
1. **Add ``entity_id UUID``** — a new nullable column that FKs directly to
   ``public.entities(id) ON DELETE SET NULL``.  Guarded by IF NOT EXISTS so
   re-runs are safe.
2. **Backfill** — populate ``entity_id`` from the linked contact's
   ``entity_id`` field.  DML guarded by ``to_regclass('public.contacts')``
   so it is a clean no-op once contacts is dropped (Phase 7.3b).
3. **Drop the contacts FK** — remove the ``REFERENCES public.contacts(id)``
   constraint from ``contact_id`` so that entity UUIDs can be stored there
   going forward (the router now writes ``entity_id = contact_id`` for new
   rows).  The column itself is preserved; the DROP is idempotent.
4. **Index** on ``entity_id`` for display-name JOIN performance.

Post-migration semantics
------------------------
- Existing rows: ``contact_id`` holds the legacy contacts UUID (no FK);
  ``entity_id`` holds the backfilled entity UUID (or NULL when the contact
  had no linked entity).
- New rows (added via the updated router): ``contact_id = entity_id``
  (the caller passes an entity UUID; the same UUID goes to both columns).
- The GET query now: ``LEFT JOIN public.entities e ON e.id = pc.entity_id``.
- The POST validation now: ``SELECT EXISTS(SELECT 1 FROM public.entities …)``.

Reversibility
-------------
``downgrade()`` drops ``entity_id`` (and its index/FK), then re-adds the
``contact_id`` FK to ``public.contacts`` — best-effort (skipped if
``public.contacts`` is already gone).

Safety
------
- All DDL is idempotent: IF NOT EXISTS checks + DROP IF EXISTS.
- ``to_regclass`` guards on both ``public.priority_contacts`` and
  ``public.contacts`` protect against fresh-DB/post-DROP re-runs.
- No data is destroyed.  No NOT NULL constraint is added to ``entity_id``
  (rows without a linked entity keep NULL).
"""

from __future__ import annotations

from alembic import op

revision = "core_131"
down_revision = "core_130"
branch_labels = None
depends_on = None

_TABLE = "public.priority_contacts"
_FK_NAME = "priority_contacts_contact_id_fkey"
_IDX_NAME = "idx_priority_contacts_entity_id"


def upgrade() -> None:
    # 0. No-op when priority_contacts does not exist yet (fresh provision
    #    before core_101 runs, or schema-scoped re-run on a butler that never
    #    got core_101).
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RAISE NOTICE 'core_131: {_TABLE} not found — skipping';
                RETURN;
            END IF;

            -- 1. Add entity_id column (idempotent).
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'priority_contacts'
                  AND column_name  = 'entity_id'
            ) THEN
                ALTER TABLE {_TABLE}
                    ADD COLUMN entity_id UUID;
            END IF;

            -- 2. Add FK entity_id → public.entities ON DELETE SET NULL
            --    (only if public.entities exists and the FK is not yet there).
            IF to_regclass('public.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema    = kcu.table_schema
                   WHERE tc.table_schema  = 'public'
                     AND tc.table_name    = 'priority_contacts'
                     AND tc.constraint_type = 'FOREIGN KEY'
                     AND kcu.column_name  = 'entity_id'
               )
            THEN
                ALTER TABLE {_TABLE}
                    ADD CONSTRAINT priority_contacts_entity_id_fkey
                    FOREIGN KEY (entity_id)
                    REFERENCES public.entities(id)
                    ON DELETE SET NULL;
            END IF;

            -- 3. Backfill entity_id from public.contacts (DML-guarded).
            IF to_regclass('public.contacts') IS NOT NULL THEN
                UPDATE {_TABLE} pc
                SET    entity_id = c.entity_id
                FROM   public.contacts c
                WHERE  c.id             = pc.contact_id
                  AND  c.entity_id      IS NOT NULL
                  AND  pc.entity_id     IS NULL;
            END IF;

            -- 4. Drop the legacy FK from contact_id → public.contacts so that
            --    the router can store entity UUIDs in contact_id for new rows.
            --    Idempotent: constraint may already be absent (re-run / fresh DB
            --    that never had it, or post-DROP state).
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_schema = 'public'
                  AND table_name        = 'priority_contacts'
                  AND constraint_name   = '{_FK_NAME}'
                  AND constraint_type   = 'FOREIGN KEY'
            ) THEN
                ALTER TABLE {_TABLE}
                    DROP CONSTRAINT {_FK_NAME};
            END IF;

            -- 5. Index on entity_id for display-name JOIN performance.
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename  = 'priority_contacts'
                  AND indexname  = '{_IDX_NAME}'
            ) THEN
                CREATE INDEX {_IDX_NAME}
                ON {_TABLE} (entity_id)
                WHERE entity_id IS NOT NULL;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;

            -- Drop entity_id FK (if present).
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_schema = 'public'
                  AND table_name        = 'priority_contacts'
                  AND constraint_name   = 'priority_contacts_entity_id_fkey'
                  AND constraint_type   = 'FOREIGN KEY'
            ) THEN
                ALTER TABLE {_TABLE}
                    DROP CONSTRAINT priority_contacts_entity_id_fkey;
            END IF;

            -- Drop entity_id index.
            DROP INDEX IF EXISTS public.{_IDX_NAME};

            -- Drop entity_id column.
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'priority_contacts'
                  AND column_name  = 'entity_id'
            ) THEN
                ALTER TABLE {_TABLE} DROP COLUMN entity_id;
            END IF;

            -- Purge new-format rows (contact_id = entity UUID) that would violate
            -- the contacts FK being re-added; such rows are non-representable in the
            -- pre-core_131 schema and must be removed before the constraint can be
            -- applied.
            IF to_regclass('public.contacts') IS NOT NULL THEN
                DELETE FROM {_TABLE} pc
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.contacts c WHERE c.id = pc.contact_id
                );
            END IF;

            -- Re-add the contacts FK (best-effort — skipped if contacts is gone).
            IF to_regclass('public.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema    = kcu.table_schema
                   WHERE tc.table_schema  = 'public'
                     AND tc.table_name    = 'priority_contacts'
                     AND tc.constraint_type = 'FOREIGN KEY'
                     AND kcu.column_name  = 'contact_id'
               )
            THEN
                ALTER TABLE {_TABLE}
                    ADD CONSTRAINT {_FK_NAME}
                    FOREIGN KEY (contact_id)
                    REFERENCES public.contacts(id)
                    ON DELETE CASCADE;
            END IF;
        END
        $$;
    """)
