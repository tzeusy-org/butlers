"""permissions, webhooks, approvals_policy tables.

Revision ID: core_095
Revises: core_094
Create Date: 2026-05-16 00:00:00.000000

Phase 4 of the settings-redesign epic.  Creates three public tables:

* ``public.permissions`` — per-butler permission grants with audit trail.
* ``public.webhooks``    — outbound webhook registrations.
* ``public.approvals_policy`` — singleton quiet-hours policy for approval
  notifications (landed here for migration cohesion; consumed by Phase 6).
"""

from __future__ import annotations

from alembic import op

revision = "core_095"
down_revision = "core_094"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
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
    # ------------------------------------------------------------------
    # public.permissions
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.permissions (
            butler      TEXT    NOT NULL,
            permission  TEXT    NOT NULL,
            granted     BOOL    NOT NULL DEFAULT TRUE,
            reason      TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  TEXT,
            PRIMARY KEY (butler, permission)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_permissions_butler
        ON public.permissions (butler)
    """)

    # ------------------------------------------------------------------
    # public.webhooks
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.webhooks (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint       TEXT        NOT NULL,
            events         JSONB       NOT NULL DEFAULT '[]'::jsonb,
            enabled        BOOL        NOT NULL DEFAULT TRUE,
            secret_hash    TEXT,
            last_test_at   TIMESTAMPTZ,
            last_test_ok   BOOL,
            retry_policy   JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_webhooks_enabled
        ON public.webhooks (enabled)
    """)

    # ------------------------------------------------------------------
    # public.approvals_policy  (singleton: id=1)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.approvals_policy (
            id               INT  PRIMARY KEY DEFAULT 1,
            quiet_start_hour INT,
            quiet_end_hour   INT,
            timezone         TEXT NOT NULL DEFAULT 'UTC',
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approvals_policy_singleton CHECK (id = 1)
        )
    """)

    # Seed the singleton row so readers never encounter an empty table.
    op.execute("""
        INSERT INTO public.approvals_policy (id, timezone)
        VALUES (1, 'UTC')
        ON CONFLICT (id) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------
    for table in ("public.permissions", "public.webhooks", "public.approvals_policy"):
        for role in _ALL_RUNTIME_ROLES:
            _grant_best_effort(table, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_webhooks_enabled")
    op.execute("DROP INDEX IF EXISTS public.idx_permissions_butler")
    op.execute("DROP TABLE IF EXISTS public.approvals_policy")
    op.execute("DROP TABLE IF EXISTS public.webhooks")
    op.execute("DROP TABLE IF EXISTS public.permissions")
