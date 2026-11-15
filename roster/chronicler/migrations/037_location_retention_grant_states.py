"""Later retention grant states, appended (bu-s11n0s.7, slice 4b).

Revision ID: chronicler_037
Revises: chronicler_036

A grant is written ``prepared`` (chronicler_036) and never changes. Every later state it
reaches is a new row here, so the history of a privacy decision is itself append-only:

* ``ready``: every holder copy has been read back and the connector may delete the grant's raw
  rows. At most once per grant.
* ``unknown``: a deletion was attempted but its committed receipt could not be read. May repeat
  while reconciliation retries; it never claims the rows are gone.
* ``reconciled``: the connector's committed receipt matched the grant. At most once.
* ``failed``: the receipt did not match, or the grant can no longer be honoured. At most once.

Absence of ``ready`` means the grant is withheld. ``receipt`` holds the state's count-only
evidence (a JSON object, never coordinates or payloads). Nothing writes this table yet.

The table reuses chronicler_036's append-only guard (an init-db replay re-grants schema-wide
DML, so a grant alone would not hold) and its grant shape: ``REVOKE ALL ... FROM PUBLIC`` and
from ``butler_chronicler_rw``, then ``SELECT, INSERT`` to that role. A re-run finds the table
in the catalog and changes nothing.

Downgrade refuses while any state is recorded (each is evidence of a privacy decision's
outcome); an empty table is dropped, keeping chronicler_036's tables and guard.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_037"
down_revision = "chronicler_036"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_GRANTS = "location_retention_grants"
_STATES = "location_retention_grant_states"
_GUARD = "chronicler.location_retention_grant_append_only()"


def upgrade() -> None:
    # The revision's DDL is one transaction, so the table exists only together with its index,
    # trigger and grants: a re-run only reads the catalog.
    installed = op.get_bind().execute(text(f"SELECT to_regclass('{_STATES}') IS NOT NULL")).scalar()
    if installed:
        return
    op.execute(
        f"""
        CREATE TABLE {_STATES} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            grant_id UUID NOT NULL REFERENCES {_GRANTS} (id),
            state TEXT NOT NULL
                CHECK (state IN ('ready', 'unknown', 'reconciled', 'failed')),
            receipt JSONB NOT NULL DEFAULT '{{}}'::jsonb
                CHECK (jsonb_typeof(receipt) = 'object'),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_STATES}_once
        ON {_STATES} (grant_id, state) WHERE state <> 'unknown'
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_STATES}_append_only
        BEFORE UPDATE OR DELETE ON {_STATES}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}
        """
    )
    op.execute(f"REVOKE ALL ON {_STATES} FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON {_STATES} TO {_ROLE}")


def downgrade() -> None:
    # A recorded state is evidence of a privacy decision's outcome: refuse to drop it.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_STATES}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_STATES}) THEN
                RAISE EXCEPTION 'location retention grant states are recorded; not dropping them'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_STATES}")
