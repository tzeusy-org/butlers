"""channel_defaults: per-channel default policy table.

Revision ID: core_102
Revises: core_101
Create Date: 2026-05-18 00:00:00.000000

Phase 3d (bu-1f91v.9).  Creates ``public.channel_defaults`` — the DB-backed
store for per-channel default ingestion policy documents.

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      ingestion-ui-information-architecture/spec.md
      §"Channel defaults data model and REST API"

Schema
------
channel              TEXT          PRIMARY KEY (e.g. 'email', 'telegram')
default_policy_json  JSONB         NOT NULL — opaque per-channel policy
updated_at           TIMESTAMPTZ   NOT NULL DEFAULT now()
updated_by           TEXT          NOT NULL

Retention
---------
No TTL; entries persist indefinitely until explicitly overwritten by PATCH.
There is no DELETE surface exposed at the API layer.

Grants
------
SELECT, INSERT, UPDATE, DELETE on channel_defaults granted to all runtime roles.
"""

from __future__ import annotations

from alembic import op

revision = "core_102"
down_revision = "core_101"
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
        CREATE TABLE IF NOT EXISTS public.channel_defaults (
            channel              TEXT        PRIMARY KEY,
            default_policy_json  JSONB       NOT NULL,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by           TEXT        NOT NULL
        )
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.channel_defaults", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.channel_defaults")
