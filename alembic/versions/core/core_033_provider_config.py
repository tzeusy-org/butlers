"""provider_config: add shared.provider_config table

Revision ID: core_033
Revises: core_032
Create Date: 2026-03-16 00:00:00.000000

Creates ``shared.provider_config`` — a registry of provider-level settings
shared across all models of a given provider type.  Initially used for
Ollama's base URL, but designed generically for any HTTP-based provider.

Schema:

  - id            UUID PK  (DEFAULT gen_random_uuid())
  - provider_type TEXT UNIQUE NOT NULL  (e.g. 'ollama')
  - display_name  TEXT NOT NULL         (e.g. 'Ollama (tailnet)')
  - config        JSONB NOT NULL DEFAULT '{}'
  - enabled       BOOLEAN NOT NULL DEFAULT false
  - created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  - updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_033"
down_revision = "core_032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.provider_config (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_type TEXT        NOT NULL,
            display_name  TEXT        NOT NULL,
            config        JSONB       NOT NULL DEFAULT '{}'::jsonb,
            enabled       BOOLEAN     NOT NULL DEFAULT false,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_provider_config_type UNIQUE (provider_type)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_config_enabled
        ON shared.provider_config (enabled)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shared.idx_provider_config_enabled")
    op.execute("DROP TABLE IF EXISTS shared.provider_config")
