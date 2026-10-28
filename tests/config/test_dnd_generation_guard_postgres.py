"""Real-PostgreSQL execution proofs for the canonical DND guard.

These tests are intentionally integration-only.  They exercise the trusted
bootstrap, ordinary core migration, actual role catalog, FORCE RLS, invoker /
definer chain, and durable replay receipt.  They must run only in an authorized
testcontainer/database environment; source-only review does not run them.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.db_authority.interfaces import (
    FUNCTIONS,
    function_matches,
    inspect_function,
    install_revision_group,
)
from butlers.db_authority.roles import AuthorityError
from butlers.migrations import (
    migration_session,
    ordinary_migration_identity,
    run_migrations,
)
from butlers.testing.migration import (
    _upgrade_chain_to_revision,
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# Only disposable fixture URLs are registered. The normal owner never acquires
# membership in serving roles; actual SET ROLE probes use the separately selected
# testcontainer maintenance connection. No fixture GRANT or runtime bypass exists.
_ROLE_PROBE_URLS: dict[str, str] = {}


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Current real migration/finalization, with a distinct normal migration URL."""
    name = migration_db_name()
    url = create_migrated_test_db(postgres_container, name, chains=["core"])
    _ROLE_PROBE_URLS[url] = migration_bootstrap_db_url(postgres_container, name)
    try:
        yield url
    finally:
        _ROLE_PROBE_URLS.pop(url, None)


def _probe_url(db_url: str) -> str:
    return _ROLE_PROBE_URLS.get(db_url, db_url)


def _observer_engine(db_url: str):
    return create_engine(
        _probe_url(db_url),
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c lock_timeout=5000",
        },
    )


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _execute_as_role(
    db_url: str,
    role_name: str,
    statement: str,
    params: dict | None = None,
    *,
    fetch_one: bool = False,
    return_rowcount: bool = False,
):
    """Execute one statement as an actual runtime role, not a setup owner.

    Receipt and DML-rowcount queries opt into a result so it is materialized
    before the role-bound connection is reset and disposed.
    """
    engine = create_engine(
        _probe_url(db_url),
        isolation_level="AUTOCOMMIT",
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c lock_timeout=5000",
        },
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {_quote_ident(role_name)}"))
            try:
                result = conn.execute(text(statement), params or {})
                if fetch_one:
                    return result.one()
                if return_rowcount:
                    return result.rowcount
                return None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def _execute_as_connecting_user(db_url: str, statement: str, params: dict | None = None):
    """Execute as the ordinary un-set connecting role.

    This connection is the actual normal NOCREATEDB migration login, not the
    maintenance role used by runtime SET ROLE probes. It receives neither a
    runtime membership nor direct canonical-writer execution authority.
    """
    engine = create_engine(
        db_url,
        isolation_level="AUTOCOMMIT",
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c lock_timeout=5000",
        },
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(statement), params or {})
    finally:
        engine.dispose()


def _dnd_set_statement(writer: str = "general") -> str:
    """Return the canonical set call used by real-role tests."""
    if writer not in {"general", "switchboard"}:
        raise ValueError(f"unsupported canonical DND writer {writer!r}")
    return f"""
        SELECT mutation_id, generation, writer, operation, correlation,
               requested_expires_at, effective_expires_at, committed_at
        FROM public.context_dnd_mutate(
            CAST(:mutation_id AS uuid),
            '{writer}',
            'set',
            :expires_at,
            :value,
            CAST(:confidence AS real),
            CAST(:metadata AS jsonb)
        )
    """


def _dnd_clear_statement(writer: str) -> str:
    """Return a fixed writer-owned clear call with no set payload fields."""
    if writer not in {"general", "switchboard"}:
        raise ValueError(f"unsupported canonical DND writer {writer!r}")
    return f"""
        SELECT mutation_id, generation, writer, operation, correlation,
               requested_expires_at, effective_expires_at, committed_at
        FROM public.context_dnd_mutate(
            CAST(:mutation_id AS uuid),
            '{writer}',
            'clear',
            NULL,
            NULL,
            NULL,
            NULL
        )
    """


