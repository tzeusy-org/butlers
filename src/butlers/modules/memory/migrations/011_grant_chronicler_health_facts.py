"""Grant Chronicler read access to the Health memory facts evidence surface.

Revision ID: mem_011
Revises: mem_010

The Google Health Chronicler adapters read ``health.facts`` as their approved,
read-only evidence surface. Core migrations run before memory migrations, so a
core-only grant can miss this table on a fresh install. This memory-chain
migration runs after ``mem_001`` creates ``facts`` and performs the narrowly
scoped grant only when the target schema is ``health``.

RFC 0006 schema isolation and RFC 0014 require named, migration-tracked
evidence surfaces. Do not replace this with a blanket schema/table grant.
"""

from __future__ import annotations

from alembic import op

revision = "mem_011"
down_revision = "mem_010"
branch_labels = None
depends_on = None

_CHRONICLER_ROLE = "butler_chronicler_rw"
_TARGET_SCHEMA = "health"
_TABLE = "health.facts"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_for_health_target(statement: str, *, role_name: str) -> None:
    """Run a table ACL change only in the Health memory migration target."""
    condition = (
        f"current_schema() = {_quote_literal(_TARGET_SCHEMA)} "
        f"AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)}) "
        f"AND to_regclass({_quote_literal(_TABLE)}) IS NOT NULL"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                {statement};
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _execute_for_health_target(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )


def downgrade() -> None:
    _execute_for_health_target(
        f"REVOKE SELECT ON TABLE {_TABLE} FROM {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )
