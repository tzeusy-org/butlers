"""rel_029 — create contact_entity_map: contact_id → entity_id bridge (bu-ozpyl).

Revision ID: rel_029
Revises: rel_028
Create Date: 2026-06-19 00:00:00.000000

Context
-------
Phase 7 of the public.contacts retirement (bu-oluyt) removes all live SQL
references to ``public.contacts`` from relationship tool code.

``roster/relationship/tools/_entity_resolve.py`` previously resolved
contact_id → entity_id via:

    SELECT entity_id FROM contacts WHERE id = $1

``contacts_source_links`` (contacts module) covers externally-synced contacts
(Google, Telegram) but not contacts created directly via ``contact_create``
(CRM contacts).  ``public.entities`` is queryable but maps entity_id → entity,
not contact_id → entity_id.

This migration creates a lightweight ``contact_entity_map`` table in the
relationship schema that acts as the non-``public.contacts`` bridge for ALL
contacts (synced or CRM-created).  The runtime write path (``contact_create``)
will populate it going forward; this migration backfills existing rows.

After this migration, ``_entity_resolve.py`` resolves in order:
  1. contacts_source_links (synced contacts, contacts module) — fast path.
  2. contact_entity_map (this table) — covers CRM contacts + backfilled rows.
  3. public.entities — entity-first / pass-through callers.
public.contacts is NOT queried in Python tool code.

Design
------
``contact_entity_map`` is a simple two-column table — no FK constraint on
``entity_id`` (public.entities is in a different schema, FK is non-trivial in
the context of Alembic's multi-chain approach and the relationship schema may
have a separate search_path).  On conflict the INSERT silently ignores
duplicates (ON CONFLICT DO NOTHING).

Idempotency
-----------
``CREATE TABLE IF NOT EXISTS`` / ``IF NOT EXISTS`` index guard.
``INSERT ... ON CONFLICT DO NOTHING`` for the backfill.

Safety
------
``to_regclass`` check before the backfill so that if ``public.contacts`` has
already been dropped (bu-y6o7q), the migration degrades gracefully (no backfill
needed — all contacts exist via live write path already).

Downgrade
---------
Reversible: drops the table and index.
"""

from __future__ import annotations

from alembic import op

revision = "rel_029"
down_revision = "rel_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create contact_entity_map table (idempotent).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_entity_map (
            contact_id  UUID NOT NULL,
            entity_id   UUID NOT NULL,
            CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)
        )
        """
    )

    # 2. Index on entity_id for reverse lookup.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contact_entity_map_entity_id
            ON contact_entity_map (entity_id)
        """
    )

    # 3. Backfill from public.contacts (skipped if table already dropped).
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.contacts') IS NOT NULL THEN
                INSERT INTO contact_entity_map (contact_id, entity_id)
                SELECT id, entity_id
                FROM   public.contacts
                WHERE  entity_id IS NOT NULL
                ON CONFLICT (contact_id) DO NOTHING;
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_contact_entity_map_entity_id")
    op.execute("DROP TABLE IF EXISTS contact_entity_map")
