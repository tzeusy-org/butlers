"""Grant relationship runtime read access to switchboard tables.

Revision ID: core_077
Revises: core_076
Create Date: 2026-04-16 00:00:00.000000

Repairs existing databases where the relationship runtime role needs to read
``switchboard.message_inbox`` for interaction sync but lacks cross-schema ACLs.
Fresh installs also benefit because the default privilege grant ensures future
switchboard tables created by the migration user remain readable.
"""

from __future__ import annotations

from alembic import op

revision = "core_077"
down_revision = "core_076"
branch_labels = None
depends_on = None

_ROLE_NAME = "butler_relationship_rw"
_SWITCHBOARD_SCHEMA = "switchboard"


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
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    quoted_role = _quote_ident(_ROLE_NAME)
    quoted_schema = _quote_ident(_SWITCHBOARD_SCHEMA)

    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=_ROLE_NAME,
    )
    _execute_best_effort(
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=_ROLE_NAME,
    )
    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
        f"GRANT SELECT ON TABLES TO {quoted_role}",
        role_name=_ROLE_NAME,
    )


def downgrade() -> None:
    quoted_role = _quote_ident(_ROLE_NAME)
    quoted_schema = _quote_ident(_SWITCHBOARD_SCHEMA)

    _execute_best_effort(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
        f"REVOKE SELECT ON TABLES FROM {quoted_role}",
        role_name=_ROLE_NAME,
    )
    _execute_best_effort(
        f"REVOKE SELECT ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
        role_name=_ROLE_NAME,
    )
    _execute_best_effort(
        f"REVOKE USAGE ON SCHEMA {quoted_schema} FROM {quoted_role}",
        role_name=_ROLE_NAME,
    )
