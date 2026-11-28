"""public.calendar_rsvp_ledger: how the owner dealt with each prepared answer (bu-5ii4gj.9 S5).

Revision ID: core_390
Revises: core_389
Create Date: 2026-11-27 12:00:00.000000

openspec change ``calendar-invitation-docket``, REQ-calendar-invitation-docket-006. One row per
parked docket card whose outcome is known: ``approved_as_prepared``, ``owner_rejected``,
``answered_same`` (the owner answered outside the card with the card's own verdict) or
``answered_elsewhere``. There is no "edited" outcome. A system rejection never gets a row.

The ledger is informational. Nothing reads it to widen autonomy; the only readers are the
dashboard's definer and the docket job's recorder:

- ``public.calendar_rsvp_ledger_record(action_id, policy_id, policy_version, outcome)`` is the only
  writer: SECURITY DEFINER, EXECUTE for ``butler_relationship_rw`` (the docket butler) alone, and
  idempotent per card, so a rerun records nothing twice.
- ``public.calendar_rsvp_ledger_dashboard(action, p)`` returns the counts per policy and version
  for the owner's current cookie session (the core_350 pattern), EXECUTE for
  ``dashboard_auth_api`` alone.
- The table is row-level-secured with no policy; every runtime role holds no privilege on it, and
  rows can be neither updated nor deleted.

The core chain runs once per butler schema over these shared objects: every statement is idempotent
under an advisory lock, and only the last schema still at core_390 drops them, refusing while a row
exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_390"
down_revision = "core_389"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_390:rsvp_ledger', 0))"
_API = "dashboard_auth_api"
_WRITER = "butler_relationship_rw"

_TABLE = """
CREATE TABLE IF NOT EXISTS public.calendar_rsvp_ledger (
    action_id UUID PRIMARY KEY,
    policy_id UUID NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    outcome TEXT NOT NULL CHECK (outcome IN (
        'approved_as_prepared', 'owner_rejected', 'answered_same', 'answered_elsewhere')),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS calendar_rsvp_ledger_policy_idx
    ON public.calendar_rsvp_ledger (policy_id, policy_version);
"""

_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_calendar_rsvp_ledger() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    RAISE EXCEPTION 'the rsvp ledger is append-only' USING ERRCODE = '23514';
END
$fn$;
DROP TRIGGER IF EXISTS guard_calendar_rsvp_ledger_rows ON public.calendar_rsvp_ledger;
CREATE TRIGGER guard_calendar_rsvp_ledger_rows
    BEFORE UPDATE OR DELETE ON public.calendar_rsvp_ledger
    FOR EACH ROW EXECUTE FUNCTION public.guard_calendar_rsvp_ledger();
DROP TRIGGER IF EXISTS guard_calendar_rsvp_ledger_truncate ON public.calendar_rsvp_ledger;
CREATE TRIGGER guard_calendar_rsvp_ledger_truncate
    BEFORE TRUNCATE ON public.calendar_rsvp_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_calendar_rsvp_ledger();
"""

_RECORD = r"""
CREATE OR REPLACE FUNCTION public.calendar_rsvp_ledger_record(
    p_action_id uuid, p_policy_id uuid, p_policy_version integer, p_outcome text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    inserted integer;
BEGIN
    IF p_action_id IS NULL OR p_policy_id IS NULL OR p_policy_version IS NULL
       OR p_policy_version < 1 OR p_outcome IS NULL
       OR p_outcome NOT IN ('approved_as_prepared', 'owner_rejected', 'answered_same',
                            'answered_elsewhere') THEN
        RAISE EXCEPTION 'invalid ledger outcome' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.calendar_rsvp_policies
                   WHERE id = p_policy_id AND version >= p_policy_version) THEN
        RAISE EXCEPTION 'unknown policy version' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.calendar_rsvp_ledger (action_id, policy_id, policy_version, outcome)
    VALUES (p_action_id, p_policy_id, p_policy_version, p_outcome)
    ON CONFLICT (action_id) DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted = 1;
END
$fn$;
"""

_DASHBOARD = r"""
CREATE OR REPLACE FUNCTION public.calendar_rsvp_ledger_dashboard(action text, p jsonb)
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
    IF action IS DISTINCT FROM 'counts' OR p IS NULL OR jsonb_typeof(p) <> 'object' THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR SHARE;
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
    p := p - ARRAY['origin', 'rp_id', 'key_generation', 'session_digest', 'csrf_digest'];
    IF p <> '{}'::jsonb THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;
    RETURN jsonb_build_object('counts', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'policy_id', x.policy_id, 'policy_version', x.policy_version,
            'outcome', x.outcome, 'count', x.n) ORDER BY x.policy_id, x.policy_version, x.outcome)
        FROM (SELECT policy_id, policy_version, outcome, count(*) AS n
              FROM public.calendar_rsvp_ledger GROUP BY 1, 2, 3) x), '[]'::jsonb));
END
$fn$;
"""

_FUNCTIONS = (
    "public.calendar_rsvp_ledger_record(uuid, uuid, integer, text)",
    "public.calendar_rsvp_ledger_dashboard(text, jsonb)",
    "public.guard_calendar_rsvp_ledger()",
)

_ASSUME_OWNER = """DO $$ DECLARE db_owner name; BEGIN
    SELECT pg_get_userbyid(datdba) INTO STRICT db_owner FROM pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('SET LOCAL ROLE %I', db_owner);
    END IF;
END $$"""


def _database_owner_access() -> None:
    """Let the database owner, who owns every object here, reach the dashboard session tables.

    A no-op in a normal deployment, where the owner already owns ``dashboard_auth``; mirrors
    core_347 and core_350 for a deployment where another login does.
    """
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
    """No runtime role touches the table; only the definers, run as the owner, do."""
    op.execute("ALTER TABLE public.calendar_rsvp_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.calendar_rsvp_ledger NO FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE public.calendar_rsvp_ledger FROM PUBLIC")
    op.execute(f"""DO $$ DECLARE role_name text; BEGIN
        FOR role_name IN SELECT rolname FROM pg_roles
            WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
               OR rolname IN ('connector_writer', '{_API}')
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.calendar_rsvp_ledger FROM %I', role_name);
        END LOOP;
    END $$""")
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_API}') THEN
            RAISE EXCEPTION 'core_390 requires the {_API} role from core_240';
        END IF;
    END $$""")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.calendar_rsvp_ledger_dashboard(text, jsonb) TO {_API}"
    )
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_WRITER}') THEN
            GRANT EXECUTE ON FUNCTION
                public.calendar_rsvp_ledger_record(uuid, uuid, integer, text) TO {_WRITER};
        END IF;
    END $$""")


