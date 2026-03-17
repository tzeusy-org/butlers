"""token_usage_ledger_and_limits: create shared.token_usage_ledger and shared.token_limits

Revision ID: core_035
Revises: core_034
Create Date: 2026-03-17 00:00:00.000000

Creates the database foundation for per-catalog-entry rolling-window token budgets:

  1. shared.token_usage_ledger — append-only ledger of per-session token consumption.
     Range-partitioned on ``recorded_at`` with monthly partitions.
     Composite PK (id, recorded_at) as required by PostgreSQL range partitioning.
     FK to shared.model_catalog(id) ON DELETE CASCADE.
     Composite index idx_ledger_entry_time on (catalog_entry_id, recorded_at).

     If the pg_partman extension is available, the table is registered for
     automatic monthly partition creation and 90-day retention.  If not, 6
     monthly partitions are created (current + 5 ahead) and a warning is
     logged that manual partition maintenance will be required.

  2. shared.token_limits — per-catalog-entry rolling-window budgets.
     One row per catalog entry that has limits configured; entries without a
     row in this table are unlimited (no enforcement, usage still recorded).
     UNIQUE on catalog_entry_id.  FK ON DELETE CASCADE.
     Nullable limit_24h / limit_30d (bigint).
     Independent reset_24h_at / reset_30d_at (timestamptz) for manual resets.

Downgrade drops both tables (which cascades to all partitions).
"""

from __future__ import annotations

import logging

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_035"
down_revision = "core_034"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

# Number of forward monthly partitions to create when pg_partman is absent.
_FALLBACK_PARTITION_COUNT = 6


def _pg_partman_available() -> bool:
    """Return True when the pg_partman extension is installed in this database."""
    bind = op.get_bind()
    result = bind.execute(
        __import__("sqlalchemy").text(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = 'pg_partman'"
        )
    )
    return bool(result.scalar())


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create shared.token_usage_ledger (partitioned by recorded_at)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.token_usage_ledger (
            id               UUID NOT NULL DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            butler_name      TEXT NOT NULL,
            session_id       UUID,
            input_tokens     INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
    """)

    # Composite index for the standard quota-check query pattern:
    # WHERE catalog_entry_id = $1 AND recorded_at > $2
    # Indexes on the parent table propagate automatically to child partitions (PG 11+).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_entry_time
        ON shared.token_usage_ledger (catalog_entry_id, recorded_at)
    """)

    # -------------------------------------------------------------------------
    # 2. Create shared.token_limits
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.token_limits (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL UNIQUE
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            limit_24h        BIGINT,
            limit_30d        BIGINT,
            reset_24h_at     TIMESTAMPTZ,
            reset_30d_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # 3. Partition management: pg_partman if available, manual fallback otherwise
    # -------------------------------------------------------------------------
    if _pg_partman_available():
        # pg_partman is present — register for automatic monthly partition
        # creation with 90-day retention.  pg_partman will create the initial
        # partitions (current month + premake=2 ahead) automatically.
        op.execute("""
            SELECT partman.create_parent(
                p_parent_table   => 'shared.token_usage_ledger',
                p_control        => 'recorded_at',
                p_type           => 'range',
                p_interval       => 'monthly',
                p_premake        => 2,
                p_start_partition => date_trunc('month', now())::text
            )
        """)
        # Set 90-day retention (pg_partman drops partitions older than this).
        op.execute("""
            UPDATE partman.part_config
            SET    retention              = '90 days',
                   retention_keep_table  = false,
                   retention_keep_index  = false
            WHERE  parent_table = 'shared.token_usage_ledger'
        """)
    else:
        # pg_partman not installed — create 6 months of partitions manually
        # (current month + 5 months ahead) and log a warning.
        log.warning(
            "pg_partman extension is not installed.  "
            "shared.token_usage_ledger partitions will NOT be created automatically.  "
            "You must create new monthly partitions manually or via a scheduled task "
            "before each month begins.  "
            "Install pg_partman and run core_035 upgrade to switch to automatic management."
        )
        op.execute("""
            DO $$
            DECLARE
                i           INT;
                month_start TIMESTAMPTZ;
                month_end   TIMESTAMPTZ;
                part_name   TEXT;
            BEGIN
                FOR i IN 0 .. 5 LOOP
                    month_start := date_trunc('month', now() + (i || ' months')::interval);
                    month_end   := month_start + INTERVAL '1 month';
                    part_name   := format(
                        'token_usage_ledger_%s',
                        to_char(month_start, 'YYYYMM')
                    );
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS shared.%I '
                        'PARTITION OF shared.token_usage_ledger '
                        'FOR VALUES FROM (%L) TO (%L)',
                        part_name,
                        month_start,
                        month_end
                    );
                END LOOP;
            END
            $$
        """)


def downgrade() -> None:
    # Deregister pg_partman configuration before dropping the table, if present.
    # This is best-effort — if pg_partman is not available the DELETE is a no-op.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
                DELETE FROM partman.part_config
                WHERE parent_table = 'shared.token_usage_ledger';
            END IF;
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_schema THEN NULL;
        END
        $$
    """)

    # Dropping the parent table cascades to all child partitions.
    # Indexes on child partitions are dropped automatically.
    op.execute("DROP INDEX IF EXISTS shared.idx_ledger_entry_time")
    op.execute("DROP TABLE IF EXISTS shared.token_limits")
    op.execute("DROP TABLE IF EXISTS shared.token_usage_ledger CASCADE")
