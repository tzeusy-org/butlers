"""When Chronicler last asked a holder about a retention grant (bu-s11n0s.7, slice S4c-3).

Revision ID: chronicler_040
Revises: chronicler_039

Chronicler's holder collector asks the least recently asked grants first. A failed ask (Switchboard
unavailable, a refusal, a malformed reply) records no receipt, so ordering by the last receipt
left a persistently failing grant first in every run. ``location_retention_holder_asks`` keeps
one row per grant and holder with the time of the latest ask, written before the ask is made, so
a grant that just failed goes to the back.

It is scheduling state, not evidence: rows are updated in place and the downgrade drops the table.
``REVOKE ALL ... FROM PUBLIC`` and from ``butler_chronicler_rw``, then ``SELECT, INSERT, UPDATE``
to that role. A re-run finds the table in the catalog and changes nothing.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_040"
down_revision = "chronicler_039"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_GRANTS = "location_retention_grants"
_ASKS = "location_retention_holder_asks"


def upgrade() -> None:
    installed = op.get_bind().execute(text(f"SELECT to_regclass('{_ASKS}') IS NOT NULL")).scalar()
    if installed:
        return
    op.execute(
        f"""
        CREATE TABLE {_ASKS} (
            grant_id UUID NOT NULL REFERENCES {_GRANTS} (id),
            holder TEXT NOT NULL CHECK (holder ~ '^[a-z_]+\\.[a-z_]+$'),
            asked_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (grant_id, holder)
        )
        """
    )
    op.execute(f"REVOKE ALL ON {_ASKS} FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_ASKS} TO {_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_ASKS}")
