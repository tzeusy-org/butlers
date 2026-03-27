"""insight_tables: public schema tables for the proactive insight engine

Revision ID: core_010
Revises: core_009
Create Date: 2026-03-27 00:00:00.000000

Creates four tables in the ``public`` schema for the proactive insight engine
(spec: openspec/specs/proactive-insight-engine/spec.md):

  public.insight_candidates  — pending/delivered/filtered/expired insight proposals.
  public.insight_cooldowns   — per dedup_key cooldown windows (prevents re-delivery).
  public.insight_engagement  — per-delivery engagement tracking for adaptive budget.
  public.insight_settings    — single-row user preferences (verbosity, quiet hours).

Design notes:
  - insight_candidates uses a UUID PK; all status transitions are uni-directional.
  - insight_cooldowns is keyed on dedup_key (TEXT); one active cooldown per key.
  - insight_engagement has a FK to insight_candidates (insight_id).
  - insight_settings enforces a single-row design via id INTEGER PRIMARY KEY
    DEFAULT 1 with a CHECK constraint.
  - All DDL is guarded with IF (NOT) EXISTS for idempotency.
  - Grants follow the narrow-privilege pattern: switchboard (broker) gets full
    DML; other butlers get INSERT on candidates only (they submit via Switchboard).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_010"
down_revision = "core_009"
branch_labels = None
depends_on = None

# The Switchboard is the sole writer/manager of the insight broker tables.
_SWITCHBOARD_ROLE = "butler_switchboard_rw"

# All butler roles: they may INSERT candidates (submitted via Switchboard MCP,
# but granting INSERT lets the broker module operate under the switchboard role).
_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

# Switchboard owns full DML on all insight tables.
_SWITCHBOARD_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
# Other butlers only need to INSERT candidates through the propose tool.
_BUTLER_CANDIDATES_PRIVILEGES = "INSERT"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
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


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA only when schema and role exist."""
    safe_schema = schema.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{safe_schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {_quote_ident(schema)} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # =========================================================================
    # 1. public.insight_candidates
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.insight_candidates (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            origin_butler   TEXT NOT NULL,
            priority        INTEGER NOT NULL,
            category        TEXT NOT NULL,
            dedup_key       TEXT NOT NULL,
            cooldown_days   INTEGER,
            expires_at      TIMESTAMPTZ NOT NULL,
            message         TEXT NOT NULL,
            channel         TEXT,
            metadata        JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            status          TEXT NOT NULL DEFAULT 'pending',
            delivered_at    TIMESTAMPTZ,
            CONSTRAINT chk_insight_candidates_priority
                CHECK (priority BETWEEN 1 AND 100),
            CONSTRAINT chk_insight_candidates_status
                CHECK (status IN ('pending', 'delivered', 'expired', 'filtered')),
            CONSTRAINT chk_insight_candidates_dedup_key_nonempty
                CHECK (dedup_key <> ''),
            CONSTRAINT chk_insight_candidates_message_nonempty
                CHECK (message <> '')
        )
    """)

    # Index: delivery cycle query — pending candidates ordered by priority.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_candidates_status_priority
        ON public.insight_candidates (status, priority DESC, created_at ASC)
        WHERE status = 'pending'
    """)

    # Index: expiry sweep.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_candidates_expires_at
        ON public.insight_candidates (expires_at)
        WHERE status = 'pending'
    """)

    # Index: dedup_key lookup (per-key uniqueness checks during delivery cycle).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_candidates_dedup_key
        ON public.insight_candidates (dedup_key, status)
    """)

    # Index: cleanup sweep — non-pending rows older than 30 days.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_candidates_cleanup
        ON public.insight_candidates (created_at)
        WHERE status <> 'pending'
    """)

    # Index: engagement rate — delivered rows within rolling window.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_candidates_delivered_at
        ON public.insight_candidates (delivered_at)
        WHERE delivered_at IS NOT NULL
    """)

    # =========================================================================
    # 2. public.insight_cooldowns
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.insight_cooldowns (
            dedup_key       TEXT PRIMARY KEY,
            cooldown_until  TIMESTAMPTZ NOT NULL,
            reason          TEXT NOT NULL DEFAULT 'delivered',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_insight_cooldowns_dedup_key_nonempty
                CHECK (dedup_key <> '')
        )
    """)

    # Index: active-cooldown filter (delivery cycle skips active cooldowns).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_cooldowns_until
        ON public.insight_cooldowns (cooldown_until)
    """)

    # =========================================================================
    # 3. public.insight_engagement
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.insight_engagement (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            insight_id      UUID NOT NULL
                REFERENCES public.insight_candidates(id) ON DELETE CASCADE,
            delivered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            engaged         BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)

    # Index: rolling engagement rate — rows within last 14 days.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_engagement_delivered_at
        ON public.insight_engagement (delivered_at DESC)
    """)

    # Index: cleanup sweep (plain index — partial predicate with now() would be
    # evaluated once at CREATE INDEX time and become a stale constant immediately).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_engagement_cleanup
        ON public.insight_engagement (delivered_at)
    """)

    # =========================================================================
    # 4. public.insight_settings
    # =========================================================================
    # Single-row design: id is always 1.
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.insight_settings (
            id              INTEGER PRIMARY KEY DEFAULT 1,
            verbosity       TEXT NOT NULL DEFAULT 'minimal',
            custom_budget   INTEGER,
            quiet_start     INTEGER,
            quiet_end       INTEGER,
            quiet_timezone  TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_insight_settings_single_row
                CHECK (id = 1),
            CONSTRAINT chk_insight_settings_verbosity
                CHECK (verbosity IN ('off', 'minimal', 'normal', 'verbose')),
            CONSTRAINT chk_insight_settings_custom_budget
                CHECK (custom_budget IS NULL OR custom_budget BETWEEN 1 AND 10),
            CONSTRAINT chk_insight_settings_quiet_start
                CHECK (quiet_start IS NULL OR quiet_start BETWEEN 0 AND 23),
            CONSTRAINT chk_insight_settings_quiet_end
                CHECK (quiet_end IS NULL OR quiet_end BETWEEN 0 AND 23)
        )
    """)

    # Seed default settings row.
    op.execute("""
        INSERT INTO public.insight_settings (id, verbosity)
        VALUES (1, 'minimal')
        ON CONFLICT (id) DO NOTHING
    """)

    # =========================================================================
    # 5. Grant privileges
    # =========================================================================

    # Switchboard gets full DML on all four tables.
    for table in (
        "public.insight_candidates",
        "public.insight_cooldowns",
        "public.insight_engagement",
        "public.insight_settings",
    ):
        _grant_if_table_exists(table, _SWITCHBOARD_PRIVILEGES, _SWITCHBOARD_ROLE)

    # All butler roles get USAGE on public schema (many already have it from
    # prior migrations; this is idempotent).
    for role in _ALL_BUTLER_ROLES:
        _grant_schema_usage_if_exists("public", role)

    # Non-switchboard butlers get INSERT on insight_candidates only (they
    # submit via the Switchboard MCP tool which runs under switchboard role).
    for role in _ALL_BUTLER_ROLES:
        if role != _SWITCHBOARD_ROLE:
            _grant_if_table_exists("public.insight_candidates", _BUTLER_CANDIDATES_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.insight_engagement CASCADE")
    op.execute("DROP TABLE IF EXISTS public.insight_cooldowns CASCADE")
    op.execute("DROP TABLE IF EXISTS public.insight_candidates CASCADE")
    op.execute("DROP TABLE IF EXISTS public.insight_settings CASCADE")
