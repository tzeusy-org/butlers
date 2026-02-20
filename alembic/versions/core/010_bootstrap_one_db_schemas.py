"""bootstrap one-db shared and per-butler schemas

Revision ID: core_010
Revises: core_009
Create Date: 2026-02-20 00:30:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_010"
down_revision = "core_009"
branch_labels = None
depends_on = None

_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_REQUIRED_SCHEMAS = ("shared", *_BUTLER_SCHEMAS)


def _quote_ident(identifier: str) -> str:
    """Return a safely quoted SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _set_schema_owner_best_effort(schema: str) -> None:
    """Set schema owner to current migration role when permitted."""
    quoted_schema = _quote_ident(schema)
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE 'ALTER SCHEMA {quoted_schema} OWNER TO CURRENT_USER';
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    for schema in _REQUIRED_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        _set_schema_owner_best_effort(schema)


def downgrade() -> None:
    # Drop only when empty. If later revisions have materialized objects in these
    # schemas, keep those schemas in place and continue.
    for schema in reversed(_REQUIRED_SCHEMAS):
        quoted_schema = _quote_ident(schema)
        op.execute(
            f"""
            DO $$
            BEGIN
                EXECUTE 'DROP SCHEMA IF EXISTS {quoted_schema}';
            EXCEPTION
                WHEN dependent_objects_still_exist THEN
                    NULL;
                WHEN insufficient_privilege THEN
                    NULL;
            END
            $$;
            """
        )
