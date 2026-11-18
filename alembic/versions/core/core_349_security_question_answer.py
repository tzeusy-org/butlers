"""One durable security question per authenticated event, answered on the dashboard (S5c).

Revision ID: core_349
Revises: core_348
Create Date: 2026-11-19 00:00:00.000000

openspec change ``endpoint-custody-holds``, REQ-endpoint-custody-holds-008, dashboard half
(bu-s11n0s.1 slice 5c). The Telegram callback, ``csq1`` question tokens and the closed provider
inventory fallback are deferred to bu-s11n0s.81.

- ``public.security_questions``: the publisher-local outbox. Switchboard admits one row per
  authenticated ``switchboard.security_event`` (keyed by the event id, so a duplicate observation
  or a consumer retry reuses it) and walks its presentation state forward. A guard trigger makes
  the walk one way (``pending`` to ``presenting`` to a terminal state), so a question that may
  already have been sent is never claimed again. Switchboard owns this table through one RLS
  policy.
- ``public.security_answers``: the owner's decision, written only by the definer below. Switchboard
  may read it (to record the canonical case) but cannot write it. ``yes`` may be raised to ``no``
  but a retained ``no`` is never lowered; rows are never deleted.
- ``public.security_question_dashboard(action, p)``: ``get``, ``list`` and ``answer`` for
  ``dashboard_auth_api`` only. It takes the ``dashboard_auth`` singleton lock first, then checks
  the configured origin, the current cookie session and, for ``answer``, its CSRF token in the
  committing transaction. A ``no`` binds an owner-selected exact target by calling
  ``public.custody_dashboard('hold', ...)`` (core_347) in the same transaction: the hold, its
  fleet case and receipt, and the recorded answer commit together or not at all, and the same
  session and CSRF are rechecked there. Nothing in a request names an actor or an authority.

Lock order: the ``dashboard_auth`` singleton, the question row, then core_347's sorted per-target
locks and rows. Switchboard only ever locks a question row.

Installed once across the per-schema core chains, as core_347: a run that finds the objects
installed reads the catalog only; otherwise it leaves the chain's open transaction, takes a
polled session lock and installs in one transaction of its own under a short lock timeout,
retrying while a sibling chain holds a catalog row it needs. Only the last schema still at
core_349 drops the objects, and downgrade refuses while any question or answer exists.
"""

from __future__ import annotations

from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "core_349"
down_revision = "core_348"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_349:security_questions', 0))"
_INSTALL_LOCK = 349_20261119
_INSTALL_LOCK_WAIT_SECONDS = 300
_INSTALL_LOCK_TIMEOUT = "100ms"
_API = "dashboard_auth_api"
_SWITCHBOARD = "butler_switchboard_rw"

_TABLES = """
CREATE TABLE IF NOT EXISTS public.security_questions (
    event_id UUID PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider <> ''),
    kind TEXT NOT NULL CHECK (kind <> ''),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'presenting', 'presented', 'unavailable', 'ambiguous')),
    detail TEXT CHECK (detail IS NULL OR char_length(detail) <= 64),
    notification_id UUID,
    admitted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    attempted_at TIMESTAMPTZ,
    presented_at TIMESTAMPTZ,
    case_id UUID,
    case_recorded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS security_questions_open_idx
    ON public.security_questions (admitted_at) WHERE state IN ('pending', 'presenting');
CREATE TABLE IF NOT EXISTS public.security_answers (
    event_id UUID PRIMARY KEY,
    answer TEXT NOT NULL CHECK (answer IN ('yes', 'no')),
    command_id UUID,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((answer = 'no') = (command_id IS NOT NULL))
);
"""

