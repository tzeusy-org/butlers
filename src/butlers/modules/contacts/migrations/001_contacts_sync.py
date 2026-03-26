"""contacts_sync — collapsed from contacts_001 and contacts_002

Revision ID: contacts_001
Revises:
Create Date: 2026-03-26 00:00:00.000000

Creates module-owned tables for contacts sync state persistence.
Replaces KV-store (core.state) usage with proper relational tables per spec S4.3.

Tables:
  - contacts_source_accounts  -- registered sync accounts per provider
  - contacts_sync_state       -- per-account incremental sync cursor and timestamps
  - contacts_source_links     -- provenance links from external contacts to local contacts

Note: public.contact_info is now created by core_002; this migration does not
touch it.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "contacts_001"
down_revision = None
branch_labels = ("contacts",)
depends_on = None


def upgrade() -> None:
    # --- contacts_source_accounts --------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS contacts_source_accounts (
            provider TEXT NOT NULL,
            account_id TEXT NOT NULL,
            subject_email TEXT,
            connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_success_at TIMESTAMPTZ,
            PRIMARY KEY (provider, account_id)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_source_accounts_last_success
            ON contacts_source_accounts (last_success_at)
    """)

    # --- contacts_sync_state -------------------------------------------------
    # contact_versions is a JSONB map of {external_contact_id: etag_hash} used
    # for idempotent change detection in the sync engine.
    op.execute("""
        CREATE TABLE IF NOT EXISTS contacts_sync_state (
            provider TEXT NOT NULL,
            account_id TEXT NOT NULL,
            sync_cursor TEXT,
            cursor_issued_at TIMESTAMPTZ,
            last_full_sync_at TIMESTAMPTZ,
            last_incremental_sync_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error TEXT,
            contact_versions JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (provider, account_id)
        )
    """)

    # --- contacts_source_links -----------------------------------------------
    # local_contact_id is SET NULL on delete so links survive local contact archival.
    op.execute("""
        CREATE TABLE IF NOT EXISTS contacts_source_links (
            provider TEXT NOT NULL,
            account_id TEXT NOT NULL,
            external_contact_id TEXT NOT NULL,
            local_contact_id UUID,
            source_etag TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ,
            PRIMARY KEY (provider, account_id, external_contact_id)
        )
    """)

    # Conditionally add FK to contacts(id) only when the contacts table exists
    # in the current schema (some butlers enable contacts module without owning
    # a local contacts table).
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.contacts', current_schema())) IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'contacts_source_links_local_contact_id_fkey'
                      AND conrelid = to_regclass(
                            format('%I.contacts_source_links', current_schema())
                      )
               )
            THEN
                ALTER TABLE contacts_source_links
                    ADD CONSTRAINT contacts_source_links_local_contact_id_fkey
                    FOREIGN KEY (local_contact_id)
                    REFERENCES contacts(id)
                    ON DELETE SET NULL;
            END IF;
        END
        $$;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_source_links_local_contact
            ON contacts_source_links (local_contact_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_source_links_last_seen
            ON contacts_source_links (last_seen_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_contacts_source_links_last_seen")
    op.execute("DROP INDEX IF EXISTS idx_contacts_source_links_local_contact")
    op.execute("DROP TABLE IF EXISTS contacts_source_links")
    op.execute("DROP TABLE IF EXISTS contacts_sync_state")
    op.execute("DROP INDEX IF EXISTS idx_contacts_source_accounts_last_success")
    op.execute("DROP TABLE IF EXISTS contacts_source_accounts")