def _installation_count() -> int:
    """Count schema-local core chains still at core_390 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 390
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    _database_owner_access()
    op.execute(_ASSUME_OWNER)
    op.execute(_TABLE)
    op.execute(_GUARD)
    op.execute(_RECORD)
    op.execute(_DASHBOARD)
    _fence()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(_ASSUME_OWNER)
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.calendar_rsvp_ledger') IS NULL THEN
            RETURN;
        END IF;
        IF NOT pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid = to_regclass('public.calendar_rsvp_ledger')), 'USAGE') THEN
            RAISE EXCEPTION 'core_390 downgrade refused: only the maintenance owner can prove '
                'no ledger row exists' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (SELECT 1 FROM public.calendar_rsvp_ledger) THEN
            RAISE EXCEPTION 'core_390 downgrade refused: ledger rows exist'
                USING ERRCODE = '55000';
        END IF;
    END $$""")
    op.execute(
        "DROP FUNCTION IF EXISTS public.calendar_rsvp_ledger_record(uuid, uuid, integer, text)"
    )
    op.execute("DROP FUNCTION IF EXISTS public.calendar_rsvp_ledger_dashboard(text, jsonb)")
    op.execute("DROP TABLE IF EXISTS public.calendar_rsvp_ledger")
    op.execute("DROP FUNCTION IF EXISTS public.guard_calendar_rsvp_ledger()")
    op.execute("RESET ROLE")