_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_security_questions() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'security questions are never deleted' USING ERRCODE = '23514';
    END IF;
    IF NEW.event_id IS DISTINCT FROM OLD.event_id OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.admitted_at IS DISTINCT FROM OLD.admitted_at
       OR (OLD.case_recorded_at IS NOT NULL
           AND (NEW.case_id IS DISTINCT FROM OLD.case_id
                OR NEW.case_recorded_at IS DISTINCT FROM OLD.case_recorded_at)) THEN
        RAISE EXCEPTION 'security question identity is immutable' USING ERRCODE = '23514';
    END IF;
    -- One way: a question that may have reached the owner is never claimed for sending again.
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
        (OLD.state = 'pending' AND NEW.state IN ('presenting', 'unavailable'))
        OR (OLD.state = 'presenting' AND NEW.state IN ('presented', 'unavailable', 'ambiguous'))
    ) THEN
        RAISE EXCEPTION 'security question presentation only moves forward'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS guard_security_questions_rows ON public.security_questions;
CREATE TRIGGER guard_security_questions_rows
    BEFORE UPDATE OR DELETE ON public.security_questions
    FOR EACH ROW EXECUTE FUNCTION public.guard_security_questions();
DROP TRIGGER IF EXISTS guard_security_questions_truncate ON public.security_questions;
CREATE TRIGGER guard_security_questions_truncate
    BEFORE TRUNCATE ON public.security_questions
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_security_questions();

CREATE OR REPLACE FUNCTION public.guard_security_answers() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    -- A retained no is never erased; yes may only be raised to no.
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'security answers are retained' USING ERRCODE = '23514';
    END IF;
    IF OLD.answer <> 'yes' OR NEW.answer <> 'no' OR NEW.event_id IS DISTINCT FROM OLD.event_id THEN
        RAISE EXCEPTION 'security answers are retained' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS guard_security_answers_rows ON public.security_answers;
CREATE TRIGGER guard_security_answers_rows
    BEFORE UPDATE OR DELETE ON public.security_answers
    FOR EACH ROW EXECUTE FUNCTION public.guard_security_answers();
