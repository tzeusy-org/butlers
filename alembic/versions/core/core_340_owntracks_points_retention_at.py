"""OwnTracks points: an immutable per-row retention clock (bu-s11n0s.7, slice 2).

Revision ID: core_340
Revises: core_339
Create Date: 2026-11-15 00:00:00.000000

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

The core chain runs once per butler schema over this one shared table, each run in one
transaction, and those runs can overlap. Locks taken in that transaction are held until the whole
chain commits, so the shared work is shaped to hold as little as possible, once:

1. A run first reads the catalog. When the column is ``NOT NULL`` and both triggers exist (an
   earlier schema's run finished), it returns: no table lock, no catalog write.
2. Otherwise Alembic's autocommit block commits the chain's transaction so far, and the run
   takes a session advisory lock, polled as core_309 does, then reads the catalog again (a peer
   may have finished meanwhile). Every statement after that commits on its own, so no ACCESS
   EXCLUSIVE lock outlives one catalog-only statement:
   a. add the column (no default, catalog-only) and the stamp trigger, so new rows are stamped;
   b. backfill the remaining ``NULL`` rows in primary-key keyset pages of 5000, one short
      transaction each holding only row locks, while the connector keeps inserting;
   c. add a ``NOT VALID`` check and validate it under SHARE UPDATE EXCLUSIVE, which blocks no
      reader or writer, so ``SET NOT NULL`` uses the check instead of a scan; drop the check;
   d. install the update guard.

Every step is idempotent (``IF NOT EXISTS``, ``CREATE OR REPLACE``, trigger existence checks, a
backfill limited to ``NULL`` rows), so an interrupted run resumes at the step it stopped. Like core_309 this does not
tolerate a missing table: core_081 creates it earlier in the chain.
The trigger functions live in ``connectors`` beside the table, pin ``search_path`` and are not
callable outside a trigger; no grant changes. Only the last schema at core_340 drops them on
downgrade, as core_338 does for its shared function.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_340"
down_revision = "core_339"
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

_NOT_NULL_CHECK = "owntracks_points_retention_at_not_null"
_BACKFILL_LOCK = 340_20261115
# A large legacy table can take minutes; a peer run waits for it rather than failing.
_BACKFILL_LOCK_WAIT_SECONDS = 1800
_BACKFILL_BATCH_ROWS = 5000
_UUID_FLOOR = "00000000-0000-0000-0000-000000000000"
# One keyset page by primary key per transaction; returns the page's last id, NULL when done.
_BACKFILL_BATCH = f"""
WITH page AS (
    SELECT id FROM {_TABLE}
    WHERE id > %s::uuid
    ORDER BY id
    LIMIT %s
), stamped AS (
    UPDATE {_TABLE} AS point
    SET retention_at = {RETENTION_AT_SQL.format(ts="point.ts", recorded_at="point.recorded_at")}
    FROM page
    WHERE point.id = page.id AND point.retention_at IS NULL
)
SELECT id::text FROM page ORDER BY id DESC LIMIT 1
"""

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


def _installed(bind) -> bool:
    """Whether a previous schema's run finished this revision's shared work (catalog reads only)."""
    return bool(
        bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass('{_TABLE}') AND attname = 'retention_at'
                  AND attnotnull AND NOT attisdropped
            )
            AND (
                SELECT count(*) FROM pg_trigger
                WHERE tgrelid = to_regclass('{_TABLE}')
                  AND tgname IN ('{_STAMP_TRIGGER}', '{_GUARD_TRIGGER}')
            ) = 2
            """
        ).scalar()
    )


def _trigger_exists(bind, name: str) -> bool:
    return bool(
        bind.exec_driver_sql(
            f"SELECT EXISTS (SELECT 1 FROM pg_trigger "
            f"WHERE tgrelid = to_regclass('{_TABLE}') AND tgname = '{name}')"
        ).scalar()
    )


def _acquire_backfill_lock(bind) -> None:
    # Session lock polled in autocommit, as core_309 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _BACKFILL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_BACKFILL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("owntracks retention backfill is busy; retry this upgrade")
        sleep(0.1)


def _create_trigger_once(connection, name: str, timing: str, function: str) -> None:
    if not _trigger_exists(connection, name):
        connection.exec_driver_sql(
            f"CREATE TRIGGER {name} {timing} ON {_TABLE} FOR EACH ROW EXECUTE FUNCTION {function}"
        )


def _install(connection) -> None:
    """The shared work; each statement is its own short autocommit transaction."""
    # 1. Stamp new rows. ADD COLUMN without a default is catalog-only.
    connection.exec_driver_sql(
        f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS retention_at TIMESTAMPTZ"
    )
    connection.exec_driver_sql(_CREATE_STAMP)
    _create_trigger_once(connection, _STAMP_TRIGGER, "BEFORE INSERT", _STAMP_FUNCTION)

    # 2. Backfill legacy rows (and any inserted before the trigger) in keyset pages, each its
    #    own transaction holding only row locks, so the connector keeps writing.
    after = _UUID_FLOOR
    while after is not None:
        after = connection.exec_driver_sql(_BACKFILL_BATCH, (after, _BACKFILL_BATCH_ROWS)).scalar()

    # 3. Prove NOT NULL with a NOT VALID check validated under SHARE UPDATE EXCLUSIVE (blocks
    #    no reader or writer), so SET NOT NULL uses the check instead of scanning.
    connection.exec_driver_sql(
        f"""
        DO $do$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('{_TABLE}') AND conname = '{_NOT_NULL_CHECK}'
            ) THEN
                ALTER TABLE {_TABLE} ADD CONSTRAINT {_NOT_NULL_CHECK}
                    CHECK (retention_at IS NOT NULL) NOT VALID;
            END IF;
        END
        $do$
        """
    )
    connection.exec_driver_sql(f"ALTER TABLE {_TABLE} VALIDATE CONSTRAINT {_NOT_NULL_CHECK}")
    connection.exec_driver_sql(f"ALTER TABLE {_TABLE} ALTER COLUMN retention_at SET NOT NULL")
    connection.exec_driver_sql(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_NOT_NULL_CHECK}")

    # 4. Refuse later changes to the clock.
    connection.exec_driver_sql(_CREATE_GUARD)
    _create_trigger_once(connection, _GUARD_TRIGGER, "BEFORE UPDATE", _GUARD_FUNCTION)


def upgrade() -> None:
    # Every later schema's run lands here: catalog reads only, no table lock, no catalog write.
    if _installed(op.get_bind()):
        return

    # The autocommit block commits the chain's transaction so far, and every statement below
    # commits on its own, so no table lock outlives one statement.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_backfill_lock(connection)
        try:
            # A peer run may have finished while this one waited for the lock.
            if not _installed(connection):
                _install(connection)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({_BACKFILL_LOCK})")


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
