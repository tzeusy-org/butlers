"""Custody case closure: release and replace close the episode's case when all is resolved.

Revision ID: core_348
Revises: core_347
Create Date: 2026-11-19 00:00:00.000000

openspec change ``endpoint-custody-holds``, REQ-endpoint-custody-holds-010 (bu-s11n0s.1 S5a).
core_347 recorded a release or replace as evidence on the episode's fleet case but never closed
the case. This revision replaces ``public.custody_engine_apply`` (same SECURITY DEFINER owner,
signature, grants and authority model) so that:

- a release or replace closes the episode's case only when no generation of that episode is
  still held (a replace counts as resolved, like a release); a partial recovery leaves the case
  active and the result reports ``remaining_held``;
- the command first locks the targets of every active member of the episode, sorted, then the
  case row, so two concurrent last releases serialize (one closes, none leaves the case stuck)
  and the lock order stays targets before case, as for a hold. A member that joins the episode
  between the membership read and the locks refuses ``membership_changed``;
- ordinary case close is untouched: it writes only ``fleet_cases`` and never reaches the engine,
  so it releases nothing.

The engine closes cases through one new UPDATE policy on the FORCE-RLS ``public.fleet_cases``,
limited to the database owner and ``endpoint_custody:`` cases (and, where another login owns the
table, UPDATE on it for the database owner), beside core_347's INSERT policy.

Installed once across the per-schema core chains, as core_347: a run that finds the new body
installed reads the catalog only; otherwise it installs in a transaction of its own under a short
lock timeout, rolling back and retrying while a sibling chain holds fleet_cases (the policy DDL
needs ACCESS EXCLUSIVE, which a chain at core_288 holds while waiting on the custody lock).
Only the last schema still at core_348 restores core_347's body on downgrade.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "core_348"
down_revision = "core_347"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_293:custody_holds', 0))"
_APPLY_FN = "public.custody_engine_apply(text, text, jsonb)"
_MARKER = "core_348: a case closes only when"

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
    locked text[] := ARRAY[]::text[];
    remaining bigint := 0;
    case_closed boolean := false;
BEGIN
    -- core_348: a case closes only when every held generation of its episode is resolved.
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
        -- Every active member of the episode (and this hold) is locked, targets sorted, before
        -- any row or the case. A concurrent member release then serializes with this one, so
        -- exactly one of two last releases observes nothing left held and closes the case.
        FOR item IN
            SELECT DISTINCT h.target_kind AS kind, h.target_digest AS digest
            FROM public.custody_holds h
            WHERE h.episode_id = hold_row.episode_id AND (h.released_at IS NULL OR h.id = hold_ref)
            ORDER BY 1, 2
        LOOP
            PERFORM pg_advisory_xact_lock(
                hashtextextended('butlers:custody_target:' || item.kind || ':' || item.digest, 0));
            locked := locked || (item.kind || ':' || item.digest);
        END LOOP;
        -- A member that joined the episode after the read above is not locked: refuse rather
        -- than close or leave open on a membership this command did not validate.
        IF EXISTS (
            SELECT 1 FROM public.custody_holds h
            WHERE h.episode_id = hold_row.episode_id AND h.released_at IS NULL
              AND NOT ((h.target_kind || ':' || h.target_digest) = ANY (locked))
        ) THEN
            RETURN '{"outcome":"refused","reason":"membership_changed"}';
        END IF;
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
        SELECT count(*) INTO remaining FROM public.custody_holds h
        WHERE h.episode_id = episode AND h.released_at IS NULL;
    END IF;

    -- One case per episode; content-blind evidence, never an identifier or digest.
    corr := 'endpoint_custody:' || episode::text;
    SELECT id INTO case_ref FROM public.fleet_cases
    WHERE correlation_key = corr AND state <> 'closed'
    FOR UPDATE;
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
        -- Only the last resolved generation closes the case, with the same outcome for a
        -- release or a replace; a partial recovery leaves it active. Closing a case by any
        -- other route never reaches this function, so it never releases a hold.
        IF remaining = 0 THEN
            UPDATE public.fleet_cases SET state = 'closed', outcome = 'custody_resolved',
                closed_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE id = case_ref AND state <> 'closed';
            case_closed := true;
        END IF;
        result := jsonb_build_object(
            'outcome', CASE p_action WHEN 'release' THEN 'released' ELSE 'replaced' END,
            'operation', p_action, 'command_id', cmd, 'episode_id', episode,
            'case_id', case_ref, 'case_closed', case_closed, 'remaining_held', remaining,
            'hold_id', hold_row.id, 'target_kind', hold_row.target_kind,
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

_ASSUME_OWNER = """DO $$ DECLARE db_owner name; BEGIN
    SELECT pg_get_userbyid(datdba) INTO STRICT db_owner FROM pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('SET LOCAL ROLE %I', db_owner);
    END IF;