DROP TRIGGER IF EXISTS guard_security_answers_truncate ON public.security_answers;
CREATE TRIGGER guard_security_answers_truncate
    BEFORE TRUNCATE ON public.security_answers
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_security_answers();
"""

# The session and CSRF checks repeat core_347's custody_dashboard, in this transaction.
_DASHBOARD = r"""
CREATE OR REPLACE FUNCTION public.security_question_dashboard(action text, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    s dashboard_auth.instance%ROWTYPE;
    se dashboard_auth.sessions%ROWTYPE;
    t timestamptz;
    ev uuid;
    q public.security_questions%ROWTYPE;
    a public.security_answers%ROWTYPE;
    held jsonb;
    verdict text;
BEGIN
    IF action IS NULL OR action NOT IN ('get', 'list', 'answer')
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
    IF action = 'answer' AND NOT EXISTS (
        SELECT 1 FROM dashboard_auth.csrf
        WHERE session_digest = se.digest AND digest = p->>'csrf_digest' AND expires_at > t
    ) THEN
        RETURN '{"error":"FORBIDDEN"}';
    END IF;
    p := p - ARRAY['origin', 'rp_id', 'key_generation'];

    IF action = 'list' THEN
        IF (p - ARRAY['session_digest', 'csrf_digest']) <> '{}'::jsonb THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        RETURN jsonb_build_object('questions', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'event_id', x.event_id, 'provider', x.provider, 'kind', x.kind,
                'presentation', x.state, 'admitted_at', x.admitted_at,
                'answer', x.answer, 'case_recorded', x.case_recorded_at IS NOT NULL)
                ORDER BY x.admitted_at DESC, x.event_id)
            FROM (SELECT q2.*, a2.answer FROM public.security_questions q2
                  LEFT JOIN public.security_answers a2 USING (event_id)
                  ORDER BY q2.admitted_at DESC, q2.event_id LIMIT 50) x), '[]'::jsonb));
    END IF;

    BEGIN
        ev := (p->>'event_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN '{"error":"BAD_REQUEST"}';
    END;
    IF ev IS NULL THEN RETURN '{"error":"BAD_REQUEST"}'; END IF;

    IF action = 'get' THEN
        IF (p - ARRAY['session_digest', 'csrf_digest', 'event_id']) <> '{}'::jsonb THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
        SELECT * INTO q FROM public.security_questions WHERE event_id = ev;
        IF NOT FOUND THEN
            RETURN '{"outcome":"refused","reason":"unknown_question"}';
        END IF;
        SELECT * INTO a FROM public.security_answers WHERE event_id = ev;
        -- Candidate targets: the owner's own active channel facts, shown as hints only.
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
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
                   ORDER BY ef.predicate, ef.id), '[]'::jsonb)
          INTO held
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.validity = 'active' AND ef.object_kind = 'literal'
          AND ef.predicate IN ('has-email', 'has-handle', 'has-phone')
          AND 'owner' = ANY (COALESCE(e.roles, ARRAY[]::text[]))
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL;
        RETURN jsonb_build_object(
            'event_id', q.event_id, 'provider', q.provider, 'kind', q.kind,
            'presentation', q.state, 'admitted_at', q.admitted_at,
            'answer', a.answer, 'case_recorded', q.case_recorded_at IS NOT NULL,
            'candidates', held);
    END IF;

    -- answer: yes changes nothing; no binds an exact owner-selected target through core_347.
    verdict := p->>'answer';
    IF verdict = 'yes' THEN
        IF (p - ARRAY['session_digest', 'csrf_digest', 'event_id', 'answer']) <> '{}'::jsonb THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
    ELSIF verdict = 'no' THEN
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(p) k
                   WHERE k NOT IN ('session_digest', 'csrf_digest', 'event_id', 'answer',
                                   'command_id', 'fact_ids', 'reason')) THEN
            RETURN '{"error":"BAD_REQUEST"}';
        END IF;
    ELSE
        RETURN '{"error":"BAD_REQUEST"}';
    END IF;

    -- The question row serializes yes against no and duplicate retries.
    SELECT * INTO q FROM public.security_questions WHERE event_id = ev FOR UPDATE;
    IF NOT FOUND THEN
        RETURN '{"outcome":"refused","reason":"unknown_question"}';
    END IF;
    IF verdict = 'yes' THEN
        INSERT INTO public.security_answers (event_id, answer) VALUES (ev, 'yes')
        ON CONFLICT (event_id) DO NOTHING;
        SELECT * INTO a FROM public.security_answers WHERE event_id = ev;
        RETURN jsonb_build_object('outcome', 'answered', 'event_id', ev, 'answer', a.answer,
                                  'retained', a.answer = 'no');
    END IF;

    -- The caller's own session, origin and CSRF are rechecked by the hold engine.
    held := public.custody_dashboard('hold', (p || jsonb_build_object(
        'origin', s.origin, 'rp_id', s.rp_id, 'key_generation', s.key_generation))
        - ARRAY['event_id', 'answer']);
    IF held ->> 'error' IS NOT NULL
       OR COALESCE(held ->> 'outcome', '') NOT IN ('held', 'already_held') THEN
        RETURN held;
    END IF;
    INSERT INTO public.security_answers (event_id, answer, command_id)
    VALUES (ev, 'no', (held->>'command_id')::uuid)
    ON CONFLICT (event_id) DO UPDATE
        SET answer = 'no', command_id = EXCLUDED.command_id, answered_at = clock_timestamp()
        WHERE public.security_answers.answer = 'yes';
    RETURN held || jsonb_build_object('event_id', ev, 'answer', 'no');
