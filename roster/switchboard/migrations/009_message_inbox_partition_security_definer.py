"""Run message_inbox partition maintenance as the table owner.

Revision ID: sw_009
Revises: sw_008
Create Date: 2026-05-01 00:00:00.000000

The switchboard runtime role can insert into ``message_inbox`` but does not own
the partitioned parent table.  Monthly partition creation/drop DDL therefore
has to execute as the migration/table owner via SECURITY DEFINER functions.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_009"
down_revision = "sw_008"
branch_labels = None
depends_on = None


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _function_search_path() -> str:
    bind = op.get_bind()
    if bind is None:
        # Offline mode (alembic upgrade --sql): no live connection. Fall back to
        # the well-known target schema for this branch.
        return _quote_ident("switchboard") + ", pg_temp"
    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    parts = [_quote_ident(str(schema)), "pg_temp"]
    return ", ".join(dict.fromkeys(parts))


def upgrade() -> None:
    function_search_path = _function_search_path()
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)
        SECURITY DEFINER
        """
    )
    op.execute(
        f"""
        ALTER FUNCTION switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)
        SET search_path TO {function_search_path}
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_drop_expired_partitions(INTERVAL, TIMESTAMPTZ)
        SECURITY DEFINER
        """
    )
    op.execute(
        f"""
        ALTER FUNCTION switchboard_message_inbox_drop_expired_partitions(INTERVAL, TIMESTAMPTZ)
        SET search_path TO {function_search_path}
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_drop_expired_partitions(INTERVAL, TIMESTAMPTZ)
        SECURITY INVOKER
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_drop_expired_partitions(INTERVAL, TIMESTAMPTZ)
        RESET search_path
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)
        SECURITY INVOKER
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_message_inbox_ensure_partition(TIMESTAMPTZ)
        RESET search_path
        """
    )
