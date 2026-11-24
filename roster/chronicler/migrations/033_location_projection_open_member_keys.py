"""Open coverage members carry a set of carry keys (bu-s11n0s.7, slice 2).

Revision ID: chronicler_033
Revises: chronicler_032

A raw OwnTracks fix can sit in several still-open carries of one adapter at once: the place
adapter keeps a fix both in an open place stay and in an open away-from-home trip, and closing
one must not release it while the other is still open. ``location_projection_open_members``
therefore replaces its single ``carry_key`` with ``carry_keys TEXT[]``, every carry that still
holds the row. A row leaves the table only when the set is empty.

Existing rows keep their one key (``ARRAY[carry_key]``). The table lives in this schema only and
this chain runs once, in the Chronicler schema, so nothing here touches another schema's
catalog. Grants are unchanged (SELECT, INSERT, DELETE for ``butler_chronicler_rw``; members are
rewritten by delete and insert, never updated).

Downgrade keeps the first key of each row, which is what chronicler_032's single-key reader
expects; a row held by two carries then waits only on the first.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_033"
down_revision = "chronicler_032"
branch_labels = None
depends_on = None

_TABLE = "location_projection_open_members"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS carry_keys TEXT[]")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = '{_TABLE}'
                  AND column_name = 'carry_key'
            ) THEN
                UPDATE {_TABLE} SET carry_keys = ARRAY[carry_key] WHERE carry_keys IS NULL;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN carry_keys SET NOT NULL")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_TABLE}_carry_keys_check")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT {_TABLE}_carry_keys_check
        CHECK (cardinality(carry_keys) > 0 AND array_position(carry_keys, '') IS NULL
               AND array_position(carry_keys, NULL) IS NULL)
        """
    )
    op.execute("DROP INDEX IF EXISTS location_projection_open_members_carry_idx")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS carry_key")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS carry_key TEXT")
    op.execute(f"UPDATE {_TABLE} SET carry_key = carry_keys[1] WHERE carry_key IS NULL")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN carry_key SET NOT NULL")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_TABLE}_carry_key_check CHECK (carry_key <> '')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS location_projection_open_members_carry_idx "
        f"ON {_TABLE} (adapter_name, carry_key)"
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_TABLE}_carry_keys_check")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS carry_keys")
