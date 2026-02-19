"""create butler_secrets table for generic key-value secrets storage

Revision ID: core_008
Revises: core_007
Create Date: 2026-02-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_008"
down_revision = "core_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Generic key-value secrets store. Replaces the singleton-row pattern of
    # google_oauth_credentials with a flexible per-key-row design that is more
    # queryable and extensible for arbitrary credential types.
    #
    # Columns:
    #   secret_key   - unique name for the secret (e.g. "google_client_id")
    #   secret_value - the actual secret material (stored as plain TEXT;
    #                  encryption at rest is a platform concern)
    #   category     - grouping label for dashboard display (e.g. "google_oauth",
    #                  "telegram", "general")
    #   description  - optional human-readable description shown in UI
    #   is_sensitive - when True, the value is masked in dashboard/log output
    #   created_at   - immutable creation timestamp
    #   updated_at   - last-write timestamp (application must maintain this)
    #   expires_at   - optional TTL; NULL means the secret never expires
    op.execute("""
        CREATE TABLE IF NOT EXISTS butler_secrets (
            secret_key   TEXT PRIMARY KEY,
            secret_value TEXT NOT NULL,
            category     TEXT NOT NULL DEFAULT 'general',
            description  TEXT,
            is_sensitive BOOLEAN NOT NULL DEFAULT true,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_butler_secrets_category
        ON butler_secrets (category)
    """)

    op.execute("""
        COMMENT ON TABLE butler_secrets IS
        'Generic key-value secrets store for all butler credential types.
         One row per logical secret. Sensitive values are masked in dashboards
         and log output when is_sensitive=true. Use expires_at for short-lived
         tokens such as OAuth access tokens.'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_butler_secrets_category")
    op.execute("DROP TABLE IF EXISTS butler_secrets")
