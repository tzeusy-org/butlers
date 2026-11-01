"""Grant Home read access to durable HA transitions, without granting writes.

Revision ID: core_289
Revises: core_288

Bootstrap may already supply SELECT through migration-role defaults. This
explicit per-table grant also converges older installations. Missing roles or
tables are valid on core-only installations; this migration creates neither.
"""

from alembic import op

revision = "core_289"
down_revision = "core_288"
branch_labels = None
depends_on = None

_ROLE = "butler_home_rw"


def _set_read_grant(*, enabled: bool) -> None:
    role_literal = "'" + _ROLE.replace("'", "''") + "'"
    role_identifier = '"' + _ROLE.replace('"', '""') + '"'
    statement = (
        f"GRANT SELECT ON TABLE connectors.home_assistant_history TO {role_identifier}"
        if enabled
        else f"REVOKE SELECT ON TABLE connectors.home_assistant_history FROM {role_identifier}"
    )
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role_literal})
               AND to_regclass('connectors.home_assistant_history') IS NOT NULL THEN
                {statement};
            END IF;
        END;
        $$
    """)


def upgrade() -> None:
    _set_read_grant(enabled=True)


def downgrade() -> None:
    _set_read_grant(enabled=False)
