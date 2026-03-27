"""google_drive: create google_drive_butler_folders table

Revision ID: google_drive_001
Revises:
Create Date: 2026-03-27 00:00:00.000000

Creates the ``google_drive_butler_folders`` table used by GoogleDriveModule
to cache the Drive folder IDs for each butler's output hierarchy
(``butlers/{butler_name}/``).

Schema (spec §2.2):
    butler_name     TEXT   — butler role name
    account_email   TEXT   — Google account email for this folder mapping
    folder_id       TEXT NOT NULL — Drive folder ID (cached for fast lookups)
    folder_path     TEXT NOT NULL — human-readable path (e.g. "butlers/finance")
    created_at      TIMESTAMPTZ DEFAULT now()
    PRIMARY KEY (butler_name, account_email)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "google_drive_001"
down_revision = None
branch_labels = ("google_drive",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS google_drive_butler_folders (
            butler_name   TEXT        NOT NULL,
            account_email TEXT        NOT NULL,
            folder_id     TEXT        NOT NULL,
            folder_path   TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (butler_name, account_email)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_gdrive_butler_folders_butler_name
        ON google_drive_butler_folders (butler_name)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_drive_butler_folders")
