"""OwnTracks retention: a filtered-event copy of a forgotten fix is withheld (bu-s11n0s.7, S4c-4).

Revision ID: core_345
Revises: core_344
Create Date: 2026-11-17 00:00:00.000000

``connectors.filtered_events`` keeps whole OwnTracks envelopes for twelve months, raw body and
coordinate text included. An envelope parked after its ingest attempts were spent copies a
fix whose point is still persisted, so a grant can name it. The deleter
(``butlers.connectors.owntracks_retention.execute_grant``) withholds the parked copies of the
fixes it deletes in its own transaction (a policy-filtered fix has no point row and is outside
a grant's reach: a follow-up under bu-s11n0s.7). This migration covers a copy written later (a phone
resend that is parked after the fix was forgotten):

* ``connectors.filtered_events_withhold_forgotten()``, a ``BEFORE INSERT`` row trigger on the
  partitioned table, takes ``connectors.owntracks_forget_guard()`` (core_343) shared and, for an
  OwnTracks row whose logical key (``'owntracks:' || sender_identity || ':' ||
  external_message_id``) is tombstoned, replaces ``full_payload.payload`` with a withheld marker
  and sets ``full_payload.replay_unavailable``, so the replay drain refuses the row instead of
  submitting a hollow event. The row is kept, so the filter and park audit still work.

Installed once across the per-schema core chains, as core_343: a run that finds the trigger
reads the catalog only; otherwise it takes a polled session lock and installs. Only the last
schema at core_345 downgrades, dropping the trigger and its function.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_345"
down_revision = "core_344"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_345:filtered-withhold', 0))"
_INSTALL_LOCK = 345_20261117
_INSTALL_LOCK_WAIT_SECONDS = 300

_EVENTS = "connectors.filtered_events"
_TOMBSTONES = "connectors.owntracks_retention_tombstones"
_FUNCTION = "connectors.filtered_events_withhold_forgotten()"
_TRIGGER = "filtered_events_withhold_forgotten"
#: Kept in step with ``WITHHELD_TEXT`` and ``NOT_REPLAYABLE`` in
#: ``butlers.connectors.owntracks_retention`` (a test pins them equal).
_WITHHELD = "[location withheld: raw retention]"
_NOT_REPLAYABLE = "the location was forgotten by raw retention"

_CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF NEW.source_channel IS DISTINCT FROM 'owntracks' THEN
        RETURN NEW;
    END IF;
    -- Wait for any deleter to commit, then read its tombstone.
    PERFORM connectors.owntracks_forget_guard();
    IF EXISTS (
        SELECT 1 FROM {_TOMBSTONES}
        WHERE logical_source_key
            = 'owntracks:' || NEW.sender_identity || ':' || NEW.external_message_id
    ) THEN
        NEW.full_payload := NEW.full_payload || jsonb_build_object(
            'payload', jsonb_build_object('raw', NULL, 'normalized_text', '{_WITHHELD}'),
            'replay_unavailable', '{_NOT_REPLAYABLE}'
        );
    END IF;
    RETURN NEW;
END;
$fn$
"""
_CREATE_TRIGGER = f"""
DO $do$
BEGIN
    DROP TRIGGER IF EXISTS {_TRIGGER} ON {_EVENTS};
    CREATE TRIGGER {_TRIGGER} BEFORE INSERT ON {_EVENTS}
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION};
END
$do$
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the trigger."""
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
                    THEN substring(version_num FROM 6)::integer >= 345
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _installed(bind) -> bool:
    """Whether an earlier schema's run finished the shared work (catalog reads only)."""
    return bool(
        bind.exec_driver_sql(
            f"""
            SELECT to_regprocedure('{_FUNCTION}') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass('{_EVENTS}') AND tgname = '{_TRIGGER}'
                      AND tgparentid = 0
               )
            """
        ).scalar()
    )


def _acquire_install_lock(bind) -> None:
    # Session lock polled in autocommit, as core_343 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("filtered-events withhold install is busy; retry this upgrade")
        sleep(0.1)


def upgrade() -> None:
    # Every later schema's run lands here: catalog reads only, no table lock, no catalog write.
    if _installed(op.get_bind()):
        return

    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_install_lock(connection)
        try:
            if not _installed(connection):
                connection.exec_driver_sql(_CREATE_FUNCTION)
                connection.exec_driver_sql(_CREATE_TRIGGER)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({_INSTALL_LOCK})")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF to_regclass('{_EVENTS}') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS {_TRIGGER} ON {_EVENTS};
            END IF;
        END
        $do$
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}")
