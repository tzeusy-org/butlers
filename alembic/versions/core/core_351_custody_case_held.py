"""Content-blind "is this custody case still held" read for the lapse sweep (bu-s11n0s.1.1).

Revision ID: core_351
Revises: core_350
Create Date: 2026-11-19 00:00:00.000000

REQ-endpoint-custody-holds-010: a lost-device case closes only when every assigned generation is
released or replaced. The fleet-case lapse sweep (Switchboard, ``butler_switchboard_rw``) closes
stale silent/routine cases, and an ``endpoint_custody:<episode>`` case is opened silent, so a
quiet hold older than the staleness window was lapsed while still held.

The sweep cannot read ``public.custody_holds`` (core_293 revokes it from PUBLIC) and is not
granted it. ``public.custody_case_held(correlation_key)`` answers only whether the case's
episode still has an unreleased hold: SECURITY DEFINER, owned by the database owner, EXECUTE to
PUBLIC like ``custody_target_held`` and ``custody_target_episode``. It returns a boolean and no
target, digest, reason or time. Any key that is not ``endpoint_custody:<uuid>`` answers false;
the episode is compared as text, so a malformed key never raises inside the sweep.

Installed once across the per-schema core chains with core_347's yielding installer, like
core_349. The downgrade drops the function with the last schema.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "core_351"
down_revision = "core_350"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_293:custody_holds', 0))"
_FN = "public.custody_case_held(text)"

_CASE_HELD_READ = """
CREATE OR REPLACE FUNCTION public.custody_case_held(p_correlation_key text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT left(p_correlation_key, 17) = 'endpoint_custody:'
       AND EXISTS (
           SELECT 1
           FROM public.custody_holds h
           WHERE h.released_at IS NULL
             AND h.episode_id::text = substr(p_correlation_key, 18)
       )
$fn$;
REVOKE ALL ON FUNCTION public.custody_case_held(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.custody_case_held(text) TO PUBLIC;
"""

_ASSUME_OWNER = """DO $$ DECLARE db_owner name; BEGIN
    SELECT pg_get_userbyid(datdba) INTO STRICT db_owner FROM pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('SET LOCAL ROLE %I', db_owner);
    END IF;
END $$"""


def _installed(bind) -> bool:
    return bool(bind.exec_driver_sql(f"SELECT to_regprocedure('{_FN}') IS NOT NULL").scalar())


def _core_347():
    """core_347's module: its install lock, sibling-yield test and timeouts are reused."""
    path = Path(__file__).with_name("core_347_custody_command_engine.py")
    spec = importlib.util.spec_from_file_location("_core_347_custody_command_engine", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installation_count() -> int:
    """Count schema-local core chains still at core_351 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 351
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _install() -> None:
    op.execute(_ASSUME_OWNER)
    op.execute(_CASE_HELD_READ)
    op.execute("RESET ROLE")


def _install_yielding(previous, connection) -> None:
    """Install in one explicit transaction that never waits long while holding anything."""
    deadline = monotonic() + previous._INSTALL_LOCK_WAIT_SECONDS
    while True:
        connection.exec_driver_sql("BEGIN")
        try:
            connection.exec_driver_sql(
                f"SET LOCAL lock_timeout = '{previous._INSTALL_LOCK_TIMEOUT}'"
            )
            if _installed(connection):
                connection.exec_driver_sql("ROLLBACK")
                return
            _install()
        except DBAPIError as error:
            connection.exec_driver_sql("ROLLBACK")
            if not previous._yields_to_a_sibling(error) or monotonic() >= deadline:
                raise
            sleep(0.2)
            continue
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        connection.exec_driver_sql("COMMIT")
        return


def upgrade() -> None:
    # Every later schema's run lands here: a catalog read only, no lock, no catalog write.
    if _installed(op.get_bind()):
        return
    previous = _core_347()
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous._acquire_install_lock(connection)
        try:
            _install_yielding(previous, connection)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({previous._INSTALL_LOCK})")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(_ASSUME_OWNER)
    op.execute(f"DROP FUNCTION IF EXISTS {_FN}")
    op.execute("RESET ROLE")
