"""Endpoint custody command engine: protected hold, release and replace (bu-s11n0s.1 slice 2a).

Revision ID: core_347
Revises: core_346
Create Date: 2026-11-17 00:00:00.000000

openspec change ``endpoint-custody-holds``, REQ-endpoint-custody-holds-001 to -004, protected
half only. core_293 installed the hold record with no runtime writer; this revision adds the one
writer, reachable only from a current owner dashboard session or an explicit host act. The cheap
owner-resolved LOCK (endpoint_lock) and the supported-process source protocol (P-CUSTODY-1c) are
not built here: both wait for the RFC 0038 cutover.

- ``public.custody_commands``: the immutable receipt of every committed command, keyed by the
  caller's command id with a digest of its canonical request. A replay returns the stored result;
  the same id with a different request refuses. A lost acknowledgement is read back by id.
  Row level security with no policy, owned by the database owner like ``custody_holds``.
- ``public.custody_engine_apply(authority, action, p)``: hold, release, replace, status and list.
  A hold names owner channel facts (``relationship.entity_facts`` ids), never raw identifiers;
  each must be an active owner fact that ``resolve_owner_triple`` resolves to that owner alone,
  so an ambiguous identifier is refused. Release and replace compare the expected generation.
  Replace is a release that refuses while the owner still holds an active fact for the target
  (for a phone, any spelling with the same trailing eight digits), so a replaced endpoint never
  regains owner authority. Every committed command opens or reuses one fleet case per episode
  (``endpoint_custody:<episode>``), adds content-blind evidence and writes its receipt in the same
  transaction; any failure rolls all of it back.
- ``public.custody_dashboard(action, p)``: EXECUTE for ``dashboard_auth_api`` only. It takes the
  ``dashboard_auth`` singleton lock first, then checks the configured origin, the current cookie
  session and, for a mutation, its CSRF token, in the committing transaction.
- ``public.custody_host(action, p)``: no grant; the host's administrative login, as owner, runs
  it. A mutation needs an explicit ``confirm``. It takes the same singleton lock first.

Lock order: the ``dashboard_auth`` singleton, then sorted per-target advisory locks, then the hold
rows, then the case, evidence and receipt.

Installed once across the per-schema core chains, as core_343 and core_345: a run that finds the
engine installed reads the catalog only. Otherwise it leaves the chain's open transaction (which
still holds core_346's row lock on ``connectors.filtered_events``), takes a polled session lock and
installs in one transaction of its own under a short lock timeout, rolling back and retrying
while a sibling chain holds a row it needs. Granting on schema ``public`` while the earlier lock
was held deadlocked against a sibling chain a few revision segments behind (core_315 and core_336
grant on the same ``pg_namespace`` row). Only the last schema still at core_347 drops the objects,
and downgrade refuses while any receipt exists.
"""

from __future__ import annotations

from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "core_347"
down_revision = "core_346"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_293:custody_holds', 0))"
_INSTALL_LOCK = 347_20261117
_INSTALL_LOCK_WAIT_SECONDS = 300
#: Well under ``deadlock_timeout`` (1s), so this run gives up before a sibling's deadlock check.
_INSTALL_LOCK_TIMEOUT = "100ms"
_API = "dashboard_auth_api"

_TABLE = """
CREATE TABLE IF NOT EXISTS public.custody_commands (
    command_id UUID PRIMARY KEY,
    operation TEXT NOT NULL CHECK (operation IN ('hold', 'release', 'replace')),
    authority TEXT NOT NULL CHECK (authority IN ('dashboard', 'host')),
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    episode_id UUID NOT NULL,
    case_id UUID NOT NULL,
    result JSONB NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
"""

_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_custody_commands() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    RAISE EXCEPTION 'custody command receipts are immutable' USING ERRCODE = '23514';
END
$fn$;
DROP TRIGGER IF EXISTS guard_custody_commands_rows ON public.custody_commands;
CREATE TRIGGER guard_custody_commands_rows
    BEFORE UPDATE OR DELETE ON public.custody_commands
    FOR EACH ROW EXECUTE FUNCTION public.guard_custody_commands();
