"""Owner-channel candidate read for a manual custody hold (S5h).

Revision ID: core_352
Revises: core_351
Create Date: 2026-11-19 12:00:00.000000

openspec change ``endpoint-custody-holds``, REQ-endpoint-custody-holds-011 (bu-s11n0s.1 slice
5h). ``public.custody_candidates_dashboard('list', p)`` is the content-blind read behind the
dashboard's "Hold a channel" action: the owner's own active channel facts as ``fact_id``, kind,
a masked hint and whether the target is already held. It is the same candidate universe and the
same masking as a security question's candidates (core_350): the owner entity, active literal
``has-email`` / ``has-handle`` / ``has-phone`` facts, never a whole identifier.

Gate: ``dashboard_auth_api`` only (``REVOKE ALL ... FROM PUBLIC`` plus one ``GRANT EXECUTE``).
Like core_347's ``custody_dashboard`` it takes the ``dashboard_auth`` singleton lock and checks the
configured origin and the current cookie session in its own transaction; a read carries no CSRF
token, as the question reads in core_350. A request carries nothing else. No new table.

Installed once across the per-schema core chains, as core_350: a run that finds the function
installed reads the catalog only; otherwise it leaves the chain's open transaction, takes a polled
session lock and installs in one transaction of its own under a short lock timeout, retrying while
a sibling chain holds a catalog row it needs. Only the last schema still at core_352 drops it.
"""

from __future__ import annotations

from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "core_352"
down_revision = "core_351"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_352:custody-candidates', 0))"
_INSTALL_LOCK = 352_20261119
_INSTALL_LOCK_WAIT_SECONDS = 300
_INSTALL_LOCK_TIMEOUT = "100ms"
_API = "dashboard_auth_api"
_SIGNATURE = "public.custody_candidates_dashboard(text, jsonb)"

_FUNCTION = r"""
CREATE OR REPLACE FUNCTION public.custody_candidates_dashboard(action text, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    s dashboard_auth.instance%ROWTYPE;
    se dashboard_auth.sessions%ROWTYPE;
    t timestamptz;
BEGIN
    IF action IS DISTINCT FROM 'list' OR p IS NULL OR jsonb_typeof(p) <> 'object' THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    t := clock_timestamp();
    IF NOT FOUND OR s.state NOT IN ('configured_key', 'keyless_enrolled')
       OR s.origin IS NULL
       OR s.origin IS DISTINCT FROM p->>'origin' OR s.rp_id IS DISTINCT FROM p->>'rp_id'
       OR s.key_generation IS DISTINCT FROM p->>'key_generation' THEN
        RETURN '{"error":"FORBIDDEN"}';
    END IF;
    SELECT * INTO se FROM dashboard_auth.sessions WHERE digest = p->>'session_digest';
    IF NOT FOUND OR se.revoked OR se.expires_at <= t
       OR se.credential_epoch <> s.credential_epoch OR se.session_epoch <> s.session_epoch THEN
        RETURN '{"error":"UNAUTHORIZED"}';
    END IF;
    IF (p - ARRAY['origin', 'rp_id', 'key_generation', 'session_digest', 'csrf_digest'])
           <> '{}'::jsonb THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    -- The candidate universe and masking of core_350's security_question_dashboard 'get'.
    RETURN jsonb_build_object('candidates', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                   'fact_id', ef.id, 'kind', ef.predicate,
                   'hint', CASE WHEN ef.predicate = 'has-email'
                       THEN left(split_part(ef.object, '@', 1), 1) || '***@'
                            || split_part(ef.object, '@', 2)
                       ELSE '...' || right(btrim(ef.object), 4) END,
                   'held', EXISTS (
                       SELECT 1 FROM public.custody_holds h
                       WHERE h.released_at IS NULL AND h.target_kind = ef.predicate
                         AND h.target_digest
                             = public.custody_target_digest(ef.predicate, ef.object)))
                   ORDER BY ef.predicate, ef.id)
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.validity = 'active' AND ef.object_kind = 'literal'
          AND ef.predicate IN ('has-email', 'has-handle', 'has-phone')
          AND 'owner' = ANY (COALESCE(e.roles, ARRAY[]::text[]))
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL), '[]'::jsonb));
END
$fn$;
"""

_ASSUME_OWNER = """DO $$ DECLARE db_owner name; BEGIN
    SELECT pg_get_userbyid(datdba) INTO STRICT db_owner FROM pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('SET LOCAL ROLE %I', db_owner);
    END IF;
END $$"""


def _database_owner_access() -> None:
    """Let the database owner reach the dashboard session tables (mirrors core_347/core_350)."""
    op.execute("""DO $$ DECLARE owner_oid oid; db_owner name; BEGIN
        SELECT datdba, pg_get_userbyid(datdba) INTO STRICT owner_oid, db_owner FROM pg_database
            WHERE datname = current_database();
        IF (SELECT nspowner FROM pg_namespace WHERE nspname = 'dashboard_auth') <> owner_oid THEN
            EXECUTE format('GRANT USAGE ON SCHEMA dashboard_auth TO %I', db_owner);
            EXECUTE format('GRANT SELECT ON dashboard_auth.instance, dashboard_auth.sessions '
                           'TO %I', db_owner);
            EXECUTE format('GRANT SELECT (session_digest, digest, expires_at) '
                           'ON dashboard_auth.csrf TO %I', db_owner);
            EXECUTE format('GRANT UPDATE (singleton) ON dashboard_auth.instance TO %I',
                           db_owner);
        END IF;
    END $$""")


def _fence() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_API}') THEN
            RAISE EXCEPTION 'core_352 requires the {_API} role from core_240';
        END IF;
    END $$""")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {_API}")


def _installation_count() -> int:
    """Count schema-local core chains still at core_352 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 352
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _installed(bind) -> bool:
    """Whether an earlier schema's run committed the function (catalog reads only)."""
    return bool(
        bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM pg_proc fn
                WHERE fn.oid = to_regprocedure('{_SIGNATURE}')
                  AND fn.proacl IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM aclexplode(fn.proacl) acl WHERE acl.grantee = 0)
            )
            """
        ).scalar()
    )


def _acquire_install_lock(bind) -> None:
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("custody candidates install is busy; retry this upgrade")
        sleep(0.1)


def _install() -> None:
    op.execute(_LOCK)
    _database_owner_access()
    op.execute(_ASSUME_OWNER)
    op.execute(_FUNCTION)
    _fence()
    op.execute("RESET ROLE")


def _yields_to_a_sibling(error: DBAPIError) -> bool:
    code = getattr(error.orig, "pgcode", None)
    return code in {"55P03", "40P01"} or (
        code == "XX000" and "tuple concurrently updated" in str(error.orig)
    )


def _install_yielding(connection) -> None:
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while True:
        connection.exec_driver_sql("BEGIN")
        try:
            connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{_INSTALL_LOCK_TIMEOUT}'")
            _install()
        except DBAPIError as error:
            connection.exec_driver_sql("ROLLBACK")
            if not _yields_to_a_sibling(error) or monotonic() >= deadline:
                raise
            sleep(0.2)
            continue
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        connection.exec_driver_sql("COMMIT")
        return


def upgrade() -> None:
    if _installed(op.get_bind()):
        return

    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_install_lock(connection)
        try:
            if not _installed(connection):
                _install_yielding(connection)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({_INSTALL_LOCK})")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(_ASSUME_OWNER)
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
    op.execute("RESET ROLE")
