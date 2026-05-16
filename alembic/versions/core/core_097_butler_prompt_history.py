"""butler prompt history: system_prompt_history and butler_tools tables.

Revision ID: core_097
Revises: core_096
Create Date: 2026-05-16 00:00:00.000000

Phase 7 of the settings-redesign epic.  Creates two public tables:

* ``public.system_prompt_history`` — versioned history of butler system prompts.
  Each PUT to the prompt endpoint snapshots the prior version here.
* ``public.butler_tools`` — per-butler tool grants and scopes.
  Controls which tools a butler is allowed to use and with what scope.
"""

from __future__ import annotations

from alembic import op

revision = "core_097"
down_revision = "core_096"
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
    # public.system_prompt_history
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.system_prompt_history (
            id           BIGSERIAL PRIMARY KEY,
            butler_name  TEXT        NOT NULL,
            prompt       TEXT        NOT NULL,
            version      INT         NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by   TEXT,
            UNIQUE (butler_name, version)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sph_butler_version
        ON public.system_prompt_history (butler_name, version DESC)
    """)

    # ------------------------------------------------------------------
    # public.butler_tools
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.butler_tools (
            butler_name  TEXT    NOT NULL,
            tool_name    TEXT    NOT NULL,
            description  TEXT,
            allowed      BOOL    NOT NULL DEFAULT TRUE,
            scope        TEXT,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by   TEXT,
            PRIMARY KEY (butler_name, tool_name)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_butler_tools_butler
        ON public.butler_tools (butler_name)
    """)

    for table_fqn in ("public.system_prompt_history", "public.butler_tools"):
        for role in _ALL_RUNTIME_ROLES:
            _grant_best_effort(table_fqn, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_butler_tools_butler")
    op.execute("DROP TABLE IF EXISTS public.butler_tools")
    op.execute("DROP INDEX IF EXISTS public.idx_sph_butler_version")
    op.execute("DROP TABLE IF EXISTS public.system_prompt_history")
