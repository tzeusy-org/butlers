"""connector entity_facts read grant for policy lookups.

Revision ID: rel_033
Revises: rel_032
Create Date: 2026-07-06 00:00:00.000000

The Gmail connector's known-contact policy tier is specified to load
``public.priority_contacts`` joined to ``relationship.entity_facts``. Connector
pools run under ``SET ROLE connector_writer``; that role already has access to
``public.priority_contacts`` but did not have explicit read access to the
relationship fact store, so Gmail startup logged ``permission denied for schema
relationship`` and retained an empty priority-contact cache.

Grant only the read surface the connector contract permits: schema USAGE plus
SELECT on ``relationship.entity_facts``. Do not grant write privileges.
"""

from __future__ import annotations

from alembic import op

revision = "rel_033"
down_revision = "rel_032"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_SCHEMA = "relationship"
_TABLE = "relationship.entity_facts"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str) -> None:
    role_exists = (
        f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CONNECTOR_ROLE)})"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {role_exists} THEN
                {statement};
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
    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA {_quote_ident(_SCHEMA)} TO {_quote_ident(_CONNECTOR_ROLE)}"
    )
    _execute_best_effort(f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CONNECTOR_ROLE)}")


def downgrade() -> None:
    _execute_best_effort(f"REVOKE SELECT ON TABLE {_TABLE} FROM {_quote_ident(_CONNECTOR_ROLE)}")
    _execute_best_effort(
        f"REVOKE USAGE ON SCHEMA {_quote_ident(_SCHEMA)} FROM {_quote_ident(_CONNECTOR_ROLE)}"
    )
