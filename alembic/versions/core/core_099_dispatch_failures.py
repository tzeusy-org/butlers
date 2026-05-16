"""dispatch_failures: create public.dispatch_failures table.

Revision ID: core_099
Revises: core_098
Create Date: 2026-05-16 00:00:00.000000

Tracks per-model dispatch failures so that ``GET /api/settings/models/{id}/failures``
returns real rows rather than an empty page.

A row is written whenever the spawner encounters an exception during
``_run()`` and a catalog entry was resolved (``catalog_entry_id`` is not None).
Failure rows are informational: the spawner write is best-effort and never
raises if the insert fails.

Schema
------
id               BIGSERIAL PK
catalog_entry_id UUID NOT NULL FK → public.model_catalog(id) ON DELETE CASCADE
ts               TIMESTAMPTZ NOT NULL DEFAULT now()
error_code       TEXT                -- short slug, e.g. "TimeoutError"
error_message    TEXT                -- full error string (truncated to 4096 chars)
butler           TEXT                -- butler name
session_id       UUID                -- session id (if one was created)
"""

from __future__ import annotations

from alembic import op

revision = "core_099"
down_revision = "core_098"
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
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.dispatch_failures (
            id               BIGSERIAL    PRIMARY KEY,
            catalog_entry_id UUID         NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            ts               TIMESTAMPTZ  NOT NULL DEFAULT now(),
            error_code       TEXT,
            error_message    TEXT,
            butler           TEXT,
            session_id       UUID
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispatch_failures_catalog_entry_ts
        ON public.dispatch_failures (catalog_entry_id, ts DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.dispatch_failures", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_dispatch_failures_catalog_entry_ts")
    op.execute("DROP TABLE IF EXISTS public.dispatch_failures")
