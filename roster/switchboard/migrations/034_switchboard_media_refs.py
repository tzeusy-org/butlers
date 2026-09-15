"""media_refs: cross-connector idempotency ledger for materialized media blobs.

Revision ID: sw_034
Revises: sw_033
Create Date: 2026-09-09 00:00:00.000000

bu-2jtfw.7 (vision lane). A connector that fetches media bytes (Telegram
photo/document via ``getFile``, and future Discord/WhatsApp attachment
fetches) must put at most one blob per media id, even when the same update is
replayed. ``attachment_refs`` (sw_004) already does this for Gmail, but it is
keyed by ``(message_id, attachment_id)`` alone and its columns/comments are
Gmail-specific (``message_id`` documented as "Gmail message_id"); reusing it
for other connectors risks a cross-provider primary-key collision, since two
providers' opaque ids share no namespace. ``media_refs`` generalizes the same
pre-put existence-check pattern (gmail.py's ``fetch_attachment``) with an
explicit ``connector_type``/``endpoint_identity`` namespace so any connector
can share the table safely.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_034"
down_revision = "sw_033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_refs (
            connector_type       TEXT NOT NULL,
            endpoint_identity    TEXT NOT NULL,
            external_message_id  TEXT NOT NULL,
            media_id              TEXT NOT NULL,
            media_type            TEXT NOT NULL,
            storage_ref           TEXT NULL,
            size_bytes            BIGINT NULL,
            width                 INTEGER NULL,
            height                INTEGER NULL,
            fetched               BOOLEAN NOT NULL DEFAULT FALSE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (connector_type, endpoint_identity, external_message_id, media_id)
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE media_refs IS
        'Cross-connector idempotency ledger for materialized media blobs. A row '
        'is written only after a successful BlobStore.put(); a connector consults '
        'this table before re-fetching a media id it may have already stored '
        '(replay-safe). See docs/redesigns/2026-09-05-jarvis-pursuit.md #7.'
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_media_refs_fetched_created_at
        ON media_refs (fetched, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_media_refs_fetched_created_at")
    op.execute("DROP TABLE IF EXISTS media_refs")
