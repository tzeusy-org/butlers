"""webhooks: add secret_prefix for human identification.

Revision ID: core_146
Revises: core_145
Create Date: 2026-06-27 00:00:00.000000

The webhook signing secret is now generated server-side and returned exactly
once at create / regenerate time (it is stored encrypted with AES-256-GCM and
never echoed again).  To let the dashboard identify a webhook's secret without
ever exposing the plaintext, this migration adds a ``secret_prefix TEXT`` column
holding the first 6 characters of the secret followed by an ellipsis
(e.g. ``"a1B2c3…"``).

Existing rows
-------------
Rows created before this migration have ``secret_prefix = NULL``.  The
application treats NULL as "no prefix available"; re-supplying a secret via
``PUT /api/webhooks/{id} {regenerate_secret: true}`` populates it.
"""

from __future__ import annotations

from alembic import op

revision = "core_146"
down_revision = "core_145"
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
    op.execute("ALTER TABLE public.webhooks ADD COLUMN IF NOT EXISTS secret_prefix TEXT")

    # Grants (table-level privileges already cover the new column).
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.webhooks", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS public.webhooks DROP COLUMN IF EXISTS secret_prefix")
