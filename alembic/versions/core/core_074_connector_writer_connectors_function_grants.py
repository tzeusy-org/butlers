"""Repair connector_writer execute grants for connectors schema functions.

Revision ID: core_074
Revises: core_073
Create Date: 2026-04-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_074"
down_revision = "core_073"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
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
    quoted_role = _quote_ident(_CONNECTOR_ROLE)
    quoted_schema = _quote_ident("connectors")
    _execute_best_effort(
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema}"
        f" GRANT EXECUTE ON FUNCTIONS TO {quoted_role}",
        role_name=_CONNECTOR_ROLE,
    )


def downgrade() -> None:
    quoted_role = _quote_ident(_CONNECTOR_ROLE)
    quoted_schema = _quote_ident("connectors")
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema}"
        f" REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM {quoted_role}",
        role_name=_CONNECTOR_ROLE,
    )