DROP TRIGGER IF EXISTS guard_custody_commands_truncate ON public.custody_commands;
CREATE TRIGGER guard_custody_commands_truncate
    BEFORE TRUNCATE ON public.custody_commands
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_custody_commands();
"""

_APPLY = r"""
CREATE OR REPLACE FUNCTION public.custody_engine_apply(
    p_authority text,
    p_action text,
    p jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    cmd uuid;
    req jsonb;
    req_digest text;
    prior public.custody_commands%ROWTYPE;
    fact_ids uuid[];
    hold_reason text;
    targets jsonb;
    owner_id uuid;
    episode uuid;
    episodes uuid[];
    case_ref uuid;
    corr text;
    item record;
    hold_row public.custody_holds%ROWTYPE;
    hold_ref uuid;
    expected bigint;
    holds jsonb := '[]'::jsonb;
    created boolean := false;
    result jsonb;
BEGIN
    IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    IF p_action = 'list' THEN
        IF p <> '{}'::jsonb THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;
        RETURN jsonb_build_object('holds', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'hold_id', h.id, 'target_kind', h.target_kind, 'generation', h.generation,
                'episode_id', h.episode_id, 'reason', h.reason, 'held_from', h.held_from)
                ORDER BY h.held_from, h.id)
            FROM public.custody_holds h WHERE h.released_at IS NULL), '[]'::jsonb));
    END IF;
    BEGIN
        cmd := (p->>'command_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END;
    IF cmd IS NULL THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;

    IF p_action = 'status' THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k WHERE k <> 'command_id') THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        SELECT * INTO prior FROM public.custody_commands WHERE command_id = cmd;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('outcome', 'unknown_command', 'command_id', cmd);
        END IF;
        RETURN prior.result;
    END IF;

    -- Closed request shapes: no caller field names an actor, an owner or an authority.
    IF p_action = 'hold' THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k
                   WHERE k NOT IN ('command_id', 'fact_ids', 'reason'))
           OR jsonb_typeof(p->'fact_ids') IS DISTINCT FROM 'array'
           OR jsonb_array_length(p->'fact_ids') NOT BETWEEN 1 AND 16
           OR p->>'reason' IS NULL
           OR p->>'reason' NOT IN ('lost', 'stolen', 'replaced', 'disowned_access') THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        BEGIN
            SELECT array_agg(DISTINCT value::uuid ORDER BY value::uuid) INTO fact_ids
            FROM jsonb_array_elements_text(p->'fact_ids');
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END;
        hold_reason := p->>'reason';
        req := jsonb_build_object('operation', 'hold', 'fact_ids', to_jsonb(fact_ids),
                                  'reason', hold_reason);
    ELSIF p_action IN ('release', 'replace') THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k
                   WHERE k NOT IN ('command_id', 'hold_id', 'expected_generation'))
           OR jsonb_typeof(p->'expected_generation') IS DISTINCT FROM 'number' THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        BEGIN
            hold_ref := (p->>'hold_id')::uuid;
            expected := (p->>'expected_generation')::bigint;
        EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END;
        IF hold_ref IS NULL OR expected IS NULL OR expected < 1 THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        req := jsonb_build_object('operation', p_action, 'hold_id', hold_ref,
                                  'expected_generation', expected);
    ELSE
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    req_digest := encode(sha256(convert_to(req::text, 'UTF8')), 'hex');

    -- Idempotent replay: the receipt answers; it never executes the request again.
    SELECT * INTO prior FROM public.custody_commands WHERE command_id = cmd;
    IF FOUND THEN
        IF prior.request_digest = req_digest AND prior.authority = p_authority THEN
            RETURN prior.result || jsonb_build_object('replayed', true);
        END IF;
        RETURN '{"outcome":"refused","reason":"command_conflict"}';
    END IF;

    IF p_action = 'hold' THEN
        -- Every target is an active literal channel fact of one live owner entity.
        SELECT jsonb_agg(jsonb_build_object(
                   'fact_id', ef.id, 'owner', ef.subject, 'kind', ef.predicate,
                   'object', ef.object,
                   'digest', public.custody_target_digest(ef.predicate, ef.object),
                   'candidate', CASE WHEN ef.predicate = 'has-phone'
                       THEN 'phone-digits:' || regexp_replace(ef.object, '\D', '', 'g')
                       ELSE ef.predicate || ':' || lower(btrim(ef.object)) END)
                   ORDER BY ef.predicate, public.custody_target_digest(ef.predicate, ef.object))
          INTO targets
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.id = ANY (fact_ids)
          AND ef.validity = 'active'
          AND ef.object_kind = 'literal'
          AND ef.predicate IN ('has-email', 'has-handle', 'has-phone')
          AND 'owner' = ANY (COALESCE(e.roles, ARRAY[]::text[]))
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL;
        IF targets IS NULL OR jsonb_array_length(targets) <> cardinality(fact_ids) THEN
            RETURN '{"outcome":"refused","reason":"not_owner_target"}';
        END IF;
        IF (SELECT count(DISTINCT t->>'owner') FROM jsonb_array_elements(targets) t) <> 1 THEN
            RETURN '{"outcome":"refused","reason":"mixed_owners"}';
        END IF;
        owner_id := (targets->0->>'owner')::uuid;

        -- Targets before rows, in one sorted order, so concurrent commands serialize.
        FOR item IN
            SELECT DISTINCT t->>'kind' AS kind, t->>'digest' AS digest
            FROM jsonb_array_elements(targets) t ORDER BY 1, 2
        LOOP
            PERFORM pg_advisory_xact_lock(
                hashtextextended('butlers:custody_target:' || item.kind || ':' || item.digest, 0));
        END LOOP;

        -- An unheld target must resolve to this owner alone; ambiguity is never held.
        FOR item IN SELECT t FROM jsonb_array_elements(targets) t LOOP
            IF NOT EXISTS (
                SELECT 1 FROM public.custody_holds h
                WHERE h.released_at IS NULL AND h.target_kind = item.t->>'kind'
                  AND h.target_digest = item.t->>'digest'
            ) AND NOT EXISTS (
                SELECT 1 FROM public.resolve_owner_triple(
                    'owner-channel', ARRAY[item.t->>'candidate']) r
                WHERE r.entity_id = owner_id
            ) THEN
                RETURN '{"outcome":"refused","reason":"unresolved_target"}';
            END IF;
        END LOOP;

        SELECT array_agg(DISTINCT h.episode_id) INTO episodes
        FROM public.custody_holds h
        JOIN jsonb_array_elements(targets) t
          ON h.target_kind = t->>'kind' AND h.target_digest = t->>'digest'
        WHERE h.released_at IS NULL;
        IF cardinality(episodes) > 1 THEN
            RETURN '{"outcome":"refused","reason":"episode_conflict"}';
        END IF;
        episode := COALESCE(episodes[1], gen_random_uuid());

        FOR item IN
            SELECT DISTINCT t->>'kind' AS kind, t->>'digest' AS digest
            FROM jsonb_array_elements(targets) t ORDER BY 1, 2
        LOOP
            SELECT * INTO hold_row FROM public.custody_holds h
            WHERE h.released_at IS NULL AND h.target_kind = item.kind
              AND h.target_digest = item.digest
            FOR UPDATE;
            IF FOUND THEN
                holds := holds || jsonb_build_object('hold_id', hold_row.id,
                    'target_kind', hold_row.target_kind, 'generation', hold_row.generation,
                    'state', 'already_held');
            ELSE
                INSERT INTO public.custody_holds (owner_entity_id, target_kind, target_digest,
                    generation, episode_id, reason, source_command_id)
                VALUES (owner_id, item.kind, item.digest,
                    (SELECT COALESCE(max(h.generation), 0) + 1 FROM public.custody_holds h
                     WHERE h.target_kind = item.kind AND h.target_digest = item.digest),
                    episode, hold_reason, cmd)
                RETURNING * INTO hold_row;
                created := true;
                holds := holds || jsonb_build_object('hold_id', hold_row.id,
                    'target_kind', hold_row.target_kind, 'generation', hold_row.generation,
                    'state', 'held');
            END IF;
        END LOOP;
    ELSE
        SELECT * INTO hold_row FROM public.custody_holds WHERE id = hold_ref;
        IF NOT FOUND THEN
            RETURN '{"outcome":"refused","reason":"not_found"}';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'butlers:custody_target:' || hold_row.target_kind || ':' || hold_row.target_digest, 0));
        SELECT * INTO hold_row FROM public.custody_holds WHERE id = hold_ref FOR UPDATE;
        IF hold_row.released_at IS NOT NULL THEN
            RETURN '{"outcome":"refused","reason":"not_active"}';
        END IF;
        IF hold_row.generation <> expected THEN
            RETURN '{"outcome":"refused","reason":"stale_generation"}';
        END IF;
        -- Replaced means the owner no longer holds the binding; while any active owner fact
        -- still maps to the target, stamping it ended would restore owner authority.
        IF p_action = 'replace' AND EXISTS (
            SELECT 1 FROM relationship.entity_facts ef
            WHERE ef.subject = hold_row.owner_entity_id
              AND ef.validity = 'active'
              AND ef.object_kind = 'literal'
              AND ef.predicate = hold_row.target_kind
              AND (
                  public.custody_target_digest(ef.predicate, ef.object) = hold_row.target_digest
                  OR (
                      ef.predicate = 'has-phone'
                      AND length(regexp_replace(ef.object, '\D', '', 'g')) >= 8
                      AND EXISTS (
                          SELECT 1 FROM relationship.entity_facts held
                          WHERE held.subject = hold_row.owner_entity_id
                            AND held.predicate = 'has-phone'
                            AND held.object_kind = 'literal'
                            AND public.custody_target_digest('has-phone', held.object)
                                = hold_row.target_digest
                            AND length(regexp_replace(held.object, '\D', '', 'g')) >= 8
                            AND right(regexp_replace(held.object, '\D', '', 'g'), 8)
                                = right(regexp_replace(ef.object, '\D', '', 'g'), 8)
                      )
                  )
              )
        ) THEN
            RETURN '{"outcome":"refused","reason":"still_bound"}';
        END IF;
        UPDATE public.custody_holds SET released_at = clock_timestamp()
        WHERE id = hold_ref
        RETURNING * INTO hold_row;
        episode := hold_row.episode_id;
        owner_id := hold_row.owner_entity_id;
    END IF;

    -- One case per episode; content-blind evidence, never an identifier or digest.
    corr := 'endpoint_custody:' || episode::text;
    SELECT id INTO case_ref FROM public.fleet_cases
    WHERE correlation_key = corr AND state <> 'closed';
    IF NOT FOUND THEN
        INSERT INTO public.fleet_cases (correlation_key) VALUES (corr) RETURNING id INTO case_ref;
    END IF;
    IF p_action = 'hold' THEN
        INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref, payload)
        SELECT case_ref, 'endpoint_custody', 'hold', h->>'hold_id',
               jsonb_build_object('target_kind', h->>'target_kind',
                   'generation', (h->>'generation')::bigint, 'reason', hold_reason,
                   'command_id', cmd)
        FROM jsonb_array_elements(holds) h WHERE h->>'state' = 'held'
        ON CONFLICT (case_id, contributor, kind, ref) DO NOTHING;
        result := jsonb_build_object(
            'outcome', CASE WHEN created THEN 'held' ELSE 'already_held' END,
            'operation', 'hold', 'command_id', cmd, 'episode_id', episode,
            'case_id', case_ref, 'holds', holds);
    ELSE
        INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref, payload)
        VALUES (case_ref, 'endpoint_custody', p_action,
                hold_row.id::text || ':' || hold_row.generation::text,
                jsonb_build_object('target_kind', hold_row.target_kind,
                    'generation', hold_row.generation, 'command_id', cmd))
        ON CONFLICT (case_id, contributor, kind, ref) DO NOTHING;
        result := jsonb_build_object(
            'outcome', CASE p_action WHEN 'release' THEN 'released' ELSE 'replaced' END,
            'operation', p_action, 'command_id', cmd, 'episode_id', episode,
            'case_id', case_ref, 'hold_id', hold_row.id, 'target_kind', hold_row.target_kind,
            'generation', hold_row.generation,
            'disposition', CASE p_action WHEN 'release' THEN 'released' ELSE 'replaced' END);
    END IF;

    INSERT INTO public.custody_commands (command_id, operation, authority, request_digest,
        episode_id, case_id, result)
    VALUES (cmd, p_action, p_authority, req_digest, episode, case_ref, result);
    RETURN result;
