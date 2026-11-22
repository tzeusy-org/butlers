"""Retired sources and frozen per-day source availability (bu-uhbhzq.2).

Revision ID: chronicler_042
Revises: chronicler_041

* ``source_adapter_state`` gains ``retired_at DATE`` and ``retired_reason TEXT``. They are declared
  in git (``contracts.INITIAL_SOURCES``) and seeded by ``register_source``; no adapter sets them.
* ``day_source_availability`` records, once per local day, whether each projection source was
  ``available``, ``dark``, ``configured_absent`` or ``retired``. It is append-only (a trigger
  refuses UPDATE and DELETE), keyed ``(local_date, timezone, source_name, revision)``, and holds
  source names, states and timestamps only: no content and no endpoint identity.

Flag evaluation and narration read this record instead of current source state, so a source that
recovers today no longer erases the days it was really down. The migration is additive; reverting
the code restores the ``now()``-based evaluation and leaves the table inert. The downgrade refuses
while any snapshot row exists, because dropping the table would lose the only record of past
availability.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_042"
down_revision = "chronicler_041"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_GUARD = "chronicler.day_source_availability_append_only()"


def upgrade() -> None:
    op.execute("ALTER TABLE source_adapter_state ADD COLUMN IF NOT EXISTS retired_at DATE")
    op.execute("ALTER TABLE source_adapter_state ADD COLUMN IF NOT EXISTS retired_reason TEXT")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS day_source_availability (
            local_date DATE NOT NULL,
            timezone TEXT NOT NULL,
            source_name TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            state TEXT NOT NULL
                CHECK (state IN ('available', 'dark', 'configured_absent', 'retired')),
            last_success_at TIMESTAMPTZ,
            watermark TIMESTAMPTZ,
            retired_at DATE,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (local_date, timezone, source_name, revision)
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'day source availability is append-only'
                USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS day_source_availability_append_only ON day_source_availability"
    )
    op.execute(
        f"""
        CREATE TRIGGER day_source_availability_append_only
        BEFORE UPDATE OR DELETE ON day_source_availability
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}
        """
    )
    op.execute(f"REVOKE ALL ON day_source_availability FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON day_source_availability TO {_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('day_source_availability') IS NOT NULL
               AND EXISTS (SELECT 1 FROM day_source_availability) THEN
                RAISE EXCEPTION
                    'refusing to drop day_source_availability: it holds the only record of '
                    'past source availability';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE IF EXISTS day_source_availability")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}")
    op.execute("ALTER TABLE source_adapter_state DROP COLUMN IF EXISTS retired_reason")
    op.execute("ALTER TABLE source_adapter_state DROP COLUMN IF EXISTS retired_at")
