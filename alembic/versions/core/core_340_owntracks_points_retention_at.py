"""OwnTracks points: an immutable per-row retention clock (bu-s11n0s.7, slice 2).

Revision ID: core_340
Revises: core_338
Create Date: 2026-11-15 00:00:00.000000

Chain note: ``core_339`` is claimed by an unmerged PR. Whichever core head lands first, re-point
this ``down_revision`` at it before merging so the chain keeps exactly one head.

Raw OwnTracks fixes are promised a declared retention horizon
(``butlers.chronicler.location_retention``). Expiry needs a per-row age that nobody can move
after the fact, so this adds ``connectors.owntracks_points.retention_at``:

    CASE WHEN ts - recorded_at > interval '4 hours' THEN recorded_at ELSE ts END

That is the declared ``RAW_AGE_BASIS`` and the OwnTracks point adapter's one-directional
clock-skew rule (``CLOCK_SKEW_THRESHOLD_HOURS``): a device timestamp more than four hours after
server receipt is implausible and cannot extend retention, while an old device timestamp (an
offline queue uploaded late) keeps its device age. Exactly four hours keeps ``ts``.

* A ``BEFORE INSERT`` trigger computes the value from ``NEW.ts``/``NEW.recorded_at`` and
  overwrites anything the caller supplied, so a writer cannot choose its own retention clock.
  ``persist_location_point`` does not name the column and needs no change.
* A ``BEFORE UPDATE`` trigger refuses any change to ``retention_at``, ``ts`` or
  ``recorded_at``. No code path updates those columns (the connector inserts with
  ``ON CONFLICT DO NOTHING``), so refusing all three is simpler than re-deriving the clock.
* Existing rows are backfilled by the same rule before ``NOT NULL`` and the update guard land.

The core chain runs once per butler schema over this one shared table, and those runs can
overlap. Every statement runs under one transaction-scoped advisory lock and is idempotent
(``ADD COLUMN IF NOT EXISTS``, ``CREATE OR REPLACE FUNCTION``, ``DROP TRIGGER IF EXISTS`` before
``CREATE TRIGGER``, a backfill limited to ``NULL`` rows), so a second schema's run is a no-op.
Like core_309 this does not tolerate a missing table: core_081 creates it earlier in the chain.
The trigger functions live in ``connectors`` beside the table, pin ``search_path`` and are not
callable outside a trigger; no grant changes. Only the last schema at core_340 drops them on
downgrade, as core_338 does for its shared function.
"""

from __future__ import annotations

from alembic import op

revision = "core_340"
down_revision = "core_338"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_340:owntracks-retention', 0))"
_TABLE = "connectors.owntracks_points"
_STAMP_FUNCTION = "connectors.owntracks_points_stamp_retention_at()"
_GUARD_FUNCTION = "connectors.owntracks_points_guard_retention_clock()"
_STAMP_TRIGGER = "owntracks_points_stamp_retention_at"
_GUARD_TRIGGER = "owntracks_points_guard_retention_clock"

#: The retention clock. Must equal ``RAW_AGE_BASIS`` and the adapter's clock-skew clamp.
RETENTION_AT_SQL = (
    "CASE WHEN {ts} - {recorded_at} > interval '4 hours' THEN {recorded_at} ELSE {ts} END"
)

_CREATE_STAMP = f"""
CREATE OR REPLACE FUNCTION {_STAMP_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    NEW.retention_at := {RETENTION_AT_SQL.format(ts="NEW.ts", recorded_at="NEW.recorded_at")};
    RETURN NEW;
END;
$fn$
"""

_CREATE_GUARD = f"""
CREATE OR REPLACE FUNCTION {_GUARD_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF NEW.retention_at IS DISTINCT FROM OLD.retention_at
       OR NEW.ts IS DISTINCT FROM OLD.ts
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'owntracks_points retention clock is immutable'
            USING ERRCODE = '55000',
                  DETAIL = 'ts, recorded_at and retention_at cannot change after insert';
    END IF;
    RETURN NEW;
END;
$fn$
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared column and triggers."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 340
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS retention_at TIMESTAMPTZ")
    op.execute(_CREATE_STAMP)
    op.execute(_CREATE_GUARD)
    op.execute(f"DROP TRIGGER IF EXISTS {_STAMP_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_STAMP_TRIGGER}
        BEFORE INSERT ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_STAMP_FUNCTION}
        """
    )
    # Backfill before the update guard exists; a rerun finds no NULL row and updates nothing.
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET retention_at = {RETENTION_AT_SQL.format(ts="ts", recorded_at="recorded_at")}
        WHERE retention_at IS NULL
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN retention_at SET NOT NULL")
    op.execute(f"DROP TRIGGER IF EXISTS {_GUARD_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_GUARD_TRIGGER}
        BEFORE UPDATE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF to_regclass('{_TABLE}') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS {_GUARD_TRIGGER} ON {_TABLE};
                DROP TRIGGER IF EXISTS {_STAMP_TRIGGER} ON {_TABLE};
                ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS retention_at;
            END IF;
        END
        $do$
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FUNCTION}")
    op.execute(f"DROP FUNCTION IF EXISTS {_STAMP_FUNCTION}")
