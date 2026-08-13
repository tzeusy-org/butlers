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

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """A disposable core-migrated database with the privileged bootstrap staged."""
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


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
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
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

    The fixture's migration user deliberately inherits runtime-role ACLs, so
    this is stronger than an ACL-only assertion: the private definer must still
    reject because no active canonical ``SET ROLE`` is present.
    """
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
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
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_122")
    asyncio.run(run_migrations(db_url, chain="relationship", schema="relationship"))
    command.upgrade(config, "core_196")


def test_dnd_bootstrap_rejects_any_preexisting_user_context_policy(postgres_container) -> None:
    """A permissive policy must never be adopted into the forced-RLS handoff."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    _run_core_chain_through_dnd_predecessor(db_url)

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
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

    config = _build_alembic_config(db_url, chains=["core"])
    with pytest.raises(Exception, match="authority interface must be absent"):
        command.upgrade(config, "core_197")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('public.dnd_generation_guard') IS NULL")
            ).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regprocedure('public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)') IS NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def test_dnd_final_catalog_has_no_login_owner_force_rls_and_no_public_execute(
    migrated_db_url: str,
) -> None:
    engine = create_engine(migrated_db_url)
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
                           bootstrap_owner.rolsuper,
                           installer.proowner = admin_schema.nspowner,
                           finalizer.proowner = admin_schema.nspowner,
                           installer.prosecdef,
                           finalizer.prosecdef,
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
                               WHERE acl.grantee = current_user::regrole
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
                    JOIN pg_namespace AS admin_schema
                        ON admin_schema.nspname = 'dnd_generation_admin'
                    JOIN pg_roles AS bootstrap_owner
                        ON bootstrap_owner.oid = admin_schema.nspowner
                    JOIN pg_proc AS installer
                        ON installer.pronamespace = admin_schema.oid
                       AND installer.proname = 'install_interface'
                       AND installer.pronargs = 0
                       AND installer.prorettype = 'void'::regtype
                    JOIN pg_proc AS finalizer
                        ON finalizer.pronamespace = admin_schema.oid
                       AND finalizer.proname = 'finalize_interface'
                       AND finalizer.pronargs = 0
                       AND finalizer.prorettype = 'void'::regtype
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
            admin_catalog = conn.execute(
                text(
                    """
                    SELECT admin_schema.nspowner = bootstrap_owner.oid,
                           bootstrap_configuration.relowner = bootstrap_owner.oid,
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(
                                       admin_schema.nspacl,
                                       acldefault('n', admin_schema.nspowner)
                                   )
                               ) AS acl
                               WHERE acl.grantee <> bootstrap_owner.oid
                           ),
                           NOT EXISTS (
                               SELECT 1
                               FROM aclexplode(
                                   COALESCE(
                                       bootstrap_configuration.relacl,
                                       acldefault(
                                           'r',
                                           bootstrap_configuration.relowner
                                       )
                                   )
                               ) AS acl
                               WHERE acl.grantee <> bootstrap_owner.oid
                           ),
                           NOT EXISTS (
                               SELECT 1
                               FROM pg_proc AS admin_function,
                                    LATERAL aclexplode(
                                        COALESCE(
                                            admin_function.proacl,
                                            acldefault('f', admin_function.proowner)
                                        )
                                    ) AS acl
                               WHERE admin_function.pronamespace = admin_schema.oid
                                 AND admin_function.proname IN ('install_interface', 'finalize_interface')
                                 AND admin_function.pronargs = 0
                                 AND admin_function.prorettype = 'void'::regtype
                                 AND acl.privilege_type = 'EXECUTE'
                                 AND acl.grantee <> bootstrap_owner.oid
                           ),
                           NOT EXISTS (
                               SELECT 1
                               FROM pg_class AS guard_table,
                                    LATERAL aclexplode(
                                        COALESCE(
                                            guard_table.relacl,
                                            acldefault('r', guard_table.relowner)
                                        )
                                    ) AS acl
                               WHERE guard_table.oid = 'public.dnd_generation_guard'::regclass
                                 AND acl.privilege_type <> 'SELECT'
                                 AND acl.grantee <> owner_role.oid
                           )
                    FROM pg_roles AS owner_role
                    JOIN pg_namespace AS admin_schema
                        ON admin_schema.nspname = 'dnd_generation_admin'
                    JOIN pg_roles AS bootstrap_owner
                        ON bootstrap_owner.oid = admin_schema.nspowner
                    JOIN pg_class AS bootstrap_configuration
                        ON bootstrap_configuration.relnamespace = admin_schema.oid
                       AND bootstrap_configuration.relname = 'bootstrap_configuration'
                       AND bootstrap_configuration.relkind = 'r'
                    WHERE owner_role.rolname = 'dnd_generation_owner'
                    """
                )
            ).one()
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
        True,  # trusted bootstrap owner is a superuser
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

    with pytest.raises(DBAPIError, match="active canonical runtime role"):
        _execute_as_connecting_user(migrated_db_url, gateway, parameters)

    with pytest.raises(DBAPIError, match="active canonical runtime role"):
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

    with pytest.raises(DBAPIError):
        _execute_as_connecting_user(
            migrated_db_url,
            "SELECT dnd_generation_admin.finalize_interface()",
        )

    with pytest.raises(DBAPIError):
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            """
            INSERT INTO public.user_context (
                signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
            )
            VALUES ('dnd', 'direct write denied', 'health', now(), now() + interval '1 hour', 1.0, NULL)
            """,
        )

    with pytest.raises(DBAPIError):
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

    with pytest.raises(DBAPIError):
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            gateway,
            {**parameters, "mutation_id": str(uuid.uuid4())},
        )

    with pytest.raises(DBAPIError):
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

    with pytest.raises(DBAPIError):
        _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            "SELECT * FROM public.dnd_generation_mutations",
        )

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

    with pytest.raises(DBAPIError):
        _execute_as_role(
            migrated_db_url,
            "butler_health_rw",
            """
            UPDATE public.user_context
            SET signal_type = 'dnd'
            WHERE signal_type = 'exercising' AND set_by_butler = 'health'
            """,
        )


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
        start.wait()
        return _execute_as_role(
            migrated_db_url,
            "butler_general_rw",
            _dnd_set_statement(),
            parameters,
            fetch_one=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke_same_action) for _ in range(2)]
        first, second = [future.result() for future in futures]

    assert first == second
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            generation = conn.execute(
                text("SELECT generation FROM public.dnd_generation_guard WHERE guard_id = 1")
            ).scalar_one()
    finally:
        engine.dispose()
    assert generation == first.generation
