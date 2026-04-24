"""google_accounts: ensure metadata JSONB NOT NULL and last_token_refresh_at TIMESTAMPTZ.

Revision ID: core_078
Revises: core_077
Create Date: 2026-04-24 00:00:00.000000

Additive schema migration for the Google Health enablement layer
(bu-k5l35.1.4 / openspec change google-health-connector).

Two columns the test-mode warning + 7-day expiry heuristic depend on:

  - metadata JSONB NOT NULL DEFAULT '{}'::jsonb
      Carries per-account flags. Known key:
      * google_health_test_mode (bool) — set by the OAuth callback when
        the underlying OAuth client is in test mode and Google Health
        scopes were granted. Dashboard surfaces a warning banner; the
        refresh token expires 7 days after issue.
      Absence of a key is interpreted as "not set".

  - last_token_refresh_at TIMESTAMPTZ NULL
      Populated by the OAuth callback / refresh pipeline on every
      successful token refresh. Dashboard reads this column for the
      7-day test-mode expiry heuristic.

Idempotency contract:

  core_008 already creates the columns on fresh installs. This
  migration is primarily for existing DBs where core_008 predates
  these columns, but is written defensively so it is safe on any
  database state:

  - ADD COLUMN IF NOT EXISTS is a no-op when the column already exists.
  - metadata is converted to NOT NULL DEFAULT '{}'::jsonb only after
    backfilling NULLs to '{}'::jsonb so the constraint never fails on
    legacy rows.
  - Running upgrade() twice in a row produces no error and no duplicate
    columns (see tests).

Downgrade strategy:

  Strictly additive by column name. downgrade() drops both columns.
  No data is preserved on downgrade — this matches the contract used
  by other additive column migrations in the chain (core_076, etc.).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_078"
down_revision = "core_077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add columns idempotently.
    #    No-op when the columns already exist (fresh installs via core_008).
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.google_accounts') IS NULL THEN
                RETURN;
            END IF;

            ALTER TABLE public.google_accounts
                ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb,
                ADD COLUMN IF NOT EXISTS last_token_refresh_at TIMESTAMPTZ;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 2. Backfill NULL metadata to '{}'::jsonb before enforcing NOT NULL.
    #    Legacy rows (pre-core_008) might have NULL; the spec requires the
    #    column be NOT NULL so absence of a key can be interpreted as
    #    "not set" rather than "unknown".
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.google_accounts') IS NULL THEN
                RETURN;
            END IF;

            UPDATE public.google_accounts
                SET metadata = '{}'::jsonb
                WHERE metadata IS NULL;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Enforce NOT NULL on metadata. Idempotent — ALTER COLUMN SET NOT NULL
    #    is a no-op when the column is already NOT NULL.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.google_accounts') IS NULL THEN
                RETURN;
            END IF;

            ALTER TABLE public.google_accounts
                ALTER COLUMN metadata SET NOT NULL;
        END
        $$;
    """)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Drop both columns. Strictly additive downgrade — matches the
    # contract used by other column-add migrations in the chain.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.google_accounts') IS NULL THEN
                RETURN;
            END IF;

            ALTER TABLE public.google_accounts
                DROP COLUMN IF EXISTS last_token_refresh_at,
                DROP COLUMN IF EXISTS metadata;
        END
        $$;
    """)
