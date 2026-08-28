"""Grant Chronicler read-only access to Health memory facts.

Revision ID: mem_011
Revises: mem_010
"""

from __future__ import annotations

from alembic import op

revision = "mem_011"
down_revision = "mem_010"
branch_labels = None
depends_on = None

_CHRONICLER_ROLE = "butler_chronicler_rw"
_HEALTH_SCHEMA = "health"
_FACTS_TABLE = "facts"


def _apply_health_facts_privilege(verb: str, preposition: str) -> None:
    """Apply one table privilege only when this is the supported Health surface."""
    target_role = _CHRONICLER_ROLE.replace("'", "''")
    op.execute(
        f"""
        DO $$
        DECLARE
            target_schema TEXT := current_schema();
            target_role TEXT := '{target_role}';
        BEGIN
            IF target_schema <> '{_HEALTH_SCHEMA}'
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role)
               OR to_regclass(format('%I.%I', target_schema, '{_FACTS_TABLE}')) IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                '{verb} SELECT ON TABLE %I.%I {preposition} %I',
                target_schema,
                '{_FACTS_TABLE}',
                target_role
            );
        END
        $$;
        """
    )


def upgrade() -> None:
    """Grant the required source-table read privilege in the target schema."""
    _apply_health_facts_privilege("GRANT", "TO")


def downgrade() -> None:
    """Remove the source-table read privilege from the target schema."""
    _apply_health_facts_privilege("REVOKE", "FROM")
