"""Drop verified-dead contacts module table: contacts_source_accounts.

Revision ID: contacts_002
Revises: contacts_001
Create Date: 2026-06-12 00:00:00.000000

Table has 0 runtime code references and 0 rows across all 6 butler schemas.
CREATE location: 001_contacts_sync.py (contacts_001)

NOT dropped (still referenced at runtime):
  - contacts_sync_state
  - contacts_source_links

Guards:
  - DROP TABLE IF EXISTS is idempotent and schema-safe.
  - This migration is applied per butler schema; IF EXISTS ensures it is safe
    for schemas where the table may have already been cleaned up.

Downgrade recreates the original contacts_source_accounts schema (contacts_001)
including its index. No data to restore.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "contacts_002"
down_revision = "contacts_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contacts_source_accounts")


def downgrade() -> None:
    # Recreate the original schema from contacts_001 (001_contacts_sync.py).
    # No data to restore. Index name keeps the original idx_ prefix used by
    # 001_contacts_sync.py to stay byte-faithful to the schema being rolled back.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts_source_accounts (
            provider TEXT NOT NULL,
            account_id TEXT NOT NULL,
            subject_email TEXT,
            connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_success_at TIMESTAMPTZ,
            PRIMARY KEY (provider, account_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contacts_source_accounts_last_success
            ON contacts_source_accounts (last_success_at)
        """
    )
