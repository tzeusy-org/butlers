"""public_state: shared KV state table for cross-butler dashboard config.

Revision ID: core_087
Revises: core_086
Create Date: 2026-05-01 00:00:00.000000

The dashboard-level general settings (timezone, language, currency, etc.)
are persisted via ``state_get``/``state_set`` against the shared credential
pool, whose ``search_path`` is ``public``.  Until now there was no
``public.state`` table — only per-butler ``<schema>.state`` tables created
in core_001 — so ``GET /api/settings/general`` 500'd with
``UndefinedTableError: relation "state" does not exist``.

This migration adds ``public.state`` with the same shape as the per-butler
state tables.  Runtime butler roles already get SELECT on public via
``_grant_public_schema_read_privileges``; writes happen from the dashboard
API user, which is the schema owner.
"""

from __future__ import annotations

from alembic import op

revision = "core_087"
down_revision = "core_086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.state (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.state")
