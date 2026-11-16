"""The owner's switch for raw location expiry, off by default (bu-s11n0s.75, slice S5a).

Revision ID: chronicler_041
Revises: chronicler_040

``location_retention_policy.enforced`` is the owner's single act that turns raw OwnTracks expiry
on. While it is false, Chronicler prepares no retention grant and offers none to the connector,
so nothing is deleted; the owner sets it through the same compare-and-set ``PUT`` as the horizon.

``NOT NULL DEFAULT false``: an upgrade never turns expiry on. A re-run finds the column and
changes nothing. The downgrade refuses while enforcement is on (dropping the column would lose
the owner's decision) and otherwise drops only the column.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_041"
down_revision = "chronicler_040"
branch_labels = None
depends_on = None

_POLICY = "location_retention_policy"


def upgrade() -> None:
    installed = (
        op.get_bind()
        .execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute "
                f"WHERE attrelid = to_regclass('{_POLICY}') AND attname = 'enforced' "
                "AND NOT attisdropped)"
            )
        )
        .scalar()
    )
    if installed:
        return
    op.execute(f"ALTER TABLE {_POLICY} ADD COLUMN enforced BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass('{_POLICY}') AND attname = 'enforced'
                  AND NOT attisdropped
            ) AND EXISTS (SELECT 1 FROM {_POLICY} WHERE enforced) THEN
                RAISE EXCEPTION 'location retention enforcement is on; not dropping the switch'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE {_POLICY} DROP COLUMN IF EXISTS enforced")
