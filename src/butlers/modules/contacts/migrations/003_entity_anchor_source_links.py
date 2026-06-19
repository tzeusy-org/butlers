"""contacts_003 — switch contacts_source_links to entity-ID anchor (bu-tzyuh)

Revision ID: contacts_003
Revises: contacts_002
Create Date: 2026-06-19

Background
----------
The contacts backfill module (ContactBackfillEngine/Writer) previously wrote
the ``public.contacts.id`` UUID into ``contacts_source_links.local_contact_id``
as the idempotency anchor (provider + account + external_id → local contact).

After bu-tzyuh, ``local_id`` throughout the backfill is a ``public.entities.id``
UUID.  This migration adds a new ``local_entity_id`` column to carry that value,
so the source-link lookup and upsert paths work without referencing
``public.contacts``.  ``local_contact_id`` is retained as dead weight until the
gated bu-y6o7q bead drops ``public.contacts`` (and this column with it).

What this migration does
------------------------
1. Add ``local_entity_id UUID`` to ``contacts_source_links`` (nullable, no FK
   constraint until public.entities is confirmed present — guarded via
   to_regclass).
2. Conditionally add FK ``REFERENCES public.entities(id) ON DELETE SET NULL``
   when public.entities exists in the current database.
3. Backfill ``local_entity_id`` from existing links where the linked
   public.contacts row has a non-null entity_id (requires public.contacts to
   exist; guarded via to_regclass — safe no-op on fresh schemas).
4. Create index ``idx_contacts_source_links_local_entity`` on the new column.

Safety properties
-----------------
- All DDL is idempotent (IF NOT EXISTS / conditional blocks).
- Cross-chain guard: both public.entities and public.contacts are probed via
  to_regclass before any DML or FK creation so the migration is order-safe
  relative to the core chain.
- Non-destructive: public.contacts and local_contact_id are untouched.
- Applied per butler schema (alembic applies module migrations per-schema).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "contacts_003"
down_revision = "contacts_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add local_entity_id column (idempotent).
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name   = 'contacts_source_links'
                  AND column_name  = 'local_entity_id'
            ) THEN
                ALTER TABLE contacts_source_links ADD COLUMN local_entity_id UUID;
            END IF;
        END
        $$
    """)

    # 2. Add FK to public.entities when the table exists (cross-chain guard).
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.entities') IS NOT NULL
               AND to_regclass(
                       format('%I.contacts_source_links', current_schema())
                   ) IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint
                   WHERE conname = 'contacts_source_links_local_entity_id_fkey'
                     AND conrelid = to_regclass(
                             format('%I.contacts_source_links', current_schema())
                         )
               )
            THEN
                ALTER TABLE contacts_source_links
                    ADD CONSTRAINT contacts_source_links_local_entity_id_fkey
                    FOREIGN KEY (local_entity_id)
                    REFERENCES public.entities(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """)

    # 3. Backfill entity_id from existing source links via public.contacts.
    #    Requires both public.contacts and public.entities to exist; guarded.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.contacts') IS NOT NULL
               AND to_regclass('public.entities') IS NOT NULL
               AND to_regclass(
                       format('%I.contacts_source_links', current_schema())
                   ) IS NOT NULL
            THEN
                UPDATE contacts_source_links sl
                SET local_entity_id = c.entity_id
                FROM public.contacts c
                WHERE sl.local_contact_id = c.id
                  AND c.entity_id         IS NOT NULL
                  AND sl.local_entity_id  IS NULL;
            END IF;
        END
        $$
    """)

    # 4. Index for source-link lookup by entity_id (idempotent).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_source_links_local_entity
            ON contacts_source_links (local_entity_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_contacts_source_links_local_entity")
    op.execute(
        "ALTER TABLE IF EXISTS contacts_source_links"
        " DROP CONSTRAINT IF EXISTS contacts_source_links_local_entity_id_fkey;"
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name   = 'contacts_source_links'
                  AND column_name  = 'local_entity_id'
            ) THEN
                ALTER TABLE contacts_source_links DROP COLUMN local_entity_id;
            END IF;
        END
        $$
    """)
