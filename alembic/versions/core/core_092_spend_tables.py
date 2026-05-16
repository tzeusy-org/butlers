"""spend_tables: add public.spend_rules and public.spend_ceiling.

Revision ID: core_092
Revises: core_091
Create Date: 2026-05-16 00:00:00.000000

Adds two tables supporting the §5.0–5.2 spend dashboard:

  public.spend_rules  — ordered routing rules (condition→action, position-sorted)
  public.spend_ceiling — singleton monthly USD ceiling (id=1 always)

"""

from __future__ import annotations

from alembic import op

revision = "core_092"
down_revision = "core_091"
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
    # spend_rules: ordered routing rules evaluated top-to-bottom by the runtime.
    # position is unique (partial unique index allows gaps to be compacted by the
    # API layer on insert/delete; the API always maintains dense ordering).
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.spend_rules (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            position   INT NOT NULL,
            condition  JSONB NOT NULL DEFAULT '{}'::jsonb,
            action     JSONB NOT NULL DEFAULT '{}'::jsonb,
            saved_7d   NUMERIC(12, 6),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_spend_rules_position
        ON public.spend_rules (position)
    """)

    # spend_ceiling: singleton row (id=1) storing the monthly USD limit.
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.spend_ceiling (
            id          INT PRIMARY KEY DEFAULT 1,
            monthly_usd NUMERIC(12, 6) NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT spend_ceiling_single_row CHECK (id = 1)
        )
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.spend_rules", _TABLE_PRIVILEGES, role)
        _grant_best_effort("public.spend_ceiling", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_spend_rules_position")
    op.execute("DROP TABLE IF EXISTS public.spend_rules")
    op.execute("DROP TABLE IF EXISTS public.spend_ceiling")