END
$fn$;
"""

# The dashboard_auth singleton lock comes first, as for every owner-auth writer, so a session
# revocation either commits before this check or waits for this command to commit.
_DASHBOARD = """
CREATE OR REPLACE FUNCTION public.custody_dashboard(action text, p jsonb)
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
    IF action IS NULL OR action NOT IN ('hold', 'release', 'replace', 'status', 'list')
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
    IF action IN ('hold', 'release', 'replace') AND NOT EXISTS (
        SELECT 1 FROM dashboard_auth.csrf
        WHERE session_digest = se.digest AND digest = p->>'csrf_digest' AND expires_at > t
    ) THEN
        RETURN '{"error":"FORBIDDEN"}';
    END IF;
    RETURN public.custody_engine_apply('dashboard', action,
        p - ARRAY['origin', 'rp_id', 'key_generation', 'session_digest', 'csrf_digest']);
END
$fn$;
"""

_HOST = """
CREATE OR REPLACE FUNCTION public.custody_host(action text, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    IF action IS NULL OR action NOT IN ('hold', 'release', 'replace', 'status', 'list')
       OR p IS NULL OR jsonb_typeof(p) <> 'object' THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;
    IF action IN ('hold', 'release', 'replace')
       AND (p->'confirm') IS DISTINCT FROM 'true'::jsonb THEN
        RETURN '{"error":"FORBIDDEN"}';
    END IF;
    PERFORM 1 FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    RETURN public.custody_engine_apply('host', action, p - 'confirm');
END
$fn$;
"""

_FUNCTIONS = (
    "public.custody_engine_apply(text, text, jsonb)",
    "public.custody_dashboard(text, jsonb)",
    "public.custody_host(text, jsonb)",
    "public.guard_custody_commands()",
)


def _database_owner_access() -> None:
    """Let the database owner, who owns every custody object, reach what the engine touches.

    In a normal deployment the database owner already owns ``dashboard_auth`` and the fleet case
    tables, and each grant below is a no-op. Where another login owns them, the owner gets only
    the session reads and the singleton lock (as ``dashboard_attention_owner`` has), case and
    evidence SELECT/INSERT, and one INSERT policy on the FORCE-RLS case table.
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
        IF (SELECT relowner FROM pg_class WHERE oid = 'public.fleet_cases'::regclass)
           <> owner_oid THEN
            EXECUTE format('GRANT SELECT, INSERT ON public.fleet_cases, '
                           'public.fleet_case_evidence TO %I', db_owner);
        END IF;
        DROP POLICY IF EXISTS fleet_cases_custody_engine ON public.fleet_cases;
        EXECUTE format('CREATE POLICY fleet_cases_custody_engine ON public.fleet_cases '
                       'FOR INSERT WITH CHECK (current_user = %L::name)', db_owner);
    END $$""")


# Every custody object belongs to the database owner (core_293/core_295 posture). A NOINHERIT
# executor that is only a member of that owner could hand a new object over but could not then
# alter, re-grant or replace it, so it assumes the owner for the whole build (as core_296 does
# for the resolver) and resets afterwards. An executor that is the owner changes nothing.
_ASSUME_OWNER = """DO $$ DECLARE db_owner name; BEGIN
    SELECT pg_get_userbyid(datdba) INTO STRICT db_owner FROM pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('SET LOCAL ROLE %I', db_owner);
    END IF;
END $$"""


def _fence() -> None:
    """Nothing but the owner's definers reaches the receipts or the engine."""
    op.execute("""DO $$ DECLARE trusted_owner oid; BEGIN
        SELECT datdba INTO STRICT trusted_owner FROM pg_database
            WHERE datname = current_database();
        IF EXISTS (SELECT 1 FROM pg_roles
            WHERE (rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\' OR rolname IN
                   ('connector_writer', 'dashboard_auth_api'))
              AND (rolsuper OR rolbypassrls OR pg_has_role(oid, trusted_owner, 'MEMBER'))) THEN
            RAISE EXCEPTION 'Runtime may not bypass or assume custody maintenance owner';
        END IF;
    END $$""")
    op.execute("""
        ALTER TABLE public.custody_commands ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.custody_commands NO FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON TABLE public.custody_commands FROM PUBLIC;
    """)
    # init-db.sql's default privileges hand runtime roles DML on new public tables; start from
    # nothing. Row level security still fences a later replay.
    op.execute("""DO $$ DECLARE role_name text; BEGIN
        FOR role_name IN SELECT rolname FROM pg_roles
            WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
               OR rolname IN ('connector_writer', 'dashboard_auth_api')
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.custody_commands FROM %I', role_name);
        END LOOP;
    END $$""")
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_API}') THEN
            RAISE EXCEPTION 'core_347 requires the {_API} role from core_240';
        END IF;
    END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION public.custody_engine_apply(text, text, jsonb) FROM {_API}")
    op.execute(f"REVOKE ALL ON FUNCTION public.custody_host(text, jsonb) FROM {_API}")
    # PUBLIC normally keeps USAGE on schema public; grant it only where a bootstrap removed it.
    op.execute(f"""DO $$ BEGIN
        IF NOT has_schema_privilege('{_API}', 'public', 'USAGE') THEN
            GRANT USAGE ON SCHEMA public TO {_API};
        END IF;
    END $$""")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.custody_dashboard(text, jsonb) TO {_API}")


