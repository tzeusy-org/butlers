"""self_healing: create public.healing_attempts table

Revision ID: core_005
Revises: core_004
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_035b, core_038.

Creates the tracking table for self-healing investigation lifecycle:

  public.healing_attempts
    - id, fingerprint, butler_name, status, severity, exception_type,
      call_site, sanitized_msg, branch_name, worktree_path, pr_url,
      pr_number, session_ids, healing_session_id, created_at, updated_at,
      closed_at, error_detail.
    - Index on fingerprint for novelty/cooldown lookups.
    - Index on status for concurrency-cap and circuit-breaker queries.
    - Partial UNIQUE index on fingerprint WHERE status IN
      ('dispatch_pending', 'investigating', 'pr_open') -- the atomic
      novelty gate (includes dispatch_pending from core_038).

Grants SELECT, INSERT, UPDATE, DELETE to all butler roles.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_005"
down_revision = "core_004"
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    # --------------------------------------------------------------------- #
    # 1. public.healing_attempts
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.healing_attempts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint         TEXT NOT NULL,
            butler_name         TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'investigating',
            severity            INTEGER NOT NULL,
            exception_type      TEXT NOT NULL,
            call_site           TEXT NOT NULL,
            sanitized_msg       TEXT,
            branch_name         TEXT,
            worktree_path       TEXT,
            pr_url              TEXT,
            pr_number           INTEGER,
            session_ids         UUID[] NOT NULL DEFAULT '{}',
            healing_session_id  UUID,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at           TIMESTAMPTZ,
            error_detail        TEXT
        )
    """)

    # Index on fingerprint for novelty gate and cooldown lookups.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_fingerprint
        ON public.healing_attempts (fingerprint)
    """)

    # Index on status for concurrency-cap and circuit-breaker queries.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_status
        ON public.healing_attempts (status)
    """)

    # Partial unique index: at most one active investigation per fingerprint.
    # Includes dispatch_pending (from core_038) in the novelty gate.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_healing_attempts_active_fingerprint
        ON public.healing_attempts (fingerprint)
        WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
    """)

    # --------------------------------------------------------------------- #
    # 2. Grants
    # --------------------------------------------------------------------- #
    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.healing_attempts", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_healing_attempts_active_fingerprint")
    op.execute("DROP INDEX IF EXISTS public.idx_healing_attempts_status")
    op.execute("DROP INDEX IF EXISTS public.idx_healing_attempts_fingerprint")
    op.execute("DROP TABLE IF EXISTS public.healing_attempts")
