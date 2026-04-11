"""Move per-session timeout to model_catalog and reduce runtime_config overlap.

Revision ID: core_073
Revises: core_072
Create Date: 2026-04-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_073"
down_revision = "core_072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.model_catalog
        ADD COLUMN IF NOT EXISTS session_timeout_s INT NOT NULL DEFAULT 1800
    """)
    op.execute("""
        ALTER TABLE IF EXISTS runtime_config
        DROP COLUMN IF EXISTS model,
        DROP COLUMN IF EXISTS runtime_type,
        DROP COLUMN IF EXISTS args,
        DROP COLUMN IF EXISTS session_timeout_s
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE IF EXISTS runtime_config
        ADD COLUMN IF NOT EXISTS model TEXT,
        ADD COLUMN IF NOT EXISTS runtime_type TEXT NOT NULL DEFAULT 'codex',
        ADD COLUMN IF NOT EXISTS args JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS session_timeout_s INT NOT NULL DEFAULT 900
    """)
    op.execute("""
        ALTER TABLE public.model_catalog
        DROP COLUMN IF EXISTS session_timeout_s
    """)
