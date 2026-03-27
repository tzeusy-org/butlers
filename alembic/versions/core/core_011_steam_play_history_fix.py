"""steam_play_history_fix: align connectors.steam_play_history schema to spec

Revision ID: core_011
Revises: core_010
Create Date: 2026-03-27 00:00:00.000000

Schema divergences in core_008:
  - steam_id BIGINT (denormalised) instead of steam_account_id UUID FK
    to public.steam_accounts(id)
  - missing app_name TEXT column
  - column named play_date instead of date

This migration fixes all three divergences in-place:

  1. ADD COLUMN steam_account_id UUID — nullable initially; we populate it
     via a best-effort JOIN on public.steam_accounts(steam_id). Rows that
     cannot be matched are left NULL (tolerated; connector now writes it).
  2. ADD COLUMN app_name TEXT — nullable; existing rows have no name.
  3. RENAME COLUMN play_date TO date.
  4. Drop the old unique constraint, add new one on
     (steam_account_id, app_id, date) while keeping backward-compat index
     on steam_id for queries that still filter by steam_id.
  5. ADD FOREIGN KEY steam_account_id → public.steam_accounts(id) ON DELETE CASCADE.

All DDL is guarded for idempotency where possible.
Downgrade reverses DDL only (data loss expected in backfill column).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_011"
down_revision = "core_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add steam_account_id UUID column (nullable).
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            ADD COLUMN IF NOT EXISTS steam_account_id UUID
    """)

    # -------------------------------------------------------------------------
    # 2. Best-effort back-fill: match existing rows to public.steam_accounts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.steam_accounts') IS NOT NULL THEN
                UPDATE connectors.steam_play_history h
                SET steam_account_id = sa.id
                FROM public.steam_accounts sa
                WHERE sa.steam_id = h.steam_id
                  AND h.steam_account_id IS NULL;
            END IF;
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_column THEN NULL;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Add app_name TEXT column (nullable).
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            ADD COLUMN IF NOT EXISTS app_name TEXT
    """)

    # -------------------------------------------------------------------------
    # 4. Rename play_date → date.
    #    Guard with an existence check to keep the migration idempotent.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'connectors'
                  AND table_name   = 'steam_play_history'
                  AND column_name  = 'play_date'
            ) THEN
                ALTER TABLE connectors.steam_play_history
                    RENAME COLUMN play_date TO date;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 5. Drop the old unique constraint (steam_id, app_id, play_date).
    #    The constraint name was defined in core_008.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE connectors.steam_play_history
                DROP CONSTRAINT IF EXISTS uq_steam_play_history_account_app_date;
        EXCEPTION
            WHEN undefined_table THEN NULL;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 6. Add new unique constraint on (steam_account_id, app_id, date).
    #    Only create when steam_account_id and date columns exist.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'connectors'
                  AND c.conname = 'uq_steam_play_history_account_app_date_v2'
            ) THEN
                ALTER TABLE connectors.steam_play_history
                    ADD CONSTRAINT uq_steam_play_history_account_app_date_v2
                    UNIQUE (steam_account_id, app_id, date);
            END IF;
        EXCEPTION
            WHEN undefined_column THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 7. Add FK steam_account_id → public.steam_accounts(id) ON DELETE CASCADE.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'connectors'
                  AND c.conname = 'fk_steam_play_history_account'
            ) AND to_regclass('public.steam_accounts') IS NOT NULL THEN
                ALTER TABLE connectors.steam_play_history
                    ADD CONSTRAINT fk_steam_play_history_account
                    FOREIGN KEY (steam_account_id)
                    REFERENCES public.steam_accounts(id)
                    ON DELETE CASCADE;
            END IF;
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_column THEN NULL;
        END
        $$;
    """)


def downgrade() -> None:
    # Drop FK
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            DROP CONSTRAINT IF EXISTS fk_steam_play_history_account
    """)

    # Drop new unique constraint
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            DROP CONSTRAINT IF EXISTS uq_steam_play_history_account_app_date_v2
    """)

    # Rename date → play_date
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'connectors'
                  AND table_name   = 'steam_play_history'
                  AND column_name  = 'date'
            ) THEN
                ALTER TABLE connectors.steam_play_history
                    RENAME COLUMN date TO play_date;
            END IF;
        END
        $$;
    """)

    # Restore old unique constraint
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'connectors'
                  AND c.conname = 'uq_steam_play_history_account_app_date'
            ) THEN
                ALTER TABLE connectors.steam_play_history
                    ADD CONSTRAINT uq_steam_play_history_account_app_date
                    UNIQUE (steam_id, app_id, play_date);
            END IF;
        EXCEPTION
            WHEN undefined_column THEN NULL;
        END
        $$;
    """)

    # Drop new columns
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            DROP COLUMN IF EXISTS app_name
    """)
    op.execute("""
        ALTER TABLE connectors.steam_play_history
            DROP COLUMN IF EXISTS steam_account_id
    """)
