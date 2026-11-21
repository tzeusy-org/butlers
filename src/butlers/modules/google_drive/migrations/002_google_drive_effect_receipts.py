"""google_drive: create google_drive_effect_receipts table

Revision ID: google_drive_002
Revises: google_drive_001

Replay receipts for the key_required native Docs and Sheets tools (RFC 0038 D12): the broker's
``effect_id`` is the key, so a repeated effect returns the stored receipt without calling
Google. Additive; the downgrade drops it.
"""

from __future__ import annotations

from alembic import op

revision = "google_drive_002"
down_revision = "google_drive_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS google_drive_effect_receipts (
            effect_id  UUID        PRIMARY KEY,
            tool       TEXT        NOT NULL,
            file_id    TEXT,
            receipt    JSONB       NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_drive_effect_receipts")
