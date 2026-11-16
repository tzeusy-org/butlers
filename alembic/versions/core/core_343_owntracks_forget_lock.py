"""OwnTracks retention: a forgotten key's insert waits for the deleter (bu-s11n0s.7, S4b-3b).

Revision ID: core_343
Revises: core_342
Create Date: 2026-11-16 00:00:00.000000

core_342's ``owntracks_points_refuse_forgotten`` read the tombstones before ``ON CONFLICT``
waited on the conflicting row. A webhook replay racing the deleter's uncommitted DELETE and
tombstone therefore saw no tombstone, waited on the unique key while the row was being deleted,
and inserted the forgotten fix again once the deleter committed.

* ``connectors.owntracks_forget_lock(key)`` takes a per-key transaction advisory lock. It is the
  one definition of that lock: the connector's deleter
  (``butlers.connectors.owntracks_retention.execute_grant``) calls it for every granted key
  before it reads or deletes anything, and the trigger calls it before it reads the tombstones.
  A racing replay therefore waits for the deleter's commit and then reads its tombstone. Keys
  only collide by hash, which serialises two unrelated keys and changes nothing else.
* The trigger also fires on ``UPDATE OF idempotency_key``, so no update can move a row onto a
  forgotten key (the update is skipped, as an insert is).

Installed once across the per-schema core chains, as core_342: a run that finds the new trigger
definition reads the catalog only; otherwise it commits the chain so far, takes a polled session
lock and installs, the trigger swap in one statement so no insert runs unchecked. Only the last
schema at core_343 downgrades, restoring core_342's function and insert-only trigger.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_343"
down_revision = "core_342"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_343:owntracks-forget', 0))"
_INSTALL_LOCK = 343_20261116
_INSTALL_LOCK_WAIT_SECONDS = 300

_CONNECTOR_ROLE = "connector_writer"
_POINTS = "connectors.owntracks_points"
_TOMBSTONES = "connectors.owntracks_retention_tombstones"
_FORGET_LOCK = "connectors.owntracks_forget_lock(text)"
_REFUSE_FUNCTION = "connectors.owntracks_points_refuse_forgotten()"
_REFUSE_TRIGGER = "owntracks_points_refuse_forgotten"
_EVENTS = "INSERT OR UPDATE OF idempotency_key"

_CREATE_FORGET_LOCK = """
CREATE OR REPLACE FUNCTION connectors.owntracks_forget_lock(key text)
RETURNS void
LANGUAGE sql
VOLATILE
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT pg_advisory_xact_lock(hashtextextended('butlers:owntracks-forget:' || key, 0))
$fn$
"""

_CREATE_REFUSE = f"""
CREATE OR REPLACE FUNCTION {_REFUSE_FUNCTION}
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    -- Wait for any deleter of this key to commit, then read its tombstone.
    PERFORM connectors.owntracks_forget_lock(NEW.idempotency_key);
    IF EXISTS (
        SELECT 1 FROM {_TOMBSTONES} WHERE logical_source_key = NEW.idempotency_key
    ) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$fn$
"""

# core_342's definitions, restored by the last downgrade.
_CORE_342_REFUSE = f"""
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


def _swap_trigger(events: str) -> str:
    """Replace the trigger in one statement, so no insert runs without it."""
    return f"""
        DO $do$
        BEGIN
            DROP TRIGGER IF EXISTS {_REFUSE_TRIGGER} ON {_POINTS};
            CREATE TRIGGER {_REFUSE_TRIGGER} BEFORE {events} ON {_POINTS}
                FOR EACH ROW EXECUTE FUNCTION {_REFUSE_FUNCTION};
        END
        $do$
    """


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the lock and trigger."""
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
                    THEN substring(version_num FROM 6)::integer >= 343
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
            SELECT to_regprocedure('{_FORGET_LOCK}') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass('{_POINTS}') AND tgname = '{_REFUSE_TRIGGER}'
                      AND position('{_EVENTS}' IN pg_get_triggerdef(oid)) > 0
               )
               AND EXISTS (
                    SELECT 1 FROM pg_proc
                    WHERE oid = to_regprocedure('{_REFUSE_FUNCTION}')
                      AND position('owntracks_forget_lock' IN prosrc) > 0
               )
            """
        ).scalar()
    )


def _acquire_install_lock(bind) -> None:
    # Session lock polled in autocommit, as core_340 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("owntracks forget-lock install is busy; retry this upgrade")
        sleep(0.1)


def _install(connection) -> None:
    connection.exec_driver_sql(_CREATE_FORGET_LOCK)
    connection.exec_driver_sql(
        f"""
        DO $do$
        BEGIN
            REVOKE ALL ON FUNCTION {_FORGET_LOCK} FROM PUBLIC;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_CONNECTOR_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION {_FORGET_LOCK} TO {_CONNECTOR_ROLE};
            END IF;
        END
        $do$
        """
    )
    connection.exec_driver_sql(_CREATE_REFUSE)
    connection.exec_driver_sql(_swap_trigger(_EVENTS))


def upgrade() -> None:
    # Every later schema's run lands here: catalog reads only, no table lock, no catalog write.
    if _installed(op.get_bind()):
        return

    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_install_lock(connection)
        try:
            if not _installed(connection):
                _install(connection)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({_INSTALL_LOCK})")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(_CORE_342_REFUSE)
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF to_regclass('{_POINTS}') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS {_REFUSE_TRIGGER} ON {_POINTS};
                CREATE TRIGGER {_REFUSE_TRIGGER} BEFORE INSERT ON {_POINTS}
                    FOR EACH ROW EXECUTE FUNCTION {_REFUSE_FUNCTION};
            END IF;
        END
        $do$
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_FORGET_LOCK}")
