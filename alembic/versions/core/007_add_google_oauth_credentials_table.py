"""add google_oauth_credentials table for shared OAuth credential storage

Revision ID: core_007
Revises: core_006
Create Date: 2026-02-19 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_007"
down_revision = "core_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A single-row credential store keyed by provider name.
    # The ``credentials`` column is a JSONB blob holding:
    #   client_id, client_secret, refresh_token, scope, stored_at.
    # Secret material is never logged; it lives only in this table.
    op.execute("""
        CREATE TABLE IF NOT EXISTS google_oauth_credentials (
            credential_key TEXT PRIMARY KEY,
            credentials JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        COMMENT ON TABLE google_oauth_credentials IS
        'Shared Google OAuth credentials (client_id, client_secret, refresh_token, scope).
         A single row keyed by credential_key=''google'' is maintained via UPSERT.
         Secret fields are never logged in plaintext.'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_oauth_credentials")
