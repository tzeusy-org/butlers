"""public.calendar_rsvp_policies: owner-written RSVP policies, written only by a dashboard definer.

Revision ID: core_388
Revises: core_387
Create Date: 2026-11-27 10:00:00.000000

openspec change ``calendar-invitation-docket`` (bu-5ii4gj.9 S1), REQ-calendar-invitation-docket-001.
The owner's standing invitation judgment: one row per policy with an ordered ``position``, a list of
closed typed predicates, a verdict, a ``send_updates`` choice, a ``version`` that only rises and a
``revoked_at`` that, once set, stays. A policy is never deleted.

Nothing but the definer writes it, and no runtime role reads it directly:

- ``public.calendar_rsvp_predicates_valid(jsonb)`` is the closed predicate vocabulary (also a CHECK):
  organizer verdict, organizer role, focus or out-of-office overlap, conflict with an accepted
  event, outside declared hours, recurring, attendee count, lead time. No event title or
  description is a kind.
- ``public.calendar_rsvp_policy_dashboard(action, p)``: ``list``, ``create``, ``update`` and
  ``revoke`` for ``dashboard_auth_api`` only. It takes the ``dashboard_auth`` singleton lock, then
  checks the configured origin, the current cookie session and, for a write, its CSRF token in the
  committing transaction (the core_350 pattern). Nothing in a request names an actor.
- The table is row-level-secured with no policy and every runtime role (``butler_*_rw``,
  ``connector_writer``, the dashboard login) holds no privilege on it. The docket job's read
  grant arrives with the slice that adds the job (bu-5ii4gj.9 S3), once that butler is fixed.

The core chain runs once per butler schema over these shared objects: every statement is idempotent
under an advisory lock, and only the last schema still at core_388 drops them, refusing while a
policy exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_388"
down_revision = "core_387"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_388:rsvp_policies', 0))"
_API = "dashboard_auth_api"

_TABLE = """
CREATE TABLE IF NOT EXISTS public.calendar_rsvp_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 1000),
    predicates JSONB NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('accepted', 'declined', 'tentative')),
    send_updates TEXT NOT NULL CHECK (send_updates IN ('all', 'externalOnly', 'none')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS calendar_rsvp_policies_active_idx
    ON public.calendar_rsvp_policies (position, id) WHERE revoked_at IS NULL;
"""

_VALID = r"""
CREATE OR REPLACE FUNCTION public.calendar_rsvp_predicates_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE
    item jsonb;
    kind text;
    seen text[] := ARRAY[]::text[];
    v jsonb;
BEGIN
    IF p IS NULL OR jsonb_typeof(p) <> 'array' OR jsonb_array_length(p) NOT BETWEEN 1 AND 8 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(p) LOOP
        IF jsonb_typeof(item) <> 'object' THEN RETURN false; END IF;
        kind := item ->> 'kind';
        v := item -> 'value';
        IF kind IS NULL OR kind = ANY (seen) OR v IS NULL THEN RETURN false; END IF;
        seen := seen || kind;
        IF kind IN ('overlaps_focus_or_out_of_office', 'conflicts_with_accepted',
                    'outside_declared_hours', 'recurring') THEN
            IF jsonb_typeof(v) <> 'boolean' OR (item - ARRAY['kind', 'value']) <> '{}'::jsonb THEN
                RETURN false;
            END IF;
        ELSIF kind = 'organizer_verdict' THEN
            IF jsonb_typeof(v) <> 'string'
               OR (v #>> '{}') NOT IN ('owner', 'known_entity', 'known_domain', 'vouched')
               OR (item - ARRAY['kind', 'value']) <> '{}'::jsonb THEN
                RETURN false;
            END IF;
        ELSIF kind = 'organizer_role' THEN
            IF jsonb_typeof(v) <> 'string' OR (v #>> '{}') !~ '^[a-z][a-z0-9_-]{0,31}$'
               OR (item - ARRAY['kind', 'value']) <> '{}'::jsonb THEN
                RETURN false;
            END IF;
        ELSIF kind IN ('attendee_count', 'lead_time_hours') THEN
            IF jsonb_typeof(v) <> 'number' OR (v #>> '{}') !~ '^[0-9]{1,5}$'
               OR (kind = 'attendee_count' AND (v #>> '{}')::integer > 10000)
               OR (kind = 'lead_time_hours' AND (v #>> '{}')::integer > 8760)
               OR (item ->> 'op') IS NULL OR (item ->> 'op') NOT IN ('lte', 'gte')
               OR (item - ARRAY['kind', 'value', 'op']) <> '{}'::jsonb THEN
                RETURN false;
            END IF;
        ELSE
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END
$fn$;
"""

_CHECK = """
DO $do$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_rsvp_policies'::regclass
          AND conname = 'calendar_rsvp_policies_predicates_valid'
    ) THEN
        ALTER TABLE public.calendar_rsvp_policies
            ADD CONSTRAINT calendar_rsvp_policies_predicates_valid
            CHECK (public.calendar_rsvp_predicates_valid(predicates));
    END IF;
END $do$;
"""

_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_calendar_rsvp_policies() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'rsvp policies are revoked, never deleted' USING ERRCODE = '23514';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'rsvp policy identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.version < OLD.version
       OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
       OR (OLD.revoked_at IS NOT NULL AND NEW.version IS DISTINCT FROM OLD.version) THEN
        RAISE EXCEPTION 'a revoked rsvp policy stays revoked; the version only rises'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS guard_calendar_rsvp_policies_rows ON public.calendar_rsvp_policies;
CREATE TRIGGER guard_calendar_rsvp_policies_rows
    BEFORE UPDATE OR DELETE ON public.calendar_rsvp_policies
    FOR EACH ROW EXECUTE FUNCTION public.guard_calendar_rsvp_policies();
DROP TRIGGER IF EXISTS guard_calendar_rsvp_policies_truncate ON public.calendar_rsvp_policies;
CREATE TRIGGER guard_calendar_rsvp_policies_truncate
    BEFORE TRUNCATE ON public.calendar_rsvp_policies
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_calendar_rsvp_policies();
"""

# The session and CSRF checks repeat core_350's security_question_dashboard, in this transaction.
_DASHBOARD = r"""
CREATE OR REPLACE FUNCTION public.calendar_rsvp_policy_dashboard(action text, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    s dashboard_auth.instance%ROWTYPE;
    se dashboard_auth.sessions%ROWTYPE;
    t timestamptz;
    row_ public.calendar_rsvp_policies%ROWTYPE;
    pid uuid;
    new_position integer;
    new_predicates jsonb;
    new_verdict text;
    new_send text;
BEGIN
    IF action IS NULL OR action NOT IN ('list', 'create', 'update', 'revoke')
       OR p IS NULL OR jsonb_typeof(p) <> 'object' THEN
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
    IF action <> 'list' AND NOT EXISTS (
        SELECT 1 FROM dashboard_auth.csrf
        WHERE session_digest = se.digest AND digest = p->>'csrf_digest' AND expires_at > t
    ) THEN
        RETURN '{"error":"FORBIDDEN"}';
    END IF;
    p := p - ARRAY['origin', 'rp_id', 'key_generation', 'session_digest', 'csrf_digest'];

    IF action = 'list' THEN
        IF p <> '{}'::jsonb THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;
        RETURN jsonb_build_object('policies', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', x.id, 'position', x.position, 'predicates', x.predicates,
                'verdict', x.verdict, 'send_updates', x.send_updates, 'version', x.version,
                'revoked_at', x.revoked_at, 'updated_at', x.updated_at)
                ORDER BY x.position, x.id)
            FROM (
                SELECT * FROM public.calendar_rsvp_policies WHERE revoked_at IS NULL
                UNION ALL
                (SELECT * FROM public.calendar_rsvp_policies WHERE revoked_at IS NOT NULL
                 ORDER BY revoked_at DESC, id LIMIT 80)
            ) x), '[]'::jsonb));
    END IF;

    IF action = 'create' THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k
                   WHERE k NOT IN ('position', 'predicates', 'verdict', 'send_updates')) THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
    ELSIF action = 'update' THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k
                   WHERE k NOT IN ('id', 'expected_version', 'position', 'predicates', 'verdict',
                                   'send_updates')) THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k WHERE k <> 'id') THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;

    IF action IN ('update', 'revoke') THEN
        BEGIN
            pid := (p->>'id')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END;
        IF pid IS NULL THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;
        SELECT * INTO row_ FROM public.calendar_rsvp_policies WHERE id = pid FOR UPDATE;
        IF NOT FOUND THEN
            RETURN '{"outcome":"refused","reason":"unknown_policy"}';
        END IF;
        IF row_.revoked_at IS NOT NULL THEN
            RETURN '{"outcome":"refused","reason":"policy_revoked"}';
        END IF;
        IF action = 'update' THEN
            BEGIN
                IF (p->>'expected_version')::integer IS DISTINCT FROM row_.version THEN
                    RETURN '{"outcome":"refused","reason":"stale_version"}';
                END IF;
            EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RETURN '{"error":"BAD_REQUEST"}';
            END;
        END IF;
        IF action = 'revoke' THEN
            UPDATE public.calendar_rsvp_policies
               SET revoked_at = t, version = version + 1, updated_at = t WHERE id = pid
            RETURNING * INTO row_;
            RETURN jsonb_build_object('outcome', 'revoked', 'id', row_.id, 'version', row_.version);
        END IF;
    END IF;

    BEGIN
        new_position := COALESCE((p->>'position')::integer, row_.position);
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END;
    new_predicates := COALESCE(p->'predicates', row_.predicates);
    new_verdict := COALESCE(p->>'verdict', row_.verdict);
    new_send := COALESCE(p->>'send_updates', row_.send_updates);
    IF new_position IS NULL OR new_predicates IS NULL OR new_verdict IS NULL OR new_send IS NULL
       OR new_position NOT BETWEEN 0 AND 1000
       OR NOT public.calendar_rsvp_predicates_valid(new_predicates)
       OR new_verdict NOT IN ('accepted', 'declined', 'tentative')
       OR new_send NOT IN ('all', 'externalOnly', 'none') THEN
        RETURN '{"outcome":"refused","reason":"invalid_policy"}';
    END IF;

    IF action = 'create' THEN
        IF (SELECT count(*) FROM public.calendar_rsvp_policies WHERE revoked_at IS NULL) >= 20 THEN
            RETURN '{"outcome":"refused","reason":"too_many_policies"}';
        END IF;
        INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, send_updates)
        VALUES (new_position, new_predicates, new_verdict, new_send) RETURNING * INTO row_;
        RETURN jsonb_build_object('outcome', 'created', 'id', row_.id, 'version', row_.version);
    END IF;

    UPDATE public.calendar_rsvp_policies
       SET position = new_position, predicates = new_predicates, verdict = new_verdict,
           send_updates = new_send, version = version + 1, updated_at = t
     WHERE id = pid RETURNING * INTO row_;
    RETURN jsonb_build_object('outcome', 'updated', 'id', row_.id, 'version', row_.version);
END
$fn$;
"""

_FUNCTIONS = (
    "public.calendar_rsvp_policy_dashboard(text, jsonb)",
    "public.calendar_rsvp_predicates_valid(jsonb)",
    "public.guard_calendar_rsvp_policies()",
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
    """No runtime role touches the table; only the definer, run as the owner, does."""
    op.execute("ALTER TABLE public.calendar_rsvp_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.calendar_rsvp_policies NO FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE public.calendar_rsvp_policies FROM PUBLIC")
    # init-db.sql's default privileges hand runtime roles DML on new public tables; start from
    # nothing. With row level security on and no policy, a later replay still reads nothing.
    op.execute(f"""DO $$ DECLARE role_name text; BEGIN
        FOR role_name IN SELECT rolname FROM pg_roles
            WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
               OR rolname IN ('connector_writer', '{_API}')
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.calendar_rsvp_policies FROM %I',
                           role_name);
        END LOOP;
    END $$""")
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_API}') THEN
            RAISE EXCEPTION 'core_388 requires the {_API} role from core_240';
        END IF;
    END $$""")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.calendar_rsvp_policy_dashboard(text, jsonb) TO {_API}"
    )


def _installation_count() -> int:
    """Count schema-local core chains still at core_388 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 388
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
    op.execute(_VALID)
    op.execute(_CHECK)
    op.execute(_GUARD)
    op.execute(_DASHBOARD)
    _fence()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(_ASSUME_OWNER)
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.calendar_rsvp_policies') IS NULL THEN
            RETURN;
        END IF;
        IF NOT pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid = to_regclass('public.calendar_rsvp_policies')), 'USAGE') THEN
            RAISE EXCEPTION 'core_388 downgrade refused: only the maintenance owner can prove '
                'no policy exists' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (SELECT 1 FROM public.calendar_rsvp_policies) THEN
            RAISE EXCEPTION 'core_388 downgrade refused: rsvp policies exist'
                USING ERRCODE = '55000';
        END IF;
    END $$""")
    op.execute("DROP FUNCTION IF EXISTS public.calendar_rsvp_policy_dashboard(text, jsonb)")
    op.execute("DROP TABLE IF EXISTS public.calendar_rsvp_policies")
    op.execute("DROP FUNCTION IF EXISTS public.calendar_rsvp_predicates_valid(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.guard_calendar_rsvp_policies()")
    op.execute("RESET ROLE")
