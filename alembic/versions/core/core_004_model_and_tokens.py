"""model_and_tokens: model catalog, provider config, token usage ledger & limits

Revision ID: core_004
Revises: core_003
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_025, core_030, core_033, core_034, core_035.

Creates PUBLIC schema tables for dynamic model routing and token budgets:

  1. public.model_catalog          -- global registry of available model entries
  2. public.butler_model_overrides -- per-butler overrides (FK -> model_catalog)
  3. public.provider_config        -- provider-level settings registry
  4. public.token_usage_ledger     -- append-only ledger, PARTITIONED BY RANGE
  5. public.token_limits           -- per-catalog-entry rolling-window budgets

complexity_tier CHECK includes all tiers from the start:
  trivial, medium, high, extra_high, discretion, self_healing

Grants SELECT, INSERT, UPDATE, DELETE to all butler roles.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_004"
down_revision = "core_003"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

_PUBLIC_TABLES = (
    "public.model_catalog",
    "public.butler_model_overrides",
    "public.provider_config",
    "public.token_usage_ledger",
    "public.token_limits",
)

_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high", "discretion", "self_healing")
_COMPLEXITY_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"

# Number of forward monthly partitions to create when pg_partman is absent.
_FALLBACK_PARTITION_COUNT = 6


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _pg_partman_available() -> bool:
    """Return True when the pg_partman extension is installed in this database."""
    bind = op.get_bind()
    result = bind.execute(sa.text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'pg_partman'"))
    return bool(result.scalar())


def _load_seed_entries() -> list[dict]:
    """Load default model catalog entries from model_catalog_defaults.toml."""
    import tomllib  # noqa: PLC0415

    defaults_path = Path(__file__).resolve().parents[3] / "model_catalog_defaults.toml"
    if not defaults_path.exists():
        return []
    with open(defaults_path, "rb") as f:
        data = tomllib.load(f)
    return [
        m for m in data.get("models", [])
        if m.get("complexity_tier", "medium") in _COMPLEXITY_TIERS
    ]


def upgrade() -> None:
    # --------------------------------------------------------------------- #
    # 1. public.model_catalog
    # --------------------------------------------------------------------- #
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS public.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN {_COMPLEXITY_CHECK})
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_catalog_complexity_tier_enabled
        ON public.model_catalog (complexity_tier, enabled, priority)
    """)

    # --------------------------------------------------------------------- #
    # 2. public.butler_model_overrides (with source column from core_030)
    # --------------------------------------------------------------------- #
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS public.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            source           TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN {_COMPLEXITY_CHECK})
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_butler_model_overrides_butler_name
        ON public.butler_model_overrides (butler_name)
    """)

    # --------------------------------------------------------------------- #
    # 3. public.provider_config
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.provider_config (
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
        ON public.provider_config (enabled)
    """)

    # --------------------------------------------------------------------- #
    # 4. public.token_usage_ledger (PARTITIONED BY RANGE on recorded_at)
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.token_usage_ledger (
            id               UUID NOT NULL DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            butler_name      TEXT NOT NULL,
            session_id       UUID,
            input_tokens     INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_entry_time
        ON public.token_usage_ledger (catalog_entry_id, recorded_at)
    """)

    # --------------------------------------------------------------------- #
    # 5. public.token_limits
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.token_limits (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL UNIQUE
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            limit_24h        BIGINT,
            limit_30d        BIGINT,
            reset_24h_at     TIMESTAMPTZ,
            reset_30d_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --------------------------------------------------------------------- #
    # 6. Partition management
    # --------------------------------------------------------------------- #
    if _pg_partman_available():
        op.execute("""
            SELECT partman.create_parent(
                p_parent_table   => 'public.token_usage_ledger',
                p_control        => 'recorded_at',
                p_type           => 'range',
                p_interval       => 'monthly',
                p_premake        => 2,
                p_start_partition => date_trunc('month', now())::text
            )
        """)
        op.execute("""
            UPDATE partman.part_config
            SET    retention              = '90 days',
                   retention_keep_table  = false,
                   retention_keep_index  = false
            WHERE  parent_table = 'public.token_usage_ledger'
        """)
    else:
        log.warning(
            "pg_partman extension is not installed.  "
            "public.token_usage_ledger partitions will NOT be created automatically.  "
            "You must create new monthly partitions manually or via a scheduled task "
            "before each month begins."
        )
        op.execute(f"""
            DO $$
            DECLARE
                i           INT;
                month_start TIMESTAMPTZ;
                month_end   TIMESTAMPTZ;
                part_name   TEXT;
            BEGIN
                FOR i IN 0 .. {_FALLBACK_PARTITION_COUNT - 1} LOOP
                    month_start := date_trunc('month', now() + (i || ' months')::interval);
                    month_end   := month_start + INTERVAL '1 month';
                    part_name   := format(
                        'token_usage_ledger_%s',
                        to_char(month_start, 'YYYYMM')
                    );
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS public.%I '
                        'PARTITION OF public.token_usage_ledger '
                        'FOR VALUES FROM (%L) TO (%L)',
                        part_name,
                        month_start,
                        month_end
                    );
                END LOOP;
            END
            $$
        """)

    # --------------------------------------------------------------------- #
    # 7. Seed default catalog entries from model_catalog_defaults.toml
    # --------------------------------------------------------------------- #
    seed_entries = _load_seed_entries()
    if seed_entries:
        seed_sql = sa.text(
            "INSERT INTO public.model_catalog"
            " (alias, runtime_type, model_id, extra_args,"
            "  complexity_tier, priority, enabled)"
            " VALUES"
            " (:alias, :runtime_type, :model_id,"
            " CAST(:extra_args AS jsonb), :complexity_tier, :priority, :enabled)"
            " ON CONFLICT (alias) DO NOTHING"
        )
        seed_params = [
            {
                "alias": entry["alias"],
                "runtime_type": entry["runtime_type"],
                "model_id": entry["model_id"],
                "extra_args": json.dumps(entry.get("extra_args", [])),
                "complexity_tier": entry.get("complexity_tier", "medium"),
                "priority": entry.get("priority", 0),
                "enabled": entry.get("enabled", True),
            }
            for entry in seed_entries
        ]
        op.get_bind().execute(seed_sql, seed_params)

    # --------------------------------------------------------------------- #
    # 8. Grants
    # --------------------------------------------------------------------- #
    for table in _PUBLIC_TABLES:
        for role in _ALL_BUTLER_ROLES:
            _grant_best_effort(table, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    # Deregister pg_partman configuration before dropping the table.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
                DELETE FROM partman.part_config
                WHERE parent_table = 'public.token_usage_ledger';
            END IF;
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_schema THEN NULL;
        END
        $$
    """)

    op.execute("DROP INDEX IF EXISTS public.idx_ledger_entry_time")
    op.execute("DROP TABLE IF EXISTS public.token_limits")
    op.execute("DROP TABLE IF EXISTS public.token_usage_ledger CASCADE")
    op.execute("DROP INDEX IF EXISTS public.idx_provider_config_enabled")
    op.execute("DROP TABLE IF EXISTS public.provider_config")
    op.execute("DROP INDEX IF EXISTS public.idx_butler_model_overrides_butler_name")
    op.execute("DROP INDEX IF EXISTS public.idx_model_catalog_complexity_tier_enabled")
    op.execute("DROP TABLE IF EXISTS public.butler_model_overrides")
    op.execute("DROP TABLE IF EXISTS public.model_catalog")