def _run_core_chain_through_dnd_predecessor(db_url: str) -> None:
    """Reach the actual core_196 state before exercising core_197's installer."""
    with migration_session(_probe_url(db_url), source_database="butlers") as connection:
        _upgrade_chain_to_revision(
            db_url, chain="core", schema=None, revision="core_122", connection=connection
        )
        connection.commit()
        asyncio.run(
            run_migrations(
                db_url, chain="relationship", schema="relationship", connection=connection
            )
        )
        _upgrade_chain_to_revision(
            db_url, chain="core", schema=None, revision="core_196", connection=connection
        )
        connection.commit()


def test_dnd_bootstrap_rejects_any_preexisting_user_context_policy(postgres_container) -> None:
    """A permissive policy must never be adopted into the forced-RLS handoff."""
    name = migration_db_name()
    db_url = create_migration_db(postgres_container, name)
    _ROLE_PROBE_URLS[db_url] = migration_bootstrap_db_url(postgres_container, name)
    try:
        _run_core_chain_through_dnd_predecessor(db_url)

        engine = create_engine(
            db_url,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
        try:
            with engine.connect() as conn:
                conn.execute(
                    text(
                        """
                        CREATE POLICY dnd_policy_poison
                        ON public.user_context
                        FOR ALL TO PUBLIC
                        USING (true)
                        WITH CHECK (true)
                        """
                    )
                )
        finally:
            engine.dispose()

        with pytest.raises(DBAPIError, match="authority interface must be absent") as poisoned:
            with migration_session(_probe_url(db_url), source_database="butlers") as connection:
                _upgrade_chain_to_revision(
                    db_url, chain="core", schema=None, revision="core_197", connection=connection
                )
        assert poisoned.value.orig.pgcode == "P0001"

        engine = _observer_engine(db_url)
        try:
            with engine.connect() as conn:
                assert conn.execute(
                    text("SELECT to_regclass('public.dnd_generation_guard') IS NULL")
                ).scalar_one()
                assert conn.execute(
                    text(
                        "SELECT to_regprocedure('public.context_dnd_mutate("
                        "uuid,text,text,timestamptz,text,real,jsonb)') IS NULL"
                    )
                ).scalar_one()
        finally:
            engine.dispose()
    finally:
        _ROLE_PROBE_URLS.pop(db_url, None)


def test_dnd_final_catalog_has_no_login_owner_force_rls_and_no_public_execute(
    migrated_db_url: str,
) -> None:
    engine = _observer_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            catalog = conn.execute(
                text(
                    """
                    SELECT owner_role.rolcanlogin,
                           owner_role.rolinherit,
                           owner_role.rolsuper,
                           owner_role.rolbypassrls,
                           owner_role.rolcreaterole,
                           owner_role.rolcreatedb,
                           owner_role.rolreplication,
                           context_table.relrowsecurity,
                           context_table.relforcerowsecurity,
                           context_table.relowner = owner_role.oid,
                           guard_table.relowner = owner_role.oid,
                           audit_table.relowner = owner_role.oid,
                           gateway.prosecdef,
                           private_mutation.prosecdef,
                           canonical_json.prosecdef,
                           canonical_json.proowner = owner_role.oid,
                           NOT migration_owner.rolsuper AND NOT migration_owner.rolcreatedb
                               AND NOT migration_owner.rolcreaterole
                               AND NOT migration_owner.rolbypassrls,
                           NOT EXISTS (SELECT FROM pg_namespace
                               WHERE nspname = 'dnd_generation_admin'),
                           gateway.proowner = owner_role.oid,
                           private_mutation.proowner = owner_role.oid,
                           NOT has_schema_privilege(owner_role.oid, 'public', 'CREATE'),
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(gateway.proacl, acldefault('f', gateway.proowner))
                               ) AS acl
                               WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                           ) AS gateway_public_revoked,
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(
                                       private_mutation.proacl,
                                       acldefault('f', private_mutation.proowner)
                                   )
                               ) AS acl
                               WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                           ) AS private_public_revoked,
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(
                                       canonical_json.proacl,
                                       acldefault('f', canonical_json.proowner)
                                   )
                               ) AS acl
                               WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                           ) AS canonical_json_public_revoked,
                           NOT EXISTS (
                               SELECT 1
                               FROM pg_auth_members AS member
                               WHERE member.roleid = owner_role.oid
                                  OR member.member = owner_role.oid
                           ) AS owner_has_no_memberships,
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(
                                       context_table.relacl,
                                       acldefault('r', context_table.relowner)
                                   )
                               ) AS acl
                               WHERE acl.privilege_type = 'DELETE'
                                 AND acl.grantee <> owner_role.oid
                           ) AS no_nonowner_context_delete,
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(gateway.proacl, acldefault('f', gateway.proowner))
                               ) AS acl
                               WHERE acl.grantee = migration_owner.oid
                                 AND acl.privilege_type = 'EXECUTE'
                           ) AS connecting_role_has_no_direct_gateway_grant
                    FROM pg_roles AS owner_role
                    JOIN pg_class AS context_table
                        ON context_table.oid = 'public.user_context'::regclass
                    JOIN pg_class AS guard_table
                        ON guard_table.oid = 'public.dnd_generation_guard'::regclass
                    JOIN pg_class AS audit_table
                        ON audit_table.oid = 'public.dnd_generation_mutations'::regclass
                    JOIN pg_proc AS gateway
                        ON gateway.oid = (
                            'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'
                        )::regprocedure
                    JOIN pg_namespace AS private_schema
                        ON private_schema.nspname = 'dnd_generation_private'
                    JOIN pg_proc AS private_mutation
                        ON private_mutation.pronamespace = private_schema.oid
                       AND private_mutation.proname = 'mutate'
                       AND private_mutation.pronargs = 7
                       AND private_mutation.proargtypes = '2950 25 25 1184 25 700 3802'::oidvector
                    JOIN pg_proc AS canonical_json
                        ON canonical_json.pronamespace = private_schema.oid
                       AND canonical_json.proname = 'canonical_json'
                       AND canonical_json.pronargs = 1
                       AND canonical_json.proargtypes = '3802'::oidvector
                    JOIN pg_database AS database ON database.datname = current_database()
                    JOIN pg_roles AS migration_owner ON migration_owner.oid = database.datdba
                    WHERE owner_role.rolname = 'dnd_generation_owner'
                    """
                )
            ).one()
            policy_count = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_policy
                    WHERE polrelid = 'public.user_context'::regclass
                    """
                )
            ).scalar_one()
            policy_catalog = [
                tuple(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT polname,
                               polcmd,
                               polpermissive,
                               array_to_string(polroles, ',')
                        FROM pg_policy
                        WHERE polrelid = 'public.user_context'::regclass
                        ORDER BY polname
                        """
                    )
                )
            ]
            context_shape = conn.execute(
                text(
                    """
                    SELECT (
                               SELECT array_agg(
                                   format('%s:%s:%s', attname, atttypid, attnotnull)
                                   ORDER BY attnum
                               )
                               FROM pg_attribute
                               WHERE attrelid = 'public.user_context'::regclass
                                 AND attnum > 0
                                 AND NOT attisdropped
                           ) AS columns,
                           (
                               SELECT count(*)
                               FROM pg_constraint
                               WHERE conrelid = 'public.user_context'::regclass
                           ) AS constraint_count,
                           (
                               SELECT count(*)
                               FROM pg_index
                               WHERE indrelid = 'public.user_context'::regclass
                           ) AS index_count,
                           NOT EXISTS (
                               SELECT 1
                               FROM pg_trigger
                               WHERE tgrelid = 'public.user_context'::regclass
                                 AND NOT tgisinternal
                           ) AS no_user_trigger,
                           NOT EXISTS (
                               SELECT 1
                               FROM pg_rewrite
                               WHERE ev_class = 'public.user_context'::regclass
                                 AND rulename <> '_RETURN'
                           ) AS no_user_rule
                    """
                )
            ).one()
            audit_columns = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'dnd_generation_mutations'
                        """
                    )
                )
            }
            # These are current executing successors of the six retired admin-
            # bunker assertions, not acceptance of a missing legacy relation.
            admin_catalog = conn.execute(
                text("""
                SELECT NOT EXISTS (SELECT FROM pg_namespace
                           WHERE nspname='dnd_generation_admin'),
                       NOT EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r
                           ON r.oid=m.roleid WHERE r.rolname='dnd_generation_owner'),
                       NOT EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r
                           ON r.oid=m.member WHERE r.rolname='dnd_generation_owner'),
                       NOT owner.rolsuper AND NOT owner.rolcreatedb AND NOT owner.rolcreaterole
                           AND NOT owner.rolbypassrls AND NOT owner.rolinherit,
                       NOT has_function_privilege(owner.oid, gateway.oid, 'EXECUTE'),
                       NOT EXISTS (SELECT FROM pg_class c,
                         LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                         WHERE c.oid='public.dnd_generation_guard'::regclass
                           AND a.privilege_type<>'SELECT' AND a.grantee<>c.relowner)
                FROM pg_database d JOIN pg_roles owner ON owner.oid=d.datdba
                JOIN pg_proc gateway ON gateway.oid=
                  'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure
                WHERE d.datname=current_database()
            """)
            ).one()
            # The real catalog, exact overload, complete body/config/ACL are
            # compared to the actual source-owned current function definitions.
            definitions = tuple(d for d in FUNCTIONS if d.owner == "dnd_generation_owner")
            assert len(definitions) == 3
            for definition in definitions:
                assert function_matches(conn, definition, inspect_function(conn, definition))

    finally:
        engine.dispose()

    assert catalog == (
        False,  # NOLOGIN
        False,  # NOINHERIT
        False,  # NOSUPERUSER
        False,  # NOBYPASSRLS
        False,  # NOCREATEROLE
        False,  # NOCREATEDB
        False,  # NOREPLICATION
        True,
        True,
        True,
        True,
        True,
        False,  # gateway is explicitly SECURITY INVOKER
        True,
        False,  # canonical JSON helper is explicitly SECURITY INVOKER
        True,
        True,  # ordinary migration owner has no serving/admin bypass flags
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
    )
    assert policy_count == 4
    assert policy_catalog == [
        ("dnd_user_context_delete", "d", True, "0"),
        ("dnd_user_context_insert", "a", True, "0"),
        ("dnd_user_context_select", "r", True, "0"),
        ("dnd_user_context_update", "w", True, "0"),
    ]
    assert tuple(context_shape) == (
        [
            "id:2950:t",
            "signal_type:25:t",
            "value:25:f",
            "set_by_butler:25:t",
            "set_at:1184:t",
            "expires_at:1184:t",
            "confidence:700:t",
            "metadata:3802:f",
            "superseded_at:1184:f",
        ],
        3,
        3,
        True,
        True,
    )
    assert {"value", "metadata"}.isdisjoint(audit_columns)
    assert admin_catalog == (True, True, True, True, True, True)


def test_dnd_gateway_replay_and_real_role_denials(migrated_db_url: str) -> None:
    mutation_id = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    parameters = {
        "mutation_id": mutation_id,
        "expires_at": expires_at,
        "value": "private input must not be persisted in receipt",
        "confidence": 1.0,
        "metadata": '{"proof":"role"}',
    }
    gateway = _dnd_set_statement()

    first = _execute_as_role(
        migrated_db_url, "butler_general_rw", gateway, parameters, fetch_one=True
    )
    replay = _execute_as_role(
        migrated_db_url, "butler_general_rw", gateway, parameters, fetch_one=True
    )
    assert replay == first
    assert first.writer == "general"
    assert first.operation == "set"
    assert first.correlation == f"dnd-action:{mutation_id}"

    changed_replays = (
        {"value": "same mutation ID, changed payload"},
        {"expires_at": expires_at + timedelta(minutes=1)},
        {"confidence": 0.75},
        {"metadata": '{"proof":"changed"}'},
    )
    for changed_fields in changed_replays:
        with pytest.raises(DBAPIError, match="idempotency_conflict"):
            _execute_as_role(
                migrated_db_url,
                "butler_general_rw",
                gateway,
                {**parameters, **changed_fields},
            )

    with pytest.raises(DBAPIError) as normal_denial:
        _execute_as_connecting_user(migrated_db_url, gateway, parameters)
    assert normal_denial.value.orig.pgcode == "42501"

    with pytest.raises(DBAPIError) as normal_denial:
        _execute_as_connecting_user(
            migrated_db_url,
            """
            SELECT * FROM dnd_generation_private.mutate(
                CAST(:mutation_id AS uuid),
                'general',
                'set',
                :expires_at,
                :value,
                CAST(:confidence AS real),
                CAST(:metadata AS jsonb)
            )
            """,
            {**parameters, "mutation_id": str(uuid.uuid4())},
        )
    assert normal_denial.value.orig.pgcode == "42501"

    # A missing retired function is not an authority-denial proof. Exercise
    # the actual current producer using the real normal migration connection.
    normal = create_engine(migrated_db_url, connect_args={"connect_timeout": 5})
    try:
        with normal.connect() as connection:
            with pytest.raises(AuthorityError, match="maintenance/ownership prerequisite"):
                install_revision_group(connection, "dnd")
    finally:
        normal.dispose()

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            """
            INSERT INTO public.user_context (
                signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
            )
            VALUES ('dnd', 'direct write denied', 'health', now(),
                    now() + interval '1 hour', 1.0, NULL)
            """,
        )
    assert role_denial.value.orig.pgcode == "42501"

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            """
            INSERT INTO public.user_context (
                signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
            )
            VALUES ('dnd', 'direct canonical-writer DML denied', 'general', now(),
                    now() + interval '1 hour', 1.0, NULL)
            """,
        )
    assert role_denial.value.orig.pgcode == "42501"

    direct_dnd_update_rowcount = _execute_as_role(
        migrated_db_url,
        "butler_general_rw",
        """
        UPDATE public.user_context
        SET value = 'direct canonical-writer DML denied'
        WHERE signal_type = 'dnd' AND set_by_butler = 'general'
        """,
        return_rowcount=True,
    )
    # PostgreSQL RLS filters an inaccessible UPDATE target rather than raising;
    # zero affected rows is the direct-DML denial proof for this existing DND row.
    assert direct_dnd_update_rowcount == 0
    unchanged_dnd_row = _execute_as_role(
        migrated_db_url,
        "butler_general_rw",
        """
        SELECT value
        FROM public.user_context
        WHERE signal_type = 'dnd' AND set_by_butler = 'general'
        """,
        fetch_one=True,
    )
    assert unchanged_dnd_row.value == parameters["value"]

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            gateway,
            {**parameters, "mutation_id": str(uuid.uuid4())},
        )
    assert role_denial.value.orig.pgcode == "42501"

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            """
            SELECT * FROM public.context_dnd_mutate(
                CAST(:mutation_id AS uuid),
                'switchboard',
                'clear',
                NULL,
                NULL,
                NULL,
                NULL
            )
            """,
            {"mutation_id": str(uuid.uuid4())},
        )
    assert role_denial.value.orig.pgcode == "P0001"

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            "SELECT * FROM public.dnd_generation_mutations",
        )
    assert role_denial.value.orig.pgcode == "42501"

    # FORCE RLS must preserve the ordinary allowed path while rejecting DND.
    _execute_as_role(
        migrated_db_url,
        "butler_health_rw",
        """
        INSERT INTO public.user_context (
            signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
        )
        VALUES ('exercising', 'unit-test', 'health', now(), now() + interval '1 hour', 1.0, NULL)
        """,
    )

    with pytest.raises(DBAPIError) as role_denial:
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            """
            UPDATE public.user_context
            SET signal_type = 'dnd'
            WHERE signal_type = 'exercising' AND set_by_butler = 'health'
            """,
        )
    assert role_denial.value.orig.pgcode == "42501"

    # Effective ACL denial is distinct from RLS filtering: no runtime can
    # TRUNCATE either ordinary context or either protected state table.
    for relation in ("user_context", "dnd_generation_guard", "dnd_generation_mutations"):
        with pytest.raises(DBAPIError) as truncate_denial:
            _execute_as_role(
                migrated_db_url, "butler_general_rw", f"TRUNCATE TABLE public.{relation}"
            )
        assert truncate_denial.value.orig.pgcode == "42501"

    # Snapshot all durable mutation state using a separate committed observer.
    # A real server statement cancellation AFTER the writer returned must roll
    # back both its receipt and generation; no fake write or catalog witness.
    observer = _observer_engine(migrated_db_url)
    cancelled = {**parameters, "mutation_id": str(uuid.uuid4())}
    try:
        with observer.connect() as observation:
            before_generation = observation.execute(
                text("SELECT generation FROM public.dnd_generation_guard WHERE guard_id=1")
            ).scalar_one()
            before_rows = tuple(
                observation.execute(
                    text("SELECT * FROM public.dnd_generation_mutations ORDER BY generation")
                )
            )
        with pytest.raises(DBAPIError) as cancelled_statement:
            with observer.begin() as transaction:
                transaction.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
                transaction.execute(text(gateway), cancelled).one()
                transaction.exec_driver_sql("SET LOCAL statement_timeout = '50ms'")
                transaction.exec_driver_sql("SELECT pg_catalog.pg_sleep(1)")
        assert cancelled_statement.value.orig.pgcode == "57014"
        with observer.connect() as observation:
            assert (
                observation.execute(
                    text("SELECT generation FROM public.dnd_generation_guard WHERE guard_id=1")
                ).scalar_one()
                == before_generation
            )
            assert (
                tuple(
                    observation.execute(
                        text("SELECT * FROM public.dnd_generation_mutations ORDER BY generation")
                    )
                )
                == before_rows
            )
            assert (
                observation.execute(
                    text(
                        "SELECT count(*) FROM public.dnd_generation_mutations WHERE mutation_id=:id"
                    ),
                    {"id": cancelled["mutation_id"]},
                ).scalar_one()
                == 0
            )
        committed = _execute_as_role(
            migrated_db_url, "butler_general_rw", gateway, cancelled, fetch_one=True
        )
        assert committed.generation == before_generation + 1
        with observer.connect() as observation:
            assert (
                observation.execute(
                    text(
                        "SELECT count(*) FROM public.dnd_generation_mutations WHERE mutation_id=:id"
                    ),
                    {"id": cancelled["mutation_id"]},
                ).scalar_one()
                == 1
            )
            assert (
                observation.execute(
                    text("SELECT generation FROM public.dnd_generation_guard WHERE guard_id=1")
                ).scalar_one()
                == committed.generation
            )
    finally:
        observer.dispose()


def test_switchboard_can_mutate_only_its_own_dnd_row(migrated_db_url: str) -> None:
    """The second canonical writer has an actual role-enforced success path."""
    set_parameters = {
        "mutation_id": str(uuid.uuid4()),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "value": "switchboard-owned DND",
        "confidence": 1.0,
        "metadata": '{"proof":"switchboard"}',
    }
    set_receipt = _execute_as_role(
        migrated_db_url,
        "butler_switchboard_rw",
        _dnd_set_statement("switchboard"),
        set_parameters,
        fetch_one=True,
    )
    clear_receipt = _execute_as_role(
        migrated_db_url,
        "butler_switchboard_rw",
        _dnd_clear_statement("switchboard"),
        {
            "mutation_id": str(uuid.uuid4()),
        },
        fetch_one=True,
    )

    assert set_receipt.writer == "switchboard"
    assert set_receipt.operation == "set"
    assert clear_receipt.writer == "switchboard"
    assert clear_receipt.operation == "clear"
    assert clear_receipt.requested_expires_at is None
    assert clear_receipt.effective_expires_at is None


def test_dnd_default_ttl_and_normalized_metadata_replay_are_durable(
    migrated_db_url: str,
) -> None:
    """Database-normalized defaults/canonical metadata survive an exact retry."""
    parameters = {
        "mutation_id": str(uuid.uuid4()),
        "expires_at": None,
        "value": "normalization proof",
        "confidence": 1.0,
        "metadata": '{"accent":"\\u00e9","count":1}',
    }
    gateway = _dnd_set_statement()
    first = _execute_as_role(
        migrated_db_url, "butler_general_rw", gateway, parameters, fetch_one=True
    )
    replay = _execute_as_role(
        migrated_db_url,
        "butler_general_rw",
        gateway,
        {
            **parameters,
            "metadata": '{"count":1.00,"accent":"e\\u0301"}',
        },
        fetch_one=True,
    )

    assert replay == first
    assert first.requested_expires_at is None
    assert first.effective_expires_at is not None

    with pytest.raises(DBAPIError, match="duplicate NFC-normalized keys"):
        _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            gateway,
            {
                **parameters,
                "mutation_id": str(uuid.uuid4()),
                "metadata": '{"\\u00e9":1,"e\\u0301":1}',
            },
        )


def test_dnd_concurrent_identical_retry_advances_generation_once(migrated_db_url: str) -> None:
    """The durable action ID serializes a race into one committed receipt."""
    mutation_id = str(uuid.uuid4())
    parameters = {
        "mutation_id": mutation_id,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "value": "same durable action",
        "confidence": 1.0,
        "metadata": '{"proof":"concurrent-replay"}',
    }
    start = Barrier(2)

    def invoke_same_action():
        start.wait(timeout=10)
        return _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            _dnd_set_statement(),
            parameters,
            fetch_one=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke_same_action) for _ in range(2)]
        first, second = [future.result(timeout=30) for future in futures]

    assert first == second
    engine = _observer_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            generation = conn.execute(
                text("SELECT generation FROM public.dnd_generation_guard WHERE guard_id = 1")
            ).scalar_one()
    finally:
        engine.dispose()
    assert generation == first.generation


_MUTATE_SIGNATURE = "dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)"


def _legacy_digest_definition(definition: str) -> str:
    """Rewrite the current mutate body into the pre-bu-mzm3su.1 pgcrypto form.

    Every ``sha256(x)`` becomes ``digest(x, 'sha256')``, exactly the shape an
    installed database still carries until the init-db rerun re-adopts it.
    """
    out: list[str] = []
    cursor = 0
    while (start := definition.find("sha256(", cursor)) != -1:
        depth = 0
        for end in range(start + len("sha256"), len(definition)):
            if definition[end] == "(":
                depth += 1
            elif definition[end] == ")":
                depth -= 1
                if depth == 0:
                    break
        inner = definition[start + len("sha256(") : end]
        out.append(definition[cursor:start])
        out.append(f"digest({inner}, 'sha256')")
        cursor = end + 1
    out.append(definition[cursor:])
    return "".join(out)


def _rerun_actual_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Execute the checked-in bootstrap source again, as production does."""
    migration_role = unquote(urlparse(migration_url).username or "")
    assert migration_role
    with migration_session(bootstrap_url, source_database="butlers") as connection:
        with ordinary_migration_identity(connection):
            install_revision_group(connection, "dnd")
        connection.commit()


