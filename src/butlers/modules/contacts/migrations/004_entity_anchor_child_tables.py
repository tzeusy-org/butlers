"""contacts_004 — add local_entity_id anchor to addresses/important_dates/contact_labels

Revision ID: contacts_004
Revises: contacts_003
Create Date: 2026-06-19

Background
----------
ContactBackfillWriter.upsert_addresses, upsert_important_dates, and upsert_labels
were left as no-ops in bu-tzyuh because:

    addresses.contact_id       FK → public.contacts(id)  NOT NULL
    important_dates.contact_id FK → public.contacts(id)  NOT NULL
    contact_labels.contact_id  FK → public.contacts(id)  NOT NULL (in PK)

After bu-tzyuh, ``local_id`` in the backfill is a public.entities UUID, so direct
writes into these columns violate the FK.

What this migration does
------------------------
For each of addresses, important_dates, contact_labels (in the current butler
schema — guarded via to_regclass so it is safe when the table doesn't exist):

1. Add ``local_entity_id UUID`` column (nullable, idempotent IF NOT EXISTS check).
2. Make ``contact_id`` nullable:
   - addresses / important_dates: straightforward ALTER COLUMN DROP NOT NULL.
   - contact_labels: contact_id is part of the PRIMARY KEY, so the migration
     adds a surrogate ``id UUID`` column, promotes it to PRIMARY KEY, then drops
     NOT NULL from contact_id.  Uniqueness of the old (label_id, contact_id) pair
     is preserved via a partial unique index.
3. Add FK ``REFERENCES public.entities(id) ON DELETE SET NULL`` on local_entity_id
   (guarded: only when public.entities exists).
4. Backfill ``local_entity_id`` from existing rows via public.contacts.entity_id
   (guarded: only when both public.contacts and public.entities exist).
5. Add index on local_entity_id.
6. contact_labels: add partial unique index ``(label_id, local_entity_id)
   WHERE local_entity_id IS NOT NULL`` so the backfill upsert path can use
   INSERT … WHERE NOT EXISTS safely.

Safety properties
-----------------
- All DDL is idempotent: guarded by IF NOT EXISTS checks or to_regclass probes.
- Cross-chain guard: both public.entities and public.contacts are probed via
  to_regclass before any DML or FK creation (cross-chain-migration-drop-hazard).
- Non-destructive: public.contacts and existing contact_id columns are untouched.
- Module-scoped: migrations apply per butler schema; tables in other schemas are
  unaffected.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "contacts_004"
down_revision = "contacts_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # addresses                                                            #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.addresses', current_schema())) IS NOT NULL THEN

                -- 1. Add local_entity_id column (idempotent).
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'addresses'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE addresses ADD COLUMN local_entity_id UUID;
                END IF;

                -- 2. Make contact_id nullable so backfill rows can omit it.
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'addresses'
                      AND column_name  = 'contact_id'
                      AND is_nullable  = 'NO'
                ) THEN
                    ALTER TABLE addresses ALTER COLUMN contact_id DROP NOT NULL;
                END IF;

                -- 3. Add FK to public.entities when the table exists (cross-chain guard).
                IF to_regclass('public.entities') IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname    = 'addresses_local_entity_id_fkey'
                         AND conrelid   = to_regclass(
                                 format('%I.addresses', current_schema())
                             )
                   )
                THEN
                    ALTER TABLE addresses
                        ADD CONSTRAINT addresses_local_entity_id_fkey
                        FOREIGN KEY (local_entity_id)
                        REFERENCES public.entities(id) ON DELETE SET NULL;
                END IF;

                -- 4. Backfill entity_id from existing rows via public.contacts.
                IF to_regclass('public.contacts') IS NOT NULL
                   AND to_regclass('public.entities') IS NOT NULL
                THEN
                    UPDATE addresses a
                    SET local_entity_id = c.entity_id
                    FROM public.contacts c
                    WHERE a.contact_id       = c.id
                      AND c.entity_id        IS NOT NULL
                      AND a.local_entity_id  IS NULL;
                END IF;

            END IF;
        END
        $$
    """)

    # Index on addresses.local_entity_id (separate block for idempotency).
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.addresses', current_schema())) IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name   = 'addresses'
                     AND column_name  = 'local_entity_id'
               )
            THEN
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_addresses_local_entity_id
                         ON %I.addresses (local_entity_id)',
                    current_schema()
                );
            END IF;
        END
        $$
    """)

    # ------------------------------------------------------------------ #
    # important_dates                                                      #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.important_dates', current_schema())) IS NOT NULL THEN

                -- 1. Add local_entity_id column (idempotent).
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'important_dates'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE important_dates ADD COLUMN local_entity_id UUID;
                END IF;

                -- 2. Make contact_id nullable.
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'important_dates'
                      AND column_name  = 'contact_id'
                      AND is_nullable  = 'NO'
                ) THEN
                    ALTER TABLE important_dates ALTER COLUMN contact_id DROP NOT NULL;
                END IF;

                -- 3. Add FK to public.entities (cross-chain guard).
                IF to_regclass('public.entities') IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname    = 'important_dates_local_entity_id_fkey'
                         AND conrelid   = to_regclass(
                                 format('%I.important_dates', current_schema())
                             )
                   )
                THEN
                    ALTER TABLE important_dates
                        ADD CONSTRAINT important_dates_local_entity_id_fkey
                        FOREIGN KEY (local_entity_id)
                        REFERENCES public.entities(id) ON DELETE SET NULL;
                END IF;

                -- 4. Backfill from existing rows via public.contacts.
                IF to_regclass('public.contacts') IS NOT NULL
                   AND to_regclass('public.entities') IS NOT NULL
                THEN
                    UPDATE important_dates d
                    SET local_entity_id = c.entity_id
                    FROM public.contacts c
                    WHERE d.contact_id       = c.id
                      AND c.entity_id        IS NOT NULL
                      AND d.local_entity_id  IS NULL;
                END IF;

            END IF;
        END
        $$
    """)

    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.important_dates', current_schema())) IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name   = 'important_dates'
                     AND column_name  = 'local_entity_id'
               )
            THEN
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_important_dates_local_entity_id
                         ON %I.important_dates (local_entity_id)',
                    current_schema()
                );
            END IF;
        END
        $$
    """)

    # ------------------------------------------------------------------ #
    # contact_labels                                                       #
    # ------------------------------------------------------------------ #
    # contact_labels has PRIMARY KEY (label_id, contact_id), so contact_id
    # cannot be made nullable without dropping the PK first.  We introduce
    # a surrogate UUID primary key and replace the composite PK, then make
    # contact_id nullable and restore uniqueness via a partial unique index.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.contact_labels', current_schema())) IS NOT NULL THEN

                -- 1. Add surrogate id column (idempotent).
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'contact_labels'
                      AND column_name  = 'id'
                ) THEN
                    ALTER TABLE contact_labels
                        ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid();
                END IF;

                -- 2. Drop old composite PK (label_id, contact_id) if it still exists.
                --    The new PK will be (id).
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname    = 'contact_labels_pkey'
                      AND contype    = 'p'
                      AND conrelid   = to_regclass(
                              format('%I.contact_labels', current_schema())
                          )
                ) THEN
                    -- Check that the PK still references contact_id (not already replaced).
                    IF EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_attribute  a ON a.attrelid = c.conrelid
                                             AND a.attnum   = ANY(c.conkey)
                        WHERE c.conname   = 'contact_labels_pkey'
                          AND c.contype   = 'p'
                          AND c.conrelid  = to_regclass(
                                  format('%I.contact_labels', current_schema())
                              )
                          AND a.attname   = 'contact_id'
                    ) THEN
                        EXECUTE format(
                            'ALTER TABLE %I.contact_labels DROP CONSTRAINT contact_labels_pkey',
                            current_schema()
                        );
                    END IF;
                END IF;

                -- 3. Promote surrogate id to PRIMARY KEY (idempotent via pg_constraint check).
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname    = 'contact_labels_pkey'
                      AND contype    = 'p'
                      AND conrelid   = to_regclass(
                              format('%I.contact_labels', current_schema())
                          )
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE %I.contact_labels ADD PRIMARY KEY (id)',
                        current_schema()
                    );
                END IF;

                -- 4. Make contact_id nullable (now safe — no longer in PK).
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'contact_labels'
                      AND column_name  = 'contact_id'
                      AND is_nullable  = 'NO'
                ) THEN
                    ALTER TABLE contact_labels ALTER COLUMN contact_id DROP NOT NULL;
                END IF;

                -- 5. Add local_entity_id column (idempotent).
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'contact_labels'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE contact_labels ADD COLUMN local_entity_id UUID;
                END IF;

                -- 6. Add FK to public.entities (cross-chain guard).
                IF to_regclass('public.entities') IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname    = 'contact_labels_local_entity_id_fkey'
                         AND conrelid   = to_regclass(
                                 format('%I.contact_labels', current_schema())
                             )
                   )
                THEN
                    ALTER TABLE contact_labels
                        ADD CONSTRAINT contact_labels_local_entity_id_fkey
                        FOREIGN KEY (local_entity_id)
                        REFERENCES public.entities(id) ON DELETE SET NULL;
                END IF;

                -- 7. Backfill local_entity_id from existing rows via public.contacts.
                IF to_regclass('public.contacts') IS NOT NULL
                   AND to_regclass('public.entities') IS NOT NULL
                THEN
                    UPDATE contact_labels cl
                    SET local_entity_id = c.entity_id
                    FROM public.contacts c
                    WHERE cl.contact_id       = c.id
                      AND c.entity_id         IS NOT NULL
                      AND cl.local_entity_id  IS NULL;
                END IF;

            END IF;
        END
        $$
    """)

    # Indexes and partial unique indexes for contact_labels.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.contact_labels', current_schema())) IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name   = 'contact_labels'
                     AND column_name  = 'local_entity_id'
               )
            THEN
                -- Partial unique to preserve old-path uniqueness after PK drop.
                EXECUTE format(
                    'CREATE UNIQUE INDEX IF NOT EXISTS idx_contact_labels_label_contact
                         ON %I.contact_labels (label_id, contact_id)
                         WHERE contact_id IS NOT NULL',
                    current_schema()
                );

                -- Partial unique for new entity-anchored path.
                EXECUTE format(
                    'CREATE UNIQUE INDEX IF NOT EXISTS idx_contact_labels_label_entity
                         ON %I.contact_labels (label_id, local_entity_id)
                         WHERE local_entity_id IS NOT NULL',
                    current_schema()
                );

                -- Plain index for lookup by local_entity_id.
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_contact_labels_local_entity_id
                         ON %I.contact_labels (local_entity_id)',
                    current_schema()
                );
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    # Remove indexes added by this migration.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.addresses', current_schema())) IS NOT NULL THEN
                DROP INDEX IF EXISTS idx_addresses_local_entity_id;
                ALTER TABLE IF EXISTS addresses
                    DROP CONSTRAINT IF EXISTS addresses_local_entity_id_fkey;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'addresses'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE addresses DROP COLUMN local_entity_id;
                END IF;
            END IF;
        END
        $$
    """)

    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.important_dates', current_schema())) IS NOT NULL THEN
                DROP INDEX IF EXISTS idx_important_dates_local_entity_id;
                ALTER TABLE IF EXISTS important_dates
                    DROP CONSTRAINT IF EXISTS important_dates_local_entity_id_fkey;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'important_dates'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE important_dates DROP COLUMN local_entity_id;
                END IF;
            END IF;
        END
        $$
    """)

    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.contact_labels', current_schema())) IS NOT NULL THEN
                DROP INDEX IF EXISTS idx_contact_labels_local_entity_id;
                DROP INDEX IF EXISTS idx_contact_labels_label_entity;
                DROP INDEX IF EXISTS idx_contact_labels_label_contact;
                ALTER TABLE IF EXISTS contact_labels
                    DROP CONSTRAINT IF EXISTS contact_labels_local_entity_id_fkey;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name   = 'contact_labels'
                      AND column_name  = 'local_entity_id'
                ) THEN
                    ALTER TABLE contact_labels DROP COLUMN local_entity_id;
                END IF;
                -- Note: downgrade does NOT restore the old composite PK or remove
                -- the surrogate id column — those are structural changes that would
                -- require data migration to reverse safely.
            END IF;
        END
        $$
    """)