END $$"""

_CLOSE_POLICY = """DO $$ DECLARE owner_oid oid; db_owner name; BEGIN
    SELECT datdba, pg_get_userbyid(datdba) INTO STRICT owner_oid, db_owner FROM pg_database
        WHERE datname = current_database();
    IF (SELECT relowner FROM pg_class WHERE oid = 'public.fleet_cases'::regclass)
       <> owner_oid THEN
        EXECUTE format('GRANT UPDATE ON public.fleet_cases TO %I', db_owner);
    END IF;
    DROP POLICY IF EXISTS fleet_cases_custody_engine_close ON public.fleet_cases;
    EXECUTE format('CREATE POLICY fleet_cases_custody_engine_close ON public.fleet_cases '
                   'FOR UPDATE USING (current_user = %1$L::name AND '
                   'correlation_key LIKE ''endpoint\\_custody:%%'') '
                   'WITH CHECK (current_user = %1$L::name AND '
                   'correlation_key LIKE ''endpoint\\_custody:%%'')', db_owner);
END $$"""


def _apply_installed(bind) -> bool:
    """Whether the core_348 body stands (catalog read only, no lock).

    A later revision that replaces ``custody_engine_apply`` MUST keep ``_MARKER`` in its body
    (or this check must learn the newer marker), or a lagging chain would reinstall this body.
    """
    return bool(
        bind.exec_driver_sql(
            "SELECT COALESCE(position('" + _MARKER + "' IN "
            "pg_get_functiondef(to_regprocedure('" + _APPLY_FN + "'))) > 0, false)"
        ).scalar()
    )


def _installation_count() -> int:
    """Count schema-local core chains still at core_348 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 348
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _core_347():
    """core_347's module: its install lock, sibling-yield test and timeouts are reused."""
    path = Path(__file__).with_name("core_347_custody_command_engine.py")
    spec = importlib.util.spec_from_file_location("_core_347_custody_command_engine", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous_apply() -> str:
    """core_347's function body, the single source of the previous definition."""
    return _core_347()._APPLY


def _install() -> None:
    op.execute(_ASSUME_OWNER)
    op.execute(_CLOSE_POLICY)
    # CREATE OR REPLACE keeps the owner and every grant core_347 set on the function.
    op.execute(_APPLY)
    op.execute("RESET ROLE")


def _install_yielding(previous, connection) -> None:
    """Install in one explicit transaction that never waits long while holding anything.

    The policy DDL takes ACCESS EXCLUSIVE on ``public.fleet_cases``, which a sibling chain still
    at core_288 holds while it waits for the custody lock at core_293. A short lock timeout makes
    this run the one that backs off, rolls back and retries (core_347's installer pattern).
    """
    deadline = monotonic() + previous._INSTALL_LOCK_WAIT_SECONDS
    while True:
        connection.exec_driver_sql("BEGIN")
        try:
            connection.exec_driver_sql(
                f"SET LOCAL lock_timeout = '{previous._INSTALL_LOCK_TIMEOUT}'"
            )
            if _apply_installed(connection):
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
    if _apply_installed(op.get_bind()):
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
    op.execute("DROP POLICY IF EXISTS fleet_cases_custody_engine_close ON public.fleet_cases")
    # Re-derive the conditional grant: upgrade granted UPDATE only where the database owner does
    # not own the table (core_347 had granted SELECT and INSERT only).
    op.execute("""DO $$ DECLARE owner_oid oid; db_owner name; BEGIN
        SELECT datdba, pg_get_userbyid(datdba) INTO STRICT owner_oid, db_owner FROM pg_database
            WHERE datname = current_database();
        IF (SELECT relowner FROM pg_class WHERE oid = 'public.fleet_cases'::regclass)
           <> owner_oid THEN
            EXECUTE format('REVOKE UPDATE ON public.fleet_cases FROM %I', db_owner);
        END IF;
    END $$""")
    op.execute(_previous_apply())
    op.execute("RESET ROLE")
