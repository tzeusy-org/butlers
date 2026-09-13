"""Allow attempt cleanup only through a presentation-lifetime cascade.

Revision ID: approvals_016
Revises: approvals_015
Create Date: 2026-09-14 00:00:00.000000

Direct attempt mutation remains forbidden. PostgreSQL's parent foreign-key
cascade may remove attempt rows only when their presentation lifetime ends.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_016"
down_revision = "approvals_015"
branch_labels = None
depends_on = None

_HISTORICAL_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION prevent_approval_delivery_attempts_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'approval_delivery_attempts is append-only: % is not allowed', TG_OP;
END $$
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_approval_delivery_attempts_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'approval_delivery_attempts is append-only: % is not allowed', TG_OP;
        END $$
        """
    )


def downgrade() -> None:
    """Restore approvals_015's fail-closed direct and cascade refusal."""
    op.execute(_HISTORICAL_FUNCTION_SQL)