def test_dnd_mutate_rerun_readopts_pinned_body_with_identical_hashes(
    postgres_container,
) -> None:
    """bu-mzm3su.1: mutate ignores public decoys and keeps its replay identity.

    The installed database is regressed to the legacy pgcrypto ``digest()``
    body with ``public`` on its path, which is what every database bootstrapped
    before this change carries.  A receipt written by that body must replay
    cleanly after the real init-db rerun re-adopts the pinned ``sha256()``
    body, proving the semantic fingerprint is byte-identical.  A
    ``convert_to(text, text)`` decoy planted by the migration login beats the
    catalog ``(text, name)`` form for the unknown ``'UTF8'`` literal, so it
    runs as the private owner until the path is pinned.
    """
    db_name = migration_db_name()
    db_url = create_migrated_test_db(postgres_container, db_name, chains=["core"])
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _ROLE_PROBE_URLS[db_url] = bootstrap_url
    try:
        # The fresh actual current chain/finalizer above precedes this
        # controlled compatibility specimen. It is not accepted by serving
        # admission and no runtime receives maintenance authority.

        def mutate_state(conn) -> tuple[list[str], str]:
            row = conn.execute(
                text(
                    "SELECT proconfig, prosrc FROM pg_proc WHERE oid = CAST(:sig AS regprocedure)"
                ),
                {"sig": _MUTATE_SIGNATURE},
            ).one()
            return list(row.proconfig or []), row.prosrc

        admin = create_engine(
            bootstrap_url,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
        try:
            with admin.connect() as conn:
                proconfig, prosrc = mutate_state(conn)
                assert proconfig == ["search_path=pg_catalog, pg_temp"]
                assert "sha256(" in prosrc and "digest(" not in prosrc
                definition = conn.execute(
                    text("SELECT pg_get_functiondef(CAST(:sig AS regprocedure))"),
                    {"sig": _MUTATE_SIGNATURE},
                ).scalar_one()
                # Raw DBAPI: the body's to_char format would parse as ':MI:SS' binds.
                with conn.connection.cursor() as cursor:
                    cursor.execute(_legacy_digest_definition(definition))
                conn.execute(
                    text(
                        f"ALTER FUNCTION {_MUTATE_SIGNATURE} "
                        "SET search_path = pg_catalog, public, pg_temp"
                    )
                )
                proconfig, prosrc = mutate_state(conn)
                assert proconfig == ["search_path=pg_catalog, public, pg_temp"]
                assert "sha256(" not in prosrc and "digest(" in prosrc
        finally:
            admin.dispose()

        legacy_parameters = {
            "mutation_id": str(uuid.uuid4()),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "value": "Caf\u00e9 written by the legacy digest body",
            "confidence": 0.5,
            "metadata": '{"proof":"hash-parity","nested":{"b":2,"a":1}}',
        }
        legacy_receipt = _execute_as_role(
            db_url, "butler_general_rw", _dnd_set_statement(), legacy_parameters, fetch_one=True
        )

        migration = create_engine(
            db_url,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
        try:
            with migration.connect() as conn:
                conn.execute(
                    text(
                        """
                        CREATE FUNCTION public.convert_to(text, text)
                        RETURNS bytea
                        LANGUAGE plpgsql
                        AS $$
                        BEGIN
                            RAISE EXCEPTION 'hijacked as %', current_user;
                        END;
                        $$
                        """
                    )
                )
        finally:
            migration.dispose()

        fresh_parameters = {**legacy_parameters, "mutation_id": str(uuid.uuid4())}
        with pytest.raises(DBAPIError, match="hijacked as dnd_generation_owner"):
            _execute_as_role(db_url, "butler_general_rw", _dnd_set_statement(), fresh_parameters)

        _rerun_actual_init_db(bootstrap_url, db_url)
        _rerun_actual_init_db(bootstrap_url, db_url)

        admin = create_engine(
            bootstrap_url,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
        try:
            with admin.connect() as conn:
                proconfig, prosrc = mutate_state(conn)
        finally:
            admin.dispose()
        assert proconfig == ["search_path=pg_catalog, pg_temp"]
        assert "sha256(" in prosrc and "digest(" not in prosrc

        # Same mutation ID, same payload: any fingerprint drift between the two
        # bodies would raise idempotency_conflict instead of replaying the receipt.
        replay = _execute_as_role(
            db_url, "butler_general_rw", _dnd_set_statement(), legacy_parameters, fetch_one=True
        )
        assert replay == legacy_receipt
        fresh = _execute_as_role(
            db_url, "butler_general_rw", _dnd_set_statement(), fresh_parameters, fetch_one=True
        )
        assert fresh.generation == legacy_receipt.generation + 1
    finally:
        _ROLE_PROBE_URLS.pop(db_url, None)
