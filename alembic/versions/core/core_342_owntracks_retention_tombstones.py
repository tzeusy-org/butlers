"""OwnTracks retention: connector-side tombstones and batch receipts (bu-s11n0s.7, S4b-3a).

Revision ID: core_342
Revises: core_341
Create Date: 2026-11-16 00:00:00.000000

Retention runs in two committed stages. Chronicler prepares a grant and forgets its own copies
(chronicler_036/037); the connector then deletes the granted raw rows from
``connectors.owntracks_points`` under ``connector_writer`` (a later slice, still dark). This
revision only creates where that deletion leaves its evidence, and the floor that keeps a deleted
row deleted:

* ``connectors.owntracks_retention_batches``: the connector's receipt, one row per grant it
  executed, with ``deleted_count + already_forgotten_count = item_count``.
* ``connectors.owntracks_retention_tombstones``: one row per forgotten logical key (the point's
  ``idempotency_key``), its raw id and the grant's v1 content digest. Each tombstone belongs to a
  receipt (deferred FK), so both are written in the deleting transaction.
* Both tables are append-only by a statement trigger on UPDATE, DELETE and TRUNCATE.
  ``scripts/init-db.sql`` re-grants every table in ``connectors`` full DML to
  ``connector_writer`` on each replay, so the revoke below is not the fence; the trigger is.
* ``owntracks_points_refuse_forgotten`` (BEFORE INSERT on ``owntracks_points``) skips a row whose
  ``idempotency_key`` is tombstoned, as ``ON CONFLICT DO NOTHING`` skips a duplicate, so a
  replayed or late webhook cannot restore raw evidence the owner was promised was forgotten. It
  fires before the retention-clock stamp (trigger names sort ``refuse`` < ``stamp``).

Grants: ``connector_writer`` SELECT and INSERT on both tables (it already holds DELETE on
``owntracks_points`` from core_081); ``butler_chronicler_rw`` SELECT, to reconcile its grants.

The core chain runs once per butler schema over these shared objects, so this follows core_340:
a run that finds everything installed reads the catalog only; otherwise it commits the chain so
far, takes a session advisory lock (polled), re-reads the catalog and installs with idempotent
statements that each commit on their own, so the ``owntracks_points`` trigger's lock never
outlives one statement. Only the last schema at core_342 drops on downgrade, and it refuses while
either table holds evidence: dropping a tombstone would let a forgotten point back in.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_342"
down_revision = "core_341"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_342:owntracks-forget', 0))"
_INSTALL_LOCK = 342_20261116
_INSTALL_LOCK_WAIT_SECONDS = 300

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_POINTS = "connectors.owntracks_points"
_BATCHES = "connectors.owntracks_retention_batches"
_TOMBSTONES = "connectors.owntracks_retention_tombstones"
_APPEND_ONLY_FUNCTION = "connectors.owntracks_retention_append_only()"
_REFUSE_FUNCTION = "connectors.owntracks_points_refuse_forgotten()"
_REFUSE_TRIGGER = "owntracks_points_refuse_forgotten"
_APPEND_ONLY_TRIGGERS = {
    _BATCHES: "owntracks_retention_batches_append_only",
    _TOMBSTONES: "owntracks_retention_tombstones_append_only",
}

_CREATE_BATCHES = f"""
CREATE TABLE IF NOT EXISTS {_BATCHES} (
    grant_id UUID PRIMARY KEY,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    deleted_count INTEGER NOT NULL CHECK (deleted_count >= 0),
    already_forgotten_count INTEGER NOT NULL CHECK (already_forgotten_count >= 0),
    committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT owntracks_retention_batches_counts_add_up
        CHECK (deleted_count + already_forgotten_count = item_count)
)
"""

_CREATE_TOMBSTONES = f"""
CREATE TABLE IF NOT EXISTS {_TOMBSTONES} (
    logical_source_key TEXT PRIMARY KEY CHECK (logical_source_key <> ''),
    raw_source_id UUID NOT NULL UNIQUE,
    content_digest TEXT NOT NULL CHECK (content_digest ~ '^v1:[0-9a-f]{{64}}$'),
    grant_id UUID NOT NULL
        REFERENCES {_BATCHES} (grant_id) DEFERRABLE INITIALLY DEFERRED,
    forgotten_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_TOMBSTONE_GRANT_INDEX = f"""
CREATE INDEX IF NOT EXISTS ix_owntracks_retention_tombstones_grant
    ON {_TOMBSTONES} (grant_id)
"""

_CREATE_APPEND_ONLY = f"""
CREATE OR REPLACE FUNCTION {_APPEND_ONLY_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    -- No format placeholder: the driver would read it as a bind parameter.
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is append-only',
              ERRCODE = '55000',
              DETAIL = 'retention receipts and tombstones are evidence and never change';
END;
$fn$
"""

_CREATE_REFUSE = f"""
CREATE OR REPLACE FUNCTION {_REFUSE_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF EXISTS (
        SELECT 1 FROM {_TOMBSTONES} WHERE logical_source_key = NEW.idempotency_key
    ) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$fn$
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared tables and triggers."""
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
                    THEN substring(version_num FROM 6)::integer >= 342
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _trigger_exists(bind, table: str, name: str) -> bool:
    return bool(
        bind.exec_driver_sql(
            f"SELECT EXISTS (SELECT 1 FROM pg_trigger "
            f"WHERE tgrelid = to_regclass('{table}') AND tgname = '{name}')"
        ).scalar()
    )


def _installed(bind) -> bool:
    """Whether an earlier schema's run finished the shared work (catalog reads only)."""
    triggers = {**_APPEND_ONLY_TRIGGERS, _POINTS: _REFUSE_TRIGGER}
    return all(_trigger_exists(bind, table, name) for table, name in triggers.items())


def _acquire_install_lock(bind) -> None:
    # Session lock polled in autocommit, as core_340 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("owntracks retention tombstone install is busy; retry this upgrade")
        sleep(0.1)


def _grant_if_role(connection, statement: str, role: str) -> None:
    connection.exec_driver_sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                {statement};
            END IF;
        END
        $do$
        """
    )


def _install(connection) -> None:
    """The shared work; each statement is its own short autocommit transaction."""
    connection.exec_driver_sql(_CREATE_BATCHES)
    connection.exec_driver_sql(_CREATE_TOMBSTONES)
    connection.exec_driver_sql(_CREATE_TOMBSTONE_GRANT_INDEX)
    for table in (_BATCHES, _TOMBSTONES):
        # Default privileges in ``connectors`` hand the connector full DML; narrow it here.
        _grant_if_role(
            connection,
            f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {_CONNECTOR_ROLE}",
            _CONNECTOR_ROLE,
        )
        _grant_if_role(
            connection, f"GRANT SELECT, INSERT ON {table} TO {_CONNECTOR_ROLE}", _CONNECTOR_ROLE
        )
        _grant_if_role(
            connection,
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM {_CHRONICLER_ROLE}",
            _CHRONICLER_ROLE,
        )
        _grant_if_role(
            connection, f"GRANT SELECT ON {table} TO {_CHRONICLER_ROLE}", _CHRONICLER_ROLE
        )

    connection.exec_driver_sql(_CREATE_APPEND_ONLY)
    for table, name in _APPEND_ONLY_TRIGGERS.items():
        if not _trigger_exists(connection, table, name):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}"
            )

    connection.exec_driver_sql(_CREATE_REFUSE)
    if not _trigger_exists(connection, _POINTS, _REFUSE_TRIGGER):
        connection.exec_driver_sql(
            f"CREATE TRIGGER {_REFUSE_TRIGGER} BEFORE INSERT ON {_POINTS} "
            f"FOR EACH ROW EXECUTE FUNCTION {_REFUSE_FUNCTION}"
        )


def upgrade() -> None:
    # Every later schema's run lands here: catalog reads only, no table lock, no catalog write.
    if _installed(op.get_bind()):
        return

    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_install_lock(connection)
        try:
            # A peer run may have finished while this one waited for the lock.
            if not _installed(connection):
                _install(connection)
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
            IF (to_regclass('{_BATCHES}') IS NOT NULL AND EXISTS (SELECT 1 FROM {_BATCHES}))
               OR (to_regclass('{_TOMBSTONES}') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM {_TOMBSTONES})) THEN
                RAISE EXCEPTION 'owntracks points have been forgotten; not dropping their evidence'
                    USING ERRCODE = '55000',
                          DETAIL = 'dropping a tombstone would let a forgotten point back in';
            END IF;
            IF to_regclass('{_POINTS}') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS {_REFUSE_TRIGGER} ON {_POINTS};
            END IF;
        END
        $do$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TOMBSTONES}")
    op.execute(f"DROP TABLE IF EXISTS {_BATCHES}")
    op.execute(f"DROP FUNCTION IF EXISTS {_REFUSE_FUNCTION}")
    op.execute(f"DROP FUNCTION IF EXISTS {_APPEND_ONLY_FUNCTION}")