def _installation_count() -> int:
    """Count schema-local core chains still at core_347 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 347
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _installed(bind) -> bool:
    """Whether an earlier schema's run committed the engine (catalog reads only).

    The install is one transaction, so its last objects stand for all of it.
    """
    return bool(
        bind.exec_driver_sql(
            """
            SELECT to_regclass('public.custody_commands') IS NOT NULL
               AND to_regprocedure('public.custody_dashboard(text, jsonb)') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM pg_policy
                    WHERE polrelid = 'public.fleet_cases'::regclass
                      AND polname = 'fleet_cases_custody_engine'
               )
            """
        ).scalar()
    )


def _acquire_install_lock(bind) -> None:
    # Session lock polled in autocommit, as core_345 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("custody command engine install is busy; retry this upgrade")
        sleep(0.1)


def _install() -> None:
    op.execute(_LOCK)
    # As the executor, which owns dashboard_auth and the fleet case tables it created.
    _database_owner_access()
    op.execute(_ASSUME_OWNER)
    op.execute(_TABLE)
    op.execute(_GUARD)
    op.execute(_APPLY)
    op.execute(_DASHBOARD)
    op.execute(_HOST)
    _fence()
    op.execute("RESET ROLE")


def _yields_to_a_sibling(error: DBAPIError) -> bool:
    """A lock wait timed out, a deadlock was broken, or a sibling committed a catalog row first."""
    code = getattr(error.orig, "pgcode", None)
    return code in {"55P03", "40P01"} or (
        code == "XX000" and "tuple concurrently updated" in str(error.orig)
    )


def _install_yielding(connection) -> None:
    """Install in one explicit transaction that never waits long while holding anything.

    The install grants on shared catalog rows (schema ``public``, the fleet case table) that a
    sibling chain, still revisions behind, may hold or be about to take while it waits for this
    run. A short lock timeout makes this run the one that backs off: it rolls back, releasing
    everything, and retries until the sibling's segment commits.
    """
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
    # Every later schema's run lands here: catalog reads only, no lock, no catalog write.
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
    op.execute("DROP POLICY IF EXISTS fleet_cases_custody_engine ON public.fleet_cases")
    op.execute(_ASSUME_OWNER)
    # Row security hides receipts from anyone but the owner: only the owner can prove none.
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.custody_commands') IS NULL THEN
            RETURN;
        END IF;
        IF NOT pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid = 'public.custody_commands'::regclass), 'USAGE') THEN
            RAISE EXCEPTION 'core_347 downgrade refused: only the custody maintenance owner '
                'can prove no custody command receipt exists' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (SELECT 1 FROM public.custody_commands) THEN
            RAISE EXCEPTION 'core_347 downgrade refused: custody command receipts exist'
                USING ERRCODE = '55000';
        END IF;
    END $$""")
    op.execute("DROP FUNCTION IF EXISTS public.custody_dashboard(text, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.custody_host(text, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.custody_engine_apply(text, text, jsonb)")
    op.execute("DROP TABLE IF EXISTS public.custody_commands")
    op.execute("DROP FUNCTION IF EXISTS public.guard_custody_commands()")
    op.execute("RESET ROLE")
