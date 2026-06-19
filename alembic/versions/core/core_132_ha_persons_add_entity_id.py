"""home_assistant_persons: add entity_id anchor, drop public.contacts FK (bu-e9xbw).

Revision ID: core_132
Revises: core_131
Create Date: 2026-06-19 00:00:00.000000

Motivation (Phase 7 contact retirement — bu-e9xbw)
----------------------------------------------------
``connectors.home_assistant_persons.contact_id`` is a legacy FK to
``public.contacts(id)``.  The chronicler adapter (home_assistant.py) resolves
contact_id → entity_id by joining through ``public.contacts``.  That join must
be removed before the gated ``DROP public.contacts`` (bu-y6o7q) can proceed.

What this migration does
------------------------
1. **Add ``entity_id UUID``** — a new nullable column that FKs directly to
   ``public.entities(id) ON DELETE SET NULL``.  Guarded by IF NOT EXISTS so
   re-runs are safe.
2. **Backfill** — populate ``entity_id`` from the linked contact's
   ``entity_id`` field.  DML guarded by ``to_regclass('public.contacts')``
   so it is a clean no-op once contacts is dropped (Phase 7 cleanup).
3. **Drop the contacts FK** — remove the ``REFERENCES public.contacts(id)``
   constraint from ``contact_id`` so the column is decoupled from the
   public.contacts table.  The column itself is preserved.
4. **Index** on ``entity_id`` for fast adapter lookup.

Post-migration query path
--------------------------
The adapter now selects ``hap.entity_id`` directly instead of joining through
``public.contacts``:

    SELECT hap.ha_entity_id, hap.entity_id
    FROM connectors.home_assistant_persons AS hap
    WHERE hap.ha_entity_id = ANY($1)
      AND hap.entity_id IS NOT NULL

Unmapped persons (entity_id IS NULL) continue to degrade gracefully to
``entity_id = NULL`` on the projected episode — same observable behaviour as
before.

Safety
------
- All DDL is idempotent: IF NOT EXISTS checks + DROP IF EXISTS.
- ``to_regclass`` guards protect against fresh-DB or post-DROP re-runs.
- No data is destroyed.  No NOT NULL constraint is added to ``entity_id``
  (rows without a linked entity keep NULL).
"""

from __future__ import annotations

from alembic import op

revision = "core_132"
down_revision = "core_131"
branch_labels = None
depends_on = None

_TABLE = "connectors.home_assistant_persons"
_FK_CONTACTS = "home_assistant_persons_contact_id_fkey"
_IDX_ENTITY = "ix_ha_persons_entity_id"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            -- 0. No-op when the table does not exist.
            IF to_regclass('{_TABLE}') IS NULL THEN
                RAISE NOTICE 'core_132: {_TABLE} not found — skipping';
                RETURN;
            END IF;

            -- 1. Add entity_id column (idempotent).
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'connectors'
                  AND table_name   = 'home_assistant_persons'
                  AND column_name  = 'entity_id'
            ) THEN
                ALTER TABLE {_TABLE}
                    ADD COLUMN entity_id UUID;
            END IF;

            -- 2. Add FK entity_id → public.entities ON DELETE SET NULL
            --    (only when public.entities exists and the FK is not yet there).
            IF to_regclass('public.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema    = kcu.table_schema
                   WHERE tc.table_schema   = 'connectors'
                     AND tc.table_name     = 'home_assistant_persons'
                     AND tc.constraint_type = 'FOREIGN KEY'
                     AND kcu.column_name   = 'entity_id'
               )
            THEN
                ALTER TABLE {_TABLE}
                    ADD CONSTRAINT home_assistant_persons_entity_id_fkey
                    FOREIGN KEY (entity_id)
                    REFERENCES public.entities(id)
                    ON DELETE SET NULL;
            END IF;

            -- 3. Backfill entity_id from public.contacts (DML-guarded — safe no-op
            --    once contacts is dropped).
            IF to_regclass('public.contacts') IS NOT NULL THEN
                UPDATE {_TABLE} hap
                SET    entity_id = c.entity_id
                FROM   public.contacts c
                WHERE  c.id           = hap.contact_id
                  AND  c.entity_id    IS NOT NULL
                  AND  hap.entity_id  IS NULL;
            END IF;

            -- 4. Drop the legacy FK from contact_id → public.contacts so the column
            --    is decoupled from the contacts table.  Idempotent.
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_schema = 'connectors'
                  AND table_name        = 'home_assistant_persons'
                  AND constraint_name   = '{_FK_CONTACTS}'
                  AND constraint_type   = 'FOREIGN KEY'
            ) THEN
                ALTER TABLE {_TABLE}
                    DROP CONSTRAINT {_FK_CONTACTS};
            END IF;

            -- 5. Index on entity_id for fast adapter batch-lookup.
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'connectors'
                  AND tablename  = 'home_assistant_persons'
                  AND indexname  = '{_IDX_ENTITY}'
            ) THEN
                CREATE INDEX {_IDX_ENTITY}
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
                WHERE constraint_schema = 'connectors'
                  AND table_name        = 'home_assistant_persons'
                  AND constraint_name   = 'home_assistant_persons_entity_id_fkey'
                  AND constraint_type   = 'FOREIGN KEY'
            ) THEN
                ALTER TABLE {_TABLE}
                    DROP CONSTRAINT home_assistant_persons_entity_id_fkey;
            END IF;

            -- Drop entity_id index.
            DROP INDEX IF EXISTS connectors.{_IDX_ENTITY};

            -- Drop entity_id column.
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'connectors'
                  AND table_name   = 'home_assistant_persons'
                  AND column_name  = 'entity_id'
            ) THEN
                ALTER TABLE {_TABLE} DROP COLUMN entity_id;
            END IF;

            -- Re-add the contacts FK (best-effort — skipped if contacts is gone;
            -- exception-caught if orphaned contact_id rows would violate the constraint).
            BEGIN
                IF to_regclass('public.contacts') IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM information_schema.table_constraints
                       WHERE constraint_schema = 'connectors'
                         AND table_name        = 'home_assistant_persons'
                         AND constraint_name   = '{_FK_CONTACTS}'
                         AND constraint_type   = 'FOREIGN KEY'
                   )
                THEN
                    ALTER TABLE {_TABLE}
                        ADD CONSTRAINT {_FK_CONTACTS}
                        FOREIGN KEY (contact_id)
                        REFERENCES public.contacts(id)
                        ON DELETE CASCADE;
                END IF;
            EXCEPTION WHEN others THEN
                RAISE NOTICE 'core_132 downgrade: contacts FK re-add skipped (best-effort) — %',
                    SQLERRM;
            END;
        END
        $$;
    """)
