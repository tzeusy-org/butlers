"""Holder receipts for prepared retention grants (bu-s11n0s.7, slice S4c-1).

Revision ID: chronicler_039
Revises: chronicler_038

A prepared grant may become ``ready`` (and so authorize the connector to delete raw rows) only
after every other holder of a copy derived from those rows has forgotten it under its own role
and Chronicler has read that back. ``location_retention_holder_receipts`` records what Chronicler
read back, one row per reading:

* ``complete``: the holder forgot every copy it holds for the grant. At most once per grant and
  holder.
* ``blocked``: the holder could not forget a copy (still processing, routed onward, or an
  ambiguous legacy copy). It may repeat while the block persists, and never counts as forgotten.

``receipt`` holds count-only evidence (a JSON object, never coordinates or payloads). Nothing
writes this table yet.

The table reuses chronicler_036's append-only guard and grant shape: ``REVOKE ALL ... FROM
PUBLIC`` and from ``butler_chronicler_rw``, then ``SELECT, INSERT`` to that role. A re-run finds
the table in the catalog and changes nothing. Downgrade refuses while any receipt is recorded.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_039"
down_revision = "chronicler_038"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_GRANTS = "location_retention_grants"
_RECEIPTS = "location_retention_holder_receipts"
_GUARD = "chronicler.location_retention_grant_append_only()"


def upgrade() -> None:
    installed = (
        op.get_bind().execute(text(f"SELECT to_regclass('{_RECEIPTS}') IS NOT NULL")).scalar()
    )
    if installed:
        return
    op.execute(
        f"""
        CREATE TABLE {_RECEIPTS} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            grant_id UUID NOT NULL REFERENCES {_GRANTS} (id),
            holder TEXT NOT NULL CHECK (holder ~ '^[a-z_]+\\.[a-z_]+$'),
            outcome TEXT NOT NULL CHECK (outcome IN ('complete', 'blocked')),
            receipt JSONB NOT NULL DEFAULT '{{}}'::jsonb
                CHECK (jsonb_typeof(receipt) = 'object'),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_RECEIPTS}_complete_once
        ON {_RECEIPTS} (grant_id, holder) WHERE outcome = 'complete'
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_RECEIPTS}_append_only
        BEFORE UPDATE OR DELETE ON {_RECEIPTS}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}
        """
    )
    op.execute(f"REVOKE ALL ON {_RECEIPTS} FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON {_RECEIPTS} TO {_ROLE}")


def downgrade() -> None:
    # A recorded receipt is evidence of a privacy decision's progress: refuse to drop it.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_RECEIPTS}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_RECEIPTS}) THEN
                RAISE EXCEPTION 'location retention holder receipts are recorded; not dropping them'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_RECEIPTS}")
