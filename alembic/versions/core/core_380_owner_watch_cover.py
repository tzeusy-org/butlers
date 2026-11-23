"""The owner-watch large-transaction cover answer (bu-4c7nvp.1.5 S7).

Revision ID: core_380
Revises: core_368
Create Date: 2026-11-24 12:00:00.000000

``public.owner_watch_covers_large_transaction(currency, amount)`` tells Finance one thing: "an
ACTIVE owner watch covers a large transaction of this size in this currency, and its read feed
reported healthy a moment ago". It returns a boolean and nothing else: no watch id, predicate,
threshold or time. Finance suppresses its own ``large_transaction_alert`` flag only on a true
answer, so the owner is told once, by the watch.

Trust: the Switchboard is the trust root for watches. Finance trusts this answer and fails open
on anything else (an error, a false, a NULL, a missing function): the flag fires as before.

The function is ``SECURITY DEFINER`` with a pinned ``search_path``, ``STABLE``, owned by the table
owner of ``public.owner_watches`` (the database's trusted maintenance owner, the core_275/295
posture: no runtime role bypasses or assumes it, so none can ``CREATE OR REPLACE`` or ``ALTER`` it
into "always true"), ``REVOKE``d from PUBLIC, and ``EXECUTE`` goes to ``butler_finance_rw`` alone.
The table's RLS (enabled, not forced) does not apply to its owner, so the definer reads
``owner_watches`` directly. It also reads the feed-health row in the Switchboard ``state`` table
(``owner_watch:feed_health``: capability to the time of its last fully successful poll) and
compares it with the database's ``now()``, never a caller-supplied time; a stale or missing row
is false. It checks the caller identity itself, so a later broader grant still answers nothing.

The core chain runs once per butler schema over this shared function, so the upgrade takes an
advisory lock and a run that finds it installed exactly reads the catalog only. The downgrade
drops it only from the last schema that depends on it.
"""

from __future__ import annotations

import hashlib

from alembic import op
from butlers.db_authority.roles import runtime_roles

revision = "core_380"
down_revision = "core_368"
branch_labels = None
depends_on = None

_FINANCE = "butler_finance_rw"
_RUNTIME = runtime_roles()
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_380:owner-watch-cover', 0))"
_WINDOW = "7 minutes"

_BODY = f"""
DECLARE
    v_capability text;
    v_seen timestamptz;
BEGIN
    IF COALESCE(NULLIF(current_setting('role', true), 'none'), session_user) <> '{_FINANCE}' THEN
        RAISE EXCEPTION 'the owner watch cover answer requires the finance identity'
            USING ERRCODE = '42501';
    END IF;
    IF p_currency IS NULL OR p_currency !~ '^[A-Za-z]{{3}}$'
       OR p_amount IS NULL OR p_amount < 0 THEN
        RETURN false;
    END IF;
    v_capability := 'finance.transaction_amount.' || lower(p_currency);
    SELECT (s.value ->> v_capability)::timestamptz INTO v_seen
    FROM switchboard.state s WHERE s.key = 'owner_watch:feed_health';
    IF v_seen IS NULL OR v_seen <= now() - interval '{_WINDOW}'
       OR v_seen > now() + interval '1 minute' THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.owner_watches w
        WHERE w.state = 'active'
          AND w.expires_at > now()
          AND w.predicate ->> 'tag' = 'read_check'
          AND w.predicate ->> 'capability' = v_capability
          AND w.predicate ->> 'field' = 'amount'
          AND jsonb_typeof(w.predicate -> 'value') = 'number'
          AND CASE w.predicate ->> 'op'
                WHEN 'gt' THEN p_amount > (w.predicate ->> 'value')::numeric
                WHEN 'ge' THEN p_amount >= (w.predicate ->> 'value')::numeric
                ELSE false
              END
    );
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE;
    WHEN OTHERS THEN
        RETURN false;
END
"""
_BODY_SHA = hashlib.sha256(_BODY.encode()).hexdigest()

_SIGNATURE = "public.owner_watch_covers_large_transaction(text, numeric)"

# Catalog-only and lock-free: the function exists with the pinned body, owned by the owner of
# public.owner_watches, SECURITY DEFINER, STABLE, pinned search_path, and an ACL of its owner plus
# (when the role exists) the finance role alone, with EXECUTE and no grant option.
_INSTALLED = f"""
SELECT EXISTS (
    SELECT 1 FROM pg_proc p
    WHERE p.oid = to_regprocedure('{_SIGNATURE}')
      AND p.prosecdef AND p.provolatile = 's'
      AND p.proowner = (SELECT relowner FROM pg_class
                        WHERE oid = to_regclass('public.owner_watches'))
      AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']
      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_BODY_SHA}'
      AND NOT EXISTS (
          SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
          LEFT JOIN pg_roles r ON r.oid = a.grantee
          WHERE a.grantee <> p.proowner
            AND (r.rolname IS DISTINCT FROM '{_FINANCE}'
                 OR a.privilege_type <> 'EXECUTE' OR a.is_grantable)
      )
      AND (
          NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_FINANCE}')
          OR EXISTS (
              SELECT 1 FROM aclexplode(p.proacl) a
              WHERE a.grantee = to_regrole('{_FINANCE}') AND a.privilege_type = 'EXECUTE'
          )
      )
)
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared function."""
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
                    THEN substring(version_num FROM 6)::integer >= 380
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _install_sql(runtime: tuple[str, ...]) -> str:
    """The install block; it refuses when any of ``runtime`` could assume the owner."""
    roles = ",".join(f"'{role}'" for role in runtime)
    return f"""
        DO $install$
        DECLARE
            v_owner oid;
            v_owner_name text;
            v_grantee record;
        BEGIN
            SELECT relowner INTO STRICT v_owner FROM pg_class
            WHERE oid = to_regclass('public.owner_watches');
            v_owner_name := pg_get_userbyid(v_owner);
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ({roles})
                       AND (rolsuper OR rolbypassrls OR pg_has_role(oid, v_owner, 'MEMBER'))) THEN
                RAISE EXCEPTION
                    'Runtime may not bypass or assume the owner watch maintenance owner';
            END IF;
            EXECUTE $fn$
                CREATE OR REPLACE FUNCTION {_SIGNATURE.replace("(text, numeric)", "")}
                    (p_currency text, p_amount numeric) RETURNS boolean
                LANGUAGE plpgsql STABLE SECURITY DEFINER
                SET search_path = pg_catalog, pg_temp AS $body${_BODY}$body$
            $fn$;
            EXECUTE format('ALTER FUNCTION {_SIGNATURE} OWNER TO %I', v_owner_name);
            REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC;
            FOR v_grantee IN
                SELECT r.rolname FROM pg_proc p
                CROSS JOIN LATERAL aclexplode(
                    COALESCE(p.proacl, acldefault('f', p.proowner))) a
                JOIN pg_roles r ON r.oid = a.grantee
                WHERE p.oid = to_regprocedure('{_SIGNATURE}') AND a.grantee <> p.proowner
            LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION {_SIGNATURE} FROM %I', v_grantee.rolname);
            END LOOP;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_FINANCE}') THEN
                GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {_FINANCE};
            END IF;
        END
        $install$
        """


def upgrade() -> None:
    op.execute(_LOCK)
    bind = op.get_bind()
    if bind.exec_driver_sql(_INSTALLED).scalar():
        return
    op.execute(_install_sql(_RUNTIME))
    if not bind.exec_driver_sql(_INSTALLED).scalar():
        raise RuntimeError("owner watch cover answer was not installed as pinned")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
