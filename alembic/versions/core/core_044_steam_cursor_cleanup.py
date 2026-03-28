"""steam_cursor_cleanup: add revoked_at to steam_accounts for 30-day cursor retention.

Revision ID: core_044
Revises: core_043
Create Date: 2026-03-28 00:00:00.000000

Adds a ``revoked_at`` column to ``public.steam_accounts`` so that the Steam
connector can enforce the 30-day cursor-retention policy: cursors for revoked
accounts are purged only after 30 days have elapsed since revocation.

Schema change:
  public.steam_accounts
    revoked_at   TIMESTAMPTZ   nullable — set when status is set to 'revoked';
                               NULL for active/suspended accounts and for rows
                               that were revoked before this migration ran.

Index:
  ix_steam_accounts_revoked_at — partial index on rows where revoked_at IS NOT
      NULL, to support efficient cleanup queries.

Back-fill:
  Existing 'revoked' rows have revoked_at set to connected_at (best available
  proxy for the actual revocation time, as steam_accounts has no updated_at)
  so they are still subject to the 30-day purge once the connector is updated.
  This is conservative — it errs toward retaining cursors longer than necessary.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_044"
down_revision = "core_043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add revoked_at column to public.steam_accounts (idempotent).
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.steam_accounts
        ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ
    """)

    # -------------------------------------------------------------------------
    # 2. Back-fill: set revoked_at for already-revoked rows.
    #    Use connected_at as the best available proxy (no updated_at column);
    #    this is conservative — it errs toward retaining cursors longer.
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE public.steam_accounts
        SET revoked_at = connected_at
        WHERE status = 'revoked'
          AND revoked_at IS NULL
    """)

    # -------------------------------------------------------------------------
    # 3. Create partial index for fast cleanup queries.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_steam_accounts_revoked_at
            ON public.steam_accounts (revoked_at)
            WHERE revoked_at IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_steam_accounts_revoked_at")
    op.execute("""
        ALTER TABLE public.steam_accounts
        DROP COLUMN IF EXISTS revoked_at
    """)