END
$fn$;
"""

_FUNCTIONS = (
    "public.security_question_dashboard(text, jsonb)",
    "public.guard_security_questions()",
    "public.guard_security_answers()",
)
_TABLE_NAMES = ("public.security_questions", "public.security_answers")

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
    core_347 for a deployment where another login does.
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
    """Switchboard owns the outbox, reads the answers; only the definer writes answers."""
    for table in _TABLE_NAMES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    # init-db.sql's default privileges hand runtime roles DML on new public tables; start from
    # nothing. Row level security still fences a later replay.
    op.execute(f"""DO $$ DECLARE role_name text; BEGIN
        FOR role_name IN SELECT rolname FROM pg_roles
            WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
               OR rolname IN ('connector_writer', '{_API}')
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.security_questions, '
                           'public.security_answers FROM %I', role_name);
        END LOOP;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SWITCHBOARD}') THEN
            GRANT SELECT, INSERT, UPDATE ON TABLE public.security_questions TO {_SWITCHBOARD};
            GRANT SELECT ON TABLE public.security_answers TO {_SWITCHBOARD};
        END IF;
    END $$""")
    op.execute("DROP POLICY IF EXISTS security_questions_switchboard ON public.security_questions")
    op.execute(
        f"""CREATE POLICY security_questions_switchboard ON public.security_questions
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD}') WITH CHECK (current_user = '{_SWITCHBOARD}')"""
    )
    op.execute("DROP POLICY IF EXISTS security_answers_switchboard ON public.security_answers")
    op.execute(
        f"""CREATE POLICY security_answers_switchboard ON public.security_answers
            FOR SELECT TO PUBLIC USING (current_user = '{_SWITCHBOARD}')"""
    )
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_API}') THEN
            RAISE EXCEPTION 'core_349 requires the {_API} role from core_240';
        END IF;
    END $$""")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.security_question_dashboard(text, jsonb) TO {_API}"
    )


def _installation_count() -> int:
    """Count schema-local core chains still at core_349 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 349
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _installed(bind) -> bool:
    """Whether an earlier schema's run committed the objects (catalog reads only)."""
    return bool(
        bind.exec_driver_sql(
            """
            SELECT to_regclass('public.security_questions') IS NOT NULL
               AND to_regclass('public.security_answers') IS NOT NULL
               AND to_regprocedure('public.security_question_dashboard(text, jsonb)') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM pg_policy
                    WHERE polrelid = to_regclass('public.security_answers')
                      AND polname = 'security_answers_switchboard'
               )
            """
        ).scalar()
    )


def _acquire_install_lock(bind) -> None:
    # Session lock polled in autocommit, as core_347 does: a blocking wait would hold a snapshot.
    deadline = monotonic() + _INSTALL_LOCK_WAIT_SECONDS
    while not bind.exec_driver_sql(f"SELECT pg_try_advisory_lock({_INSTALL_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("security question install is busy; retry this upgrade")
        sleep(0.1)


def _install() -> None:
    op.execute(_LOCK)
    _database_owner_access()
    op.execute(_ASSUME_OWNER)
    op.execute(_TABLES)
    op.execute(_GUARD)
    op.execute(_DASHBOARD)
    _fence()
    op.execute("RESET ROLE")


def _yields_to_a_sibling(error: DBAPIError) -> bool:
    code = getattr(error.orig, "pgcode", None)
    return code in {"55P03", "40P01"} or (
        code == "XX000" and "tuple concurrently updated" in str(error.orig)
    )


def _install_yielding(connection) -> None:
    """Install in one explicit transaction that never waits long while holding anything."""
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
    op.execute(_ASSUME_OWNER)
    # Row security hides rows from anyone but the owner: only the owner can prove none exist.
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.security_questions') IS NULL THEN
            RETURN;
        END IF;
        IF NOT pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid = to_regclass('public.security_questions')), 'USAGE') THEN
            RAISE EXCEPTION 'core_349 downgrade refused: only the maintenance owner can prove '
                'no security question exists' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (SELECT 1 FROM public.security_questions)
           OR EXISTS (SELECT 1 FROM public.security_answers) THEN
            RAISE EXCEPTION 'core_349 downgrade refused: security questions or answers exist'
                USING ERRCODE = '55000';
        END IF;
    END $$""")
    op.execute("DROP FUNCTION IF EXISTS public.security_question_dashboard(text, jsonb)")
    op.execute("DROP TABLE IF EXISTS public.security_answers")
    op.execute("DROP TABLE IF EXISTS public.security_questions")
    op.execute("DROP FUNCTION IF EXISTS public.guard_security_answers()")
    op.execute("DROP FUNCTION IF EXISTS public.guard_security_questions()")
    op.execute("RESET ROLE")
