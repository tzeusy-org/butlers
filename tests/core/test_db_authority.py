"""Executing serving admission controls; driver boundaries only are doubled."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from butlers.db import Database
from butlers.db_authority import roles
from butlers.db_authority.roles import AuthorityError, load_runtime_profile

pytestmark = pytest.mark.unit


@contextmanager
def _recording_postgresql_connection(respond):
    """Real SQLAlchemy dispatch; mapping decoding is not a native PostgreSQL proof."""
    import psycopg2
    from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
    from sqlalchemy.engine import URL, Engine
    from sqlalchemy.pool import StaticPool

    calls = []

    class Cursor:
        description = None
        rowcount = -1

        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=None):
            parameters = {} if parameters is None else parameters
            # Model the mapping-format boundary after the actual PG dialect has
            # supplied its parameters. Do not interpret SQL or emulate PG roles.
            decoded = statement % parameters
            calls.append((statement, dict(parameters), decoded))
            columns, self.rows = respond(statement, parameters, decoded)
            self.rows = list(self.rows)
            self.description = (
                [(column, None, None, None, None, None, None) for column in columns]
                if columns is not None
                else None
            )

        def fetchone(self):
            return self.rows.pop(0) if self.rows else None

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            pass

    class Transport:
        notices = []

        def cursor(self):
            return Cursor(self)

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    engine = Engine(
        StaticPool(creator=Transport),
        PGDialect_psycopg2(dbapi=psycopg2),
        URL.create("postgresql+psycopg2"),
    )
    try:
        with engine.connect() as connection:
            yield connection, calls
    finally:
        engine.dispose()


def test_actual_dashboard_staging_roundtrips_literal_sql_and_restores_identity():
    from butlers.db_authority import interfaces

    state = {"actor": "previous_actor", "profile": True, "maintenance": True, "fail": None}
    staged = []

    def respond(statement, parameters, decoded):
        if "FROM pg_catalog.pg_database d" in statement:
            return ("database", "owner", "valid"), [
                ("butlers", "normal_migrator", state["profile"])
            ]
        if "current_user=session_user AND rolsuper" in statement:
            return ("valid",), [(state["maintenance"],)]
        if statement.startswith("SELECT current_setting"):
            return ("actor",), [(state["actor"],)]
        if "pg_catalog.set_config" in statement:
            state["actor"] = parameters["owner"]
            return ("actor",), [(state["actor"],)]
        assert decoded == interfaces.DASHBOARD_PURPOSE_SQL
        assert dict(parameters) == {}
        assert state["actor"] == "normal_migrator"
        if state["fail"] is not None:
            raise state["fail"]
        staged.append(decoded)
        return None, []

    with _recording_postgresql_connection(respond) as (connection, calls):
        try:
            interfaces.stage_dashboard_interfaces(connection)
        finally:
            assert state["actor"] == "previous_actor"
        assert staged == [interfaces.DASHBOARD_PURPOSE_SQL]
        assert any(template != decoded for template, _, decoded in calls)
        for guard in ("profile", "maintenance"):
            state[guard] = False
            with pytest.raises(AuthorityError):
                interfaces.stage_dashboard_interfaces(connection)
            state[guard] = True
            assert staged == [interfaces.DASHBOARD_PURPOSE_SQL]
        for primary in (LookupError("recording transport failure"), asyncio.CancelledError()):
            state["fail"] = primary
            with pytest.raises(type(primary)) as error:
                interfaces.stage_dashboard_interfaces(connection)
            assert error.value is primary
            if isinstance(primary, asyncio.CancelledError):
                # SQLAlchemy invalidates a DBAPI connection on BaseException;
                # the real finally attempts restoration and preserves cancel.
                assert connection.invalidated
                assert primary.__notes__ == [
                    "Dashboard maintenance identity restoration was incomplete"
                ]
            else:
                assert state["actor"] == "previous_actor"


def test_actual_protected_installer_roundtrips_all_percent_bodies_and_catalog_checks():
    from butlers.db_authority import interfaces
    from butlers.db_authority.apply import quote_identifier

    names = ("mutate", "reissue_runtime_attention_episode", "append_runtime_attention_condition")
    definitions = tuple(
        next(row for row in interfaces.FUNCTIONS if row.name == name) for name in names
    )
    index = {interfaces._signature(row): row for row in definitions}
    created = {}
    owners = {}
    acls = {}
    installed = []
    valid = {"owners": True, "maintenance": True}

    def respond(statement, parameters, decoded):
        if "WITH owners AS" in statement:
            assert set(parameters["owners"]) == {row.owner for row in definitions}
            assert parameters["count"] == len(set(parameters["owners"]))
            return ("valid",), [(valid["owners"],)]
        if "pg_get_function_identity_arguments" in statement:
            key = parameters["signature"]
            row = index[key]
            return ("oid", "owner", "definer", "config", "body"), (
                [
                    (
                        key,
                        owners[key],
                        row.security_definer,
                        ["search_path=pg_catalog, pg_temp"],
                        row.body,
                    )
                ]
                if key in created
                else []
            )
        if "pg_catalog.aclexplode" in statement:
            return ("role", "privilege", "grantable"), [
                (role, "EXECUTE", False) for role in sorted(acls[parameters["oid"]])
            ]
        if "session_user=current_user AND rolsuper" in statement:
            return ("valid",), [(valid["maintenance"],)]
        for key, row in index.items():
            if decoded == row.sql:
                assert "%" in row.sql
                assert dict(parameters) == {}
                created[key] = row
                owners[key] = "maintenance"
                acls[key] = {"PUBLIC"}
                installed.append(decoded)
                return None, []
            if decoded == f"ALTER FUNCTION {key} OWNER TO {quote_identifier(row.owner)}":
                owners[key] = row.owner
                return None, []
            if decoded == f"REVOKE ALL ON FUNCTION {key} FROM PUBLIC":
                acls[key].remove("PUBLIC")
                return None, []
            for role in row.executors:
                if decoded == f"GRANT EXECUTE ON FUNCTION {key} TO {quote_identifier(role)}":
                    acls[key].add(role)
                    return None, []
        raise AssertionError("unexpected protected installer dispatch")

    with _recording_postgresql_connection(respond) as (connection, calls):
        for guard in ("owners", "maintenance"):
            valid[guard] = False
            with pytest.raises(AuthorityError):
                interfaces.install_functions(connection, definitions)
            valid[guard] = True
            assert installed == []
        interfaces.install_functions(connection, definitions)
        assert installed == [row.sql for row in definitions]
        assert all(owners[key] == row.owner for key, row in index.items())
        assert all(acls[key] == set(row.executors) for key, row in index.items())
        assert all(
            interfaces.function_matches(
                connection, row, interfaces.inspect_function(connection, row)
            )
            for row in definitions
        )
        interfaces.install_functions(connection, definitions)
        assert installed == [row.sql for row in definitions]
        assert len([template for template, _, decoded in calls if decoded in installed]) == 3


def test_actual_trigger_shape_query_binds_only_relation_and_preserves_location_normalization():
    from butlers.db_authority import interfaces

    expected = """
SELECT tgname, tgtype, tgenabled, tgfoid, tgargs, tgattr::smallint[] AS attributes,
 tgdeferrable, tginitdeferred, tgconstraint<>0 AS constraint_trigger,
 pg_catalog.regexp_replace(tgqual::text, ':location -?[0-9]+', ':location -1', 'g') AS expression
FROM pg_catalog.pg_trigger
WHERE tgrelid=pg_catalog.to_regclass(:relation) AND NOT tgisinternal
ORDER BY tgname
"""
    relation = "public.protocol_subject"

    def respond(statement, parameters, decoded):
        assert dict(parameters) == {"relation": relation}
        assert statement.replace("%(relation)s", ":relation") == expected
        return ("tgname", "expression"), [("fixed_trigger", "{EXPR :location -1}")]

    with _recording_postgresql_connection(respond) as (connection, calls):
        assert interfaces._shape_rows(connection, interfaces._SHAPE_TRIGGERS, relation) == (
            {"tgname": "fixed_trigger", "expression": "{EXPR :location -1}"},
        )
        assert len(calls) == 1


async def test_connect_rejects_hidden_connecting_login_owner_before_lazy_pool():
    connection = AsyncMock()
    connection.fetchval.return_value = True
    connection.fetchrow.return_value = {
        "identity_ok": True,
        "role_set_ok": False,
        "attributes_ok": True,
        "ownership_ok": False,
        "create_ok": True,
        "target_member": True,
    }
    connection.terminate = Mock()
    database = Database("butlers", schema="general", role="butler_general_rw", min_pool_size=0)
    with (
        patch("butlers.db.asyncpg.connect", AsyncMock(return_value=connection)),
        patch("butlers.db.asyncpg.create_pool", AsyncMock(return_value=Mock())) as create,
    ):
        with pytest.raises(RuntimeError):
            await database.connect()
        create.assert_not_called()
    assert database.pool is None
    connection.terminate.assert_called_once()


def _facts():
    return dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )


class _DriverConnection:
    """Driver protocol double, not proof that catalog SQL executed in PostgreSQL."""

    def __init__(self):
        self.facts = _facts()
        self.private_usage = True
        self.hidden_owner = False
        self.calls = []
        self.close = AsyncMock()
        self.terminate = Mock()
        self.set_type_codec = AsyncMock()

    async def fetchval(self, query, *args):
        return self.private_usage if "has_schema_privilege" in query else True

    async def execute(self, query, *args):
        self.calls.append(query)
        return "SET"

    async def fetchrow(self, query, *args):
        self.calls.append(query)
        result = self.facts.copy()
        if self.hidden_owner and "rolname = session_user" in query.split("UNION", 1)[0]:
            result["ownership_ok"] = False
        return result


class _DriverPool:
    def __init__(self, connection, setup):
        self.connection, self.setup = connection, setup
        self.close = AsyncMock()

    @asynccontextmanager
    async def acquire(self):
        await self.setup(self.connection)
        yield self.connection


@pytest.fixture
def driver(monkeypatch):
    connection = _DriverConnection()
    pools = []

    async def create(**kwargs):
        pool = _DriverPool(connection, kwargs.get("setup", AsyncMock()))
        pools.append(pool)
        return pool

    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr("butlers.db.asyncpg.connect", connect)
    monkeypatch.setattr("butlers.db.asyncpg.create_pool", AsyncMock(side_effect=create))
    return connection, pools, connect


def _runtime(schema="general"):
    return Database("butlers", schema=schema, role=f"butler_{schema}_rw", min_pool_size=0)


async def test_connect_and_each_handoff_observe_fresh_authority(driver):
    connection, pools, _ = driver
    database = _runtime()
    pool = await database.connect()
    assert connection.close.await_count == 1  # probe is released before pool creation
    assert pools == [pool]
    async with pool.acquire() as acquired:
        assert acquired is connection
    for fact in ("role_set_ok", "ownership_ok", "create_ok", "attributes_ok", "target_member"):
        connection.facts[fact] = False
        with pytest.raises(RuntimeError):
            async with pool.acquire():
                pytest.fail("unsafe handoff")
        connection.facts[fact] = True
    assert len([q for q in connection.calls if "WITH RECURSIVE" in q]) == 8
    # Probe + first real pool handoff + caller acquisition + five fresh denials.
    await database.close()
    pool.close.assert_awaited_once()


async def test_session_login_graph_not_post_set_role_graph(driver):
    connection, _, _ = driver
    connection.hidden_owner = True
    database = _runtime()
    with pytest.raises(RuntimeError):
        await database.connect()
    assert database.pool is None
    connection.terminate.assert_called_once()


@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_nonexact_catalog_fact_refuses(driver, value):
    connection, _, _ = driver
    connection.facts["ownership_ok"] = value
    with pytest.raises(RuntimeError):
        await _runtime().connect()
    connection.terminate.assert_called_once()


async def test_unreadable_and_cancelled_catalog_preserve_cause_and_cleanup(driver):
    connection, _, _ = driver
    cause = ValueError("synthetic-private-driver-detail")
    connection.fetchrow = AsyncMock(side_effect=cause)
    with pytest.raises(RuntimeError) as error:
        await _runtime().connect()
    assert "synthetic-private-driver-detail" not in str(error.value)
    assert error.value.__cause__.__cause__ is cause
    connection.terminate.assert_called_once()
    connection.terminate.reset_mock()
    cancellation = asyncio.CancelledError()
    connection.fetchrow.side_effect = cancellation
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await _runtime().connect()
    assert cancelled.value is cancellation
    connection.terminate.assert_called_once()


async def test_private_child_holds_primary_profile_and_lifetime(driver, monkeypatch):
    connection, _, _ = driver
    primary = _runtime("chronicler")
    await primary.connect()
    child = primary.for_owned_schema("chronicler_mem")
    assert child._runtime_profile is primary._runtime_profile
    monkeypatch.setattr(
        "butlers.db.load_runtime_profile", Mock(side_effect=AssertionError("reload"))
    )
    pool = await child.connect()  # actual held primary profile; no re-derived private role
    async with pool.acquire():
        pass
    connection.private_usage = False
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("missing private USAGE")
    connection.private_usage = True
    original_pool = primary.pool
    primary.pool = _DriverPool(connection, AsyncMock())
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("replaced primary pool")
    primary.pool = original_pool
    primary._runtime_profile = None
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("replaced primary profile")
    await primary.close()
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("closed primary")
    await child.close()


async def test_administrative_path_has_no_roster_or_role_io(driver, monkeypatch):
    _, pools, connect = driver
    monkeypatch.setattr(
        "butlers.db.load_runtime_profile", Mock(side_effect=AssertionError("roster"))
    )
    database = Database("administrative")
    assert await database.connect() is pools[0]
    connect.assert_not_awaited()
    await database.close()


def _write_roster(root, name="general", *, schema="general", database="butlers", private=None):
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    data = f'[butler]\nname="{name}"\n[butler.db]\nname="{database}"\nschema="{schema}"\n'
    if private is not None:
        data += f'[modules.memory]\nmemory_schema="{private}"\n'
    (child / "butler.toml").write_text(data)


def test_fixed_complete_source_profile_and_immutability():
    profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    assert profile.role in profile.runtime_roles
    assert len(set(profile.runtime_roles)) == len(profile.runtime_roles) > 0
    assert "connector_writer" not in profile.runtime_roles
    with pytest.raises(FrozenInstanceError):
        profile.role = "other"
    private = load_runtime_profile("butlers", "chronicler", "butler_chronicler_rw")
    assert private.private_schemas == ("chronicler_mem",)


@pytest.mark.parametrize(
    "fault",
    ["missing", "empty", "missing_entry", "malformed", "invalid", "duplicate", "private_alias"],
)
def test_roster_errors_are_not_discovery_skips(tmp_path, monkeypatch, fault):
    root = tmp_path / "roster"
    if fault != "missing":
        root.mkdir()
    if fault not in ("missing", "empty"):
        _write_roster(root)
        if fault == "missing_entry":
            (root / "health").mkdir()
        elif fault == "malformed":
            (root / "general/butler.toml").write_text("[broken")
        elif fault == "invalid":
            (root / "general/butler.toml").write_text('[butler]\nname="general"')
        elif fault == "duplicate":
            _write_roster(root, "health")
        elif fault == "private_alias":
            _write_roster(root, "health", schema="health", private="general")
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    with pytest.raises(AuthorityError):
        load_runtime_profile("butlers", "general", "butler_general_rw")


@pytest.mark.parametrize(
    "database,schema,role",
    [
        ("other", "general", "butler_general_rw"),
        ("butlers", "health", "butler_general_rw"),
        ("butlers", "general", "butler_unlisted_rw"),
    ],
)
async def test_unlisted_identity_refuses_before_driver_io(driver, database, schema, role):
    _, _, connect = driver
    with pytest.raises(RuntimeError):
        await Database(database, schema=schema, role=role).connect()
    connect.assert_not_awaited()


def test_interpreter_cache_is_not_a_roster_identity(tmp_path, monkeypatch):
    _write_roster(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    monkeypatch.setattr(roles, "_ROSTER_ROOT", tmp_path)
    profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    assert profile.role == "butler_general_rw"
    (tmp_path / "undeclared").mkdir()
    with pytest.raises(AuthorityError):
        load_runtime_profile("butlers", "general", "butler_general_rw")


@pytest.mark.parametrize(
    "purpose,role",
    [
        ("dashboard", "dashboard_api"),
        ("connector", "connector_writer"),
        ("credential", "credential_store_writer"),
        ("restore_executor", "restore_drill_executor"),
    ],
)
def test_service_purpose_has_only_its_fixed_capability(purpose, role):
    profile = roles.purpose_profile("butlers", purpose)
    assert profile.role == role
    assert profile.runtime_roles == (role,)
    with pytest.raises(FrozenInstanceError):
        profile.role = "butler_general_rw"
    with pytest.raises(AuthorityError):
        roles.purpose_profile("butlers", "butler_general_rw")


async def test_purpose_acquire_does_not_reuse_a_daemon_identity():
    from butlers.db_authority.catalog import setup_purpose

    connection = _DriverConnection()
    profile = roles.purpose_profile("butlers", "dashboard")
    await setup_purpose(connection, profile)
    assert connection.calls[0] == 'SET ROLE "dashboard_api"'
    assert "WITH RECURSIVE" in connection.calls[1]
    connection.facts["role_set_ok"] = False
    with pytest.raises(AuthorityError):
        await setup_purpose(connection, profile)


async def test_actual_connector_hook_refuses_hidden_connecting_owner():
    from butlers.connectors.db_role import connector_setup_role

    connection = _DriverConnection()
    driver_read = connection.fetchval

    async def selected_database_or_catalog(query, *args):
        if query == "SELECT current_database()":
            return "butlers"
        return await driver_read(query, *args)

    connection.fetchval = selected_database_or_catalog
    connection.facts["ownership_ok"] = False
    with pytest.raises(AuthorityError):
        await connector_setup_role(connection)
    connection.facts["ownership_ok"] = True
    await connector_setup_role(connection)
    assert connection.calls[-2] == 'SET ROLE "connector_writer"'


async def test_actual_dashboard_pool_proves_lazy_connection_before_publication(driver):
    from butlers.api.db import DatabaseManager

    connection, pools, _ = driver
    connection.facts["ownership_ok"] = False
    manager = DatabaseManager(min_pool_size=0, max_pool_size=1)
    with pytest.raises(AuthorityError):
        await manager._create_pool(database="butlers", log_name="synthetic", schema="general")
    assert len(pools) == 1
    pools[0].close.assert_awaited_once()
    assert manager._pools == {}


async def test_purpose_pool_rechecks_each_acquisition_and_preserves_cancellation(driver):
    from butlers.db_authority.catalog import create_purpose_pool

    connection, pools, _ = driver
    profile = roles.purpose_profile("butlers", "credential")
    pool = await create_purpose_pool(profile, database="butlers", min_size=0)
    async with pool.acquire() as acquired:
        assert acquired is connection
    connection.facts["attributes_ok"] = False
    with pytest.raises(AuthorityError):
        async with pool.acquire():
            pytest.fail("Unproved credential handoff escaped")
    connection.facts["attributes_ok"] = True
    cancel = asyncio.CancelledError()
    connection.fetchrow = AsyncMock(side_effect=cancel)
    with pytest.raises(asyncio.CancelledError) as caught:
        await create_purpose_pool(profile, database="butlers", min_size=0)
    assert caught.value is cancel
    pools[-1].close.assert_awaited_once()


async def test_actual_credential_factory_refuses_an_unproved_writer(driver, monkeypatch):
    from butlers.credential_store import create_system_global_credential_store_from_env

    connection, pools, _ = driver
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL", "postgresql://credential:synthetic@localhost/butlers"
    )
    connection.facts["ownership_ok"] = False
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    pools[0].close.assert_awaited_once()


@pytest.mark.parametrize("cancel", [False, True])
async def test_unpublished_native_pool_is_terminated_after_failed_first_handoff(
    monkeypatch, cancel
):
    import asyncpg

    from butlers.db_authority.catalog import create_purpose_pool

    failure = asyncio.CancelledError() if cancel else AuthorityError("Synthetic admission refusal")
    calls = []

    async def refuse_connection(*args, **kwargs):
        calls.append("connect")
        raise failure

    native_create = asyncpg.create_pool
    pool = native_create(min_size=0, max_size=1, connect=refuse_connection)
    monkeypatch.setattr(asyncpg, "create_pool", lambda **options: pool)
    with pytest.raises(type(failure)) as caught:
        await create_purpose_pool(roles.purpose_profile("butlers", "dashboard"), min_size=0)
    assert caught.value is failure
    assert type(pool) is asyncpg.Pool
    assert calls == ["connect"]
    assert pool._closed is True
    assert all(holder._con is None for holder in pool._holders)


async def test_native_pool_failed_eager_initialization_owns_late_sibling_connection():
    import asyncpg

    from butlers.db_authority.catalog import create_purpose_pool

    third_entered = asyncio.Event()
    settle = asyncio.Event()
    connections = []
    count = 0
    failure = AuthorityError("Synthetic initialization failure")

    class DriverConnection(asyncpg.Connection):
        """Native class protocol boundary only; no initialized wire connection."""

        def __init__(self):
            self.close = AsyncMock()
            self.terminate = Mock()

        def __del__(self):
            pass

    async def connect(*args, **kwargs):
        nonlocal count
        count += 1
        ordinal = count
        if ordinal == 2:
            await third_entered.wait()
            raise failure
        if ordinal == 3:
            third_entered.set()
            while not settle.is_set():
                try:
                    await settle.wait()
                except asyncio.CancelledError:
                    # Faithfully exercise a driver boundary that settles late.
                    pass
        connection = DriverConnection()
        connections.append(connection)
        return connection

    try:
        with pytest.raises(AuthorityError) as caught:
            await asyncio.wait_for(
                create_purpose_pool(
                    roles.purpose_profile("butlers", "dashboard"),
                    min_size=3,
                    max_size=3,
                    max_inactive_connection_lifetime=0,
                    connect=connect,
                ),
                timeout=1,
            )
        assert caught.value is failure
    finally:
        settle.set()
    for _ in range(8):
        await asyncio.sleep(0)
    assert len(connections) == 2
    for connection in connections:
        connection.terminate.assert_called()


async def test_actual_database_does_not_publish_failed_lazy_native_pool(monkeypatch):
    import asyncpg

    failure = AuthorityError("Synthetic native initialization refusal")

    async def refuse_connection(*args, **kwargs):
        raise failure

    pool = asyncpg.create_pool(min_size=0, max_size=1, connect=refuse_connection)
    monkeypatch.setattr(asyncpg, "create_pool", lambda **options: pool)
    database = Database("synthetic", min_pool_size=0, max_pool_size=1)
    try:
        with pytest.raises(AuthorityError) as caught:
            await database.connect()
        assert caught.value is failure
        assert database.pool is None
        assert pool._closed is True
    finally:
        pool.terminate()


async def test_actual_dashboard_authority_never_certifies_a_connected_subset(driver):
    from butlers.api.db import DatabaseManager

    connection, _, _ = driver
    manager = DatabaseManager(min_pool_size=0, max_pool_size=1)
    assert await manager.authority_status() == "unavailable"
    await manager.add_butler("general", db_name="butlers", db_schema="general", modules=frozenset())
    await manager.set_credential_shared_pool("butlers", "public")
    assert await manager.authority_status() == "admitted"
    manager.register_configured_modules("health", frozenset())
    assert await manager.authority_status() == "unavailable"
    await manager.add_butler("health", db_name="butlers", db_schema="health", modules=frozenset())
    connection.facts["ownership_ok"] = False
    assert await manager.authority_status() == "unavailable"
    connection.facts["ownership_ok"] = True
    assert await manager.authority_status() == "admitted"
    await manager.close()


def test_privileged_disposable_compiler_does_not_relax_serving_identity():
    compiled = roles.compile_deployment_profiles("test_disposable", source_database="butlers")
    source = roles.runtime_profiles("butlers")
    assert len(compiled) == len(source) == 13
    for actual, original in zip(compiled, source, strict=True):
        assert actual.database == "test_disposable"
        assert (actual.role, actual.primary_schema, actual.private_schemas, actual.modules) == (
            original.role,
            original.primary_schema,
            original.private_schemas,
            original.modules,
        )
    with pytest.raises(AuthorityError):
        roles.compile_deployment_profiles("test_disposable", source_database="unlisted")
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("test_disposable", "general", "butler_general_rw")
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("butlers", "general", "unlisted_role")
    with pytest.raises(AuthorityError):
        roles.purpose_profile("unlisted", "dashboard")


def test_catalog_classification_refuses_missing_unknown_kind_owner_and_rls():
    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind

    expected = ObjectAuthority("general", "state", ObjectKind.TABLE, "migrator", (), "core")
    actual = dict(
        schema="general",
        name="state",
        kind="r",
        owner="migrator",
        rls=False,
        forced_rls=False,
        oid=1,
    )
    assert validate_relations([actual], (expected,)) == ((actual, expected),)
    with pytest.raises(AuthorityError):
        validate_relations([], (expected,))
    for change in (dict(name="undeclared"), dict(kind="f"), dict(owner="runtime")):
        with pytest.raises(AuthorityError):
            validate_relations([actual | change], (expected,))
    protected = ObjectAuthority(
        "general", "state", ObjectKind.TABLE, "migrator", (), "core", forced_rls=True
    )
    with pytest.raises(AuthorityError):
        validate_relations([actual | dict(rls=True)], (protected,))
    assert validate_relations([actual | dict(rls=True, forced_rls=True)], (protected,))


async def test_actual_credential_writer_cannot_fall_back_to_runtime_credentials(
    driver, monkeypatch
):
    from butlers.credential_store import create_system_global_credential_store_from_env

    _, pools, connect = driver
    monkeypatch.delenv("BUTLERS_CREDENTIAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime:synthetic@localhost/butlers")
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    assert pools == []
    connect.assert_not_called()
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL", "postgresql://credential:synthetic@localhost/unlisted"
    )
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    assert pools == []


def test_protected_function_maintenance_refuses_without_existing_operator_authority():
    from butlers.db_authority.interfaces import FUNCTIONS, install_functions

    definition = FUNCTIONS[0]
    connection = Mock()
    result = Mock()
    result.scalar_one.return_value = True  # Existing isolated NOLOGIN owner profile.
    result.mappings.return_value.one_or_none.return_value = None
    connection.execute.return_value = result
    connection.exec_driver_sql.return_value.scalar_one.return_value = False
    with pytest.raises(AuthorityError, match="maintenance prerequisite"):
        install_functions(connection, (definition,))
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements == [
        "SELECT session_user=current_user AND rolsuper "
        "FROM pg_catalog.pg_roles WHERE rolname=session_user"
    ]
    result.scalar_one.return_value = False
    connection.exec_driver_sql.reset_mock()
    with pytest.raises(AuthorityError, match="owner profile"):
        install_functions(connection, (definition,))
    connection.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize(
    "mismatch", [None, "columns", "constraints", "indexes", "policies", "triggers"]
)
def test_actual_protected_shape_comparison_refuses_drift_and_drops_only_owned_scratch(mismatch):
    from butlers.db_authority.interfaces import verify_protected_relation_shapes

    class Result:
        def __init__(self, rows=()):
            self.rows = rows

        def scalar_one(self):
            return True

        def mappings(self):
            return iter(self.rows)

    class Connection:
        def __init__(self):
            self.statements = []
            self.reads = []

        def exec_driver_sql(self, sql):
            self.statements.append(sql)
            return Result()

        def execute(self, sql, params):
            sql = str(sql)
            relation = params["relation"]
            self.reads.append((sql, relation))
            if "relforcerowsecurity AS forced" in sql:
                return Result(
                    (
                        {
                            "owner": "dnd_generation_owner",
                            "enabled": relation.endswith('"user_context"'),
                            "forced": relation.endswith('"user_context"'),
                        },
                    )
                )
            kind = (
                "policies"
                if "pg_catalog.pg_policy" in sql
                else "triggers"
                if "pg_catalog.pg_trigger" in sql
                else "indexes"
                if "pg_catalog.pg_index" in sql
                else "constraints"
                if "pg_catalog.pg_constraint" in sql
                else "columns"
            )
            # Catalog responses belong to the driver boundary. No installer,
            # comparison, source definitions or cleanup method is patched.
            value = "drift" if kind == mismatch and relation.startswith('"public".') else "source"
            return Result(({"attname": "synthetic_column", "catalog_shape": value},))

    connection = Connection()
    if mismatch is None:
        verify_protected_relation_shapes(connection, "dnd")
        assert len(connection.reads) == 33  # Three owners plus both sides of five catalogs.
    else:
        with pytest.raises(AuthorityError, match="differ[s]? from source"):
            verify_protected_relation_shapes(connection, "dnd")
    created = [sql for sql in connection.statements if sql.startswith("CREATE TEMPORARY TABLE")]
    dropped = [sql for sql in connection.statements if sql.startswith("DROP TABLE")]
    assert created and len(created) == len(dropped)
    for create, drop in zip(created, dropped, strict=True):
        name = create.split('"', 2)[1]
        assert name.startswith("_butlers_shape_")
        assert drop == f'DROP TABLE pg_temp."{name}"'
    assert not any(
        sql.startswith(("UPDATE ", "DELETE ", "ALTER TABLE public"))
        for sql in connection.statements
    )


def test_actual_protected_shape_cleanup_preserves_primary_failure():
    from butlers.db_authority.interfaces import verify_protected_relation_shapes

    primary = LookupError("synthetic catalog unavailable")
    cleanup = OSError("synthetic scratch cleanup unavailable")
    connection = Mock()
    connection.exec_driver_sql.return_value.scalar_one.return_value = True

    def read(sql, params):
        if "relforcerowsecurity AS forced" in str(sql):
            return Mock(
                mappings=lambda: (
                    {"owner": "dnd_generation_owner", "enabled": False, "forced": False},
                )
            )
        raise primary

    connection.execute.side_effect = read

    def execute(sql):
        if sql.startswith("DROP TABLE"):
            raise cleanup
        return Mock(scalar_one=lambda: True)

    connection.exec_driver_sql.side_effect = execute
    with pytest.raises(LookupError) as caught:
        verify_protected_relation_shapes(connection, "dnd")
    assert caught.value is primary
    assert primary.__notes__ == ["Protected source shape cleanup was incomplete"]
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert any(sql.startswith("CREATE TEMPORARY TABLE") for sql in statements)
    assert any(sql.startswith('DROP TABLE pg_temp."_butlers_shape_') for sql in statements)


def test_compiled_relation_manifest_preserves_real_private_and_narrow_reader_authority():
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    objects = {(row.schema, row.name): row for row in manifest.relations}
    assert len(manifest.profiles) == 13
    assert objects["chronicler_mem", "facts"].owner == "migration_selected"
    private = {permission.role for permission in objects["chronicler_mem", "facts"].permissions}
    assert private == {"butler_chronicler_rw", "dashboard_api"}
    inbox = {
        permission.role: permission.verbs
        for permission in objects["switchboard", "message_inbox"].permissions
    }
    for reader in ("relationship", "messenger", "travel"):
        assert inbox[f"butler_{reader}_rw"] == ("SELECT",)
    assert "butler_concierge_rw" not in inbox
    registry = {
        permission.role: permission.verbs
        for permission in objects["switchboard", "connector_registry"].permissions
    }
    assert registry["connector_writer"] == ("SELECT", "INSERT", "UPDATE")
    for identity in (
        ("education", "sessions"),
        ("general", "calendar_event_instances"),
        ("general", "calendar_sources"),
        ("general", "calendar_events"),
        ("general", "calendar_event_entities"),
    ):
        assert {permission.role: permission.verbs for permission in objects[identity].permissions}[
            "butler_chronicler_rw"
        ] == ("SELECT",)
    assert all(
        permission.role != "butler_chronicler_rw"
        for permission in objects["education", "state"].permissions
    )
    assert any(
        permission.role == "butler_concierge_rw"
        for permission in objects["concierge", "v_fleet_sessions"].permissions
    )
    assert objects["public", "user_context"].forced_rls is True
    assert objects["public", "user_context"].owner == "dnd_generation_owner"
    assert objects["dashboard_attention", "bootstrap_configuration"].owner == "maintenance_selected"
    assert ("public", "contacts") not in objects
    assert ("public", "facts") not in objects
    assert all(
        permission.role != "butler_calendar_rw"
        for row in manifest.relations
        for permission in row.permissions
    )
    for owner in ("butler_general_rw", "dnd_generation_owner", "dashboard_api"):
        with pytest.raises(AuthorityError):
            compile_relation_manifest(
                "butlers", owner, "maintenance_selected", source_database="butlers"
            )
    with pytest.raises(AuthorityError):
        compile_relation_manifest("butlers", "same", "same", source_database="butlers")
    with pytest.raises(AuthorityError):
        compile_relation_manifest(
            "butlers", "migration_selected", "maintenance_selected", source_database="unlisted"
        )


def test_source_partition_children_are_closed_and_sequences_bind_the_declared_parent():
    from butlers.db_authority.apply import (
        bind_partition_relations,
        validate_relations,
        validate_sequence_bindings,
    )
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind, Permission

    parent = ObjectAuthority(
        "switchboard",
        "message_inbox",
        ObjectKind.TABLE,
        "migration_selected",
        (Permission("butler_switchboard_rw", ("SELECT", "INSERT")),),
        "registered ingest",
    )
    child = dict(
        schema="switchboard",
        name="message_inbox_209901",
        kind="r",
        owner="migration_selected",
        oid=20,
        partition=True,
        rls=False,
        forced_rls=False,
    )
    link = dict(oid=20, parent_schema="switchboard", parent_name="message_inbox")
    declared = bind_partition_relations((child,), (parent,), (link,))
    assert declared[0] is parent
    assert declared[1].permissions == ()
    assert validate_relations(
        (child,), tuple(__import__("dataclasses").replace(row, required=False) for row in declared)
    )
    for actual, links in (
        (child | dict(partition=False), (link,)),
        (child | dict(kind="v"), (link,)),
        (child | dict(owner="butler_switchboard_rw"), (link,)),
        (child | dict(schema="public"), (link,)),
        (child, ()),
        (child, (link, link)),
        (child, (link | dict(parent_name="unlisted_parent"),)),
    ):
        with pytest.raises(AuthorityError):
            bind_partition_relations((actual,), (parent,), links)
    sequence = ObjectAuthority(
        "public",
        "audit_log_id_seq",
        ObjectKind.SEQUENCE,
        "migration_selected",
        (),
        "core audit identity",
        sequence_parent=("public", "audit_log", "id"),
    )
    actual = dict(oid=21)
    binding = dict(
        oid=21,
        parent_schema="public",
        parent_name="audit_log",
        parent_column="id",
        parent_owner="migration_selected",
        deptype="a",
    )
    validate_sequence_bindings(((actual, sequence),), (binding,))
    for bindings in (
        (),
        (binding, binding),
        (binding | dict(parent_column="other"),),
        (binding | dict(parent_owner="foreign_owner"),),
    ):
        with pytest.raises(AuthorityError):
            validate_sequence_bindings(((actual, sequence),), bindings)

    # Bind the sequence emitted by actual core_096, not a sequence invented for
    # its separate TEXT-keyed retention-policy table.
    import runpy
    from pathlib import Path

    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    emitted = []
    producer = runpy.run_path(
        str(
            Path(__file__).parents[2]
            / "alembic/versions/core/core_096_memory_retention_policies.py"
        )
    )
    with patch("alembic.op.execute", side_effect=emitted.append):
        producer["upgrade"]()
    statements = [" ".join(sql.split()) for sql in emitted]
    assert any(
        "CREATE TABLE IF NOT EXISTS public.memory_compaction_log ( id BIGSERIAL PRIMARY KEY," in sql
        for sql in statements
    )
    assert any(
        "CREATE TABLE IF NOT EXISTS public.memory_retention_policies ( kind TEXT PRIMARY KEY,"
        in sql
        for sql in statements
    )
    manifest = select_migration_manifest(
        compile_relation_manifest(
            "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
        ),
        (("core", None, None),),
    )
    rows = {row.identity: row for row in manifest.relations}
    compaction = rows["public", "memory_compaction_log_id_seq", "sequence", ""]
    assert compaction.owner == "migration_selected"
    assert compaction.sequence_parent == ("public", "memory_compaction_log", "id")
    assert compaction.required is False
    assert ("public", "memory_retention_policies_id_seq", "sequence", "") not in rows
    assert set(compaction.permissions) == {
        Permission(profile.role, ("USAGE",)) for profile in manifest.profiles
    }
    observed = dict(
        schema="public",
        name="memory_compaction_log_id_seq",
        kind="S",
        owner="migration_selected",
        oid=22,
        partition=False,
        rls=False,
        forced_rls=False,
    )
    assert bind_partition_relations((observed,), manifest.relations, ()) == manifest.relations
    bound = validate_relations((observed,), (compaction,))
    dependency = binding | dict(oid=22, parent_name="memory_compaction_log")
    validate_sequence_bindings(bound, (dependency,))
    for name in ("memory_retention_policies_id_seq", "unlisted_sequence"):
        with pytest.raises(AuthorityError):
            bind_partition_relations((observed | dict(name=name),), manifest.relations, ())
    for changed in (dict(kind="r"), dict(owner="foreign_owner"), dict(schema="foreign_schema")):
        with pytest.raises(AuthorityError):
            validate_relations((observed | changed,), (compaction,))
    for dependencies in (
        (),
        (dependency, dependency),
        (dependency | dict(parent_schema="foreign_schema"),),
        (dependency | dict(parent_name="memory_retention_policies"),),
        (dependency | dict(parent_column="kind"),),
        (dependency | dict(parent_owner="foreign_owner"),),
    ):
        with pytest.raises(AuthorityError):
            validate_sequence_bindings(bound, dependencies)


def test_selected_source_manifest_preserves_core_only_private_and_historical_boundaries():
    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    full = compile_relation_manifest(
        "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    private = select_migration_manifest(full, (("memory", "chronicler_mem", None),))
    assert private.schemas == ("chronicler_mem",)
    private_rows = {row.name: row for row in private.relations}
    assert private_rows["facts"].required is True
    assert private_rows["facts"].permissions == next(
        row.permissions
        for row in full.relations
        if (row.schema, row.name) == ("chronicler_mem", "facts")
    )
    for retired in ("embedding_versions", "rule_applications"):
        assert private_rows[retired].required is False
        assert private_rows[retired].permissions == ()
        assert not any(row.name == retired for row in full.relations)
    core = select_migration_manifest(full, (("core", None, None),))
    assert not ({"chronicler", "chronicler_mem", "switchboard"} & set(core.schemas))
    assert any(row.schema == "public" and row.name == "scheduled_tasks" for row in core.relations)
    assert any(
        row.schema == "public" and row.name == "audit_log" and row.required
        for row in core.relations
    )
    assert not any(
        row.schema == "public" and row.name == "scheduled_tasks" and row.permissions
        for row in core.relations
    )
    historical = select_migration_manifest(full, (("memory", "chronicler_mem", "mem_001"),))
    assert all(not row.required and not row.permissions for row in historical.relations)
    isolated = select_migration_manifest(full, (("memory", "isolated_admin", None),))
    assert isolated.schemas == ("isolated_admin",)
    assert all(not row.permissions for row in isolated.relations)
    assert any(row.name == "facts" and row.required for row in isolated.relations)
    production = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    with pytest.raises(AuthorityError):
        select_migration_manifest(production, (("memory", "isolated_admin", None),))
    for plans in (
        (),
        (("core", "chronicler_mem", None),),
        (("memory", "dashboard_auth", None),),
        (("unlisted_chain", "chronicler", None),),
        (("health", "chronicler", None),),
        (("memory", "chronicler_mem", object()),),
    ):
        with pytest.raises(AuthorityError):
            select_migration_manifest(full, plans)


def test_selected_revision_proof_uses_actual_chain_graph_and_same_held_driver():
    from butlers.db_authority.apply import verify_selected_revisions
    from butlers.migrations import get_chain_head

    head = get_chain_head("memory")
    connection = Mock()
    connection.exec_driver_sql.return_value = [(head,), ("core_280",)]
    verify_selected_revisions(connection, (("memory", "chronicler_mem", None),))
    connection.exec_driver_sql.assert_called_once_with(
        'SELECT version_num FROM "chronicler_mem".alembic_version'
    )
    for versions in ((), (("mem_001",),), ((head,), ("mem_001",))):
        connection.exec_driver_sql.return_value = versions
        with pytest.raises(AuthorityError, match="not installed"):
            verify_selected_revisions(connection, (("memory", "chronicler_mem", None),))
    connection.reset_mock()
    with pytest.raises(AuthorityError, match="not source-owned"):
        verify_selected_revisions(connection, (("memory", "chronicler_mem", "core_280"),))
    connection.exec_driver_sql.assert_not_called()
    with pytest.raises(AuthorityError, match="plan is required"):
        verify_selected_revisions(connection, None)


def test_function_manifest_binds_fixed_overloads_to_actual_consumers_and_selected_chains():
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import PURPOSE_OWNERS, runtime_profiles

    profiles = runtime_profiles("butlers")
    plans = (
        ("core", "general", None),
        ("switchboard", "switchboard", None),
        ("memory", "chronicler_mem", None),
        ("approvals", "relationship", None),
        ("general", "general", None),
    )
    rows = compile_function_manifest(profiles, "migration_selected", plans)
    indexed = {row.identity: row for row in rows}
    assert len(indexed) == len(rows)
    assert indexed["public", "qa_local_schedule_policy", ""].executors == ("butler_qa_rw",)
    assert indexed["public", "reserve_butler_probe", "text"].executors == (
        "butler_switchboard_rw",
        "dashboard_api",
    )
    for name in ("reserve_butler_probe", "record_butler_probe", "set_butler_registry_policy"):
        definition = next(row for row in rows if row.name == name)
        assert "session_user = current_user" not in definition.body
        assert "'dashboard_api'" in definition.body
    assert indexed[
        "public", "enqueue_premise_amendments", "text,text,timestamp with time zone,text"
    ].executors == tuple(p.role for p in profiles)
    assert indexed["chronicler_mem", "record_episode_tombstone", ""].executors == ()
    approval = indexed["relationship", "validate_approval_event_action_reference", ""]
    assert 'FROM "relationship".pending_actions' in approval.body
    assert (
        "reconciled row is final" in indexed["public", "qa_publication_operations_guard", ""].body
    )
    assert indexed["dashboard_auth", "cleanup", ""].executors == ("dashboard_auth_api",)
    allowed = (
        {p.role for p in profiles}
        | set(PURPOSE_OWNERS)
        | {
            "dashboard_api",
            "dashboard_auth_api",
            "connector_writer",
            "credential_store_writer",
            "migration_selected",
        }
    )
    assert all(set(row.executors) <= allowed for row in rows)
    assert all(
        row.owner in PURPOSE_OWNERS and row.owner != "migration_selected"
        for row in rows
        if row.security_definer
    )
    assert {row.name for row in rows if "migration_selected" in row.executors} == {
        "host",
        "cost_claim_restore_row",
        "ensure_registry_control",
        "switchboard_connector_heartbeat_log_drop_expired_partitions",
        "switchboard_message_inbox_drop_expired_partitions",
    }
    assert all(
        row.settings
        in {
            ("search_path=pg_catalog, pg_temp",),
            ("row_security=on", "search_path=pg_catalog, pg_temp"),
        }
        for row in rows
    )
    assert all(row.producer and row.signature is not None for row in rows)
    assert (
        compile_function_manifest(profiles, "migration_selected", (("core", None, "core_198"),))
        == ()
    )


def test_function_population_and_convergence_refuse_ambiguous_catalog_before_effects():
    from types import SimpleNamespace

    from butlers.db_authority.apply import (
        converge_ordinary_functions,
        validate_function_population,
    )
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.interfaces import _FUNCTION_ACL, _FUNCTION_CATALOG
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in compile_function_manifest(
            runtime_profiles("butlers"), "migration_selected", (("core", None, None),)
        )
        if row.name == "resolve_relationship_prepared_action_status"
    )
    observed = dict(
        oid=71, schema="public", name=definition.name, arguments="uuid", owner=definition.owner
    )
    bound = validate_function_population((observed,), (definition,), {definition.identity})
    for population, required in (
        ((), {definition.identity}),
        ((observed, observed), {definition.identity}),
        ((observed | {"owner": "hostile_owner"},), {definition.identity}),
        ((observed | {"arguments": "text"},), {definition.identity}),
        ((observed | {"name": "unlisted_function"},), {definition.identity}),
    ):
        with pytest.raises(AuthorityError):
            validate_function_population(population, (definition,), required)

    class CatalogConnection:
        def __init__(self, maintenance, owner_profile=True):
            self.maintenance = maintenance
            self.owner_profile = owner_profile
            self.row = dict(
                oid=71,
                owner=definition.owner,
                definer=True,
                config=["search_path=public"],
                body="old body",
            )
            self.acl = [dict(role="PUBLIC", privilege="EXECUTE", grantable=False)]
            self.effects = []

        def execute(self, statement, parameters):
            if "WITH owners AS" in str(statement):
                assert parameters == {"owners": ["relationship_status_owner"], "count": 1}
                return SimpleNamespace(scalar_one=lambda: self.owner_profile)
            if str(statement) == _FUNCTION_CATALOG:
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(one_or_none=lambda: self.row)
                )
            if str(statement) == _FUNCTION_ACL:
                return SimpleNamespace(mappings=lambda: tuple(self.acl))
            raise AssertionError("unexpected catalog read")

        def exec_driver_sql(self, statement):
            if statement.startswith("SELECT current_user=session_user AND rolsuper"):
                return SimpleNamespace(scalar_one=lambda: self.maintenance)
            self.effects.append(statement)
            if statement == definition.sql:
                self.row.update(body=definition.body, config=list(definition.settings))
            elif statement.startswith("ALTER FUNCTION"):
                assert statement.endswith('OWNER TO "relationship_status_owner"')
                self.row["owner"] = definition.owner
            elif statement.startswith("REVOKE ALL ON FUNCTION"):
                self.acl.clear()
            elif statement.startswith("GRANT EXECUTE ON FUNCTION"):
                self.acl.append(
                    dict(role="butler_switchboard_rw", privilege="EXECUTE", grantable=False)
                )
            else:
                raise AssertionError("unexpected catalog mutation")

    refused = CatalogConnection(False)
    with pytest.raises(AuthorityError):
        converge_ordinary_functions(refused, bound)
    assert refused.effects == []
    connection = CatalogConnection(True)
    converge_ordinary_functions(connection, bound)
    assert connection.effects[0] == definition.sql
    assert len(connection.effects) == 4  # Fixed owner transfer is an explicit effect.
    assert connection.acl == [
        dict(role="butler_switchboard_rw", privilege="EXECUTE", grantable=False)
    ]
    connection.effects.clear()
    converge_ordinary_functions(connection, bound)
    assert connection.effects == []
    unsafe_owner = CatalogConnection(True, owner_profile=False)
    with pytest.raises(AuthorityError, match="owner profile"):
        converge_ordinary_functions(unsafe_owner, bound)
    assert unsafe_owner.effects == []
    predecessor = observed | {"owner": "migration_selected"}
    assert validate_function_population(
        (predecessor,), (definition,), {definition.identity}, migration_owner="migration_selected"
    )
    with pytest.raises(AuthorityError):
        validate_function_population((predecessor,), (definition,), {definition.identity})
    previous_owner = CatalogConnection(True)
    previous_owner.row["owner"] = "migration_selected"
    previous_bound = validate_function_population(
        (predecessor,), (definition,), {definition.identity}, migration_owner="migration_selected"
    )
    converge_ordinary_functions(previous_owner, previous_bound)
    assert previous_owner.row["owner"] == "relationship_status_owner"
    assert any(
        statement.endswith('OWNER TO "relationship_status_owner"')
        for statement in previous_owner.effects
    )
    connection.row["oid"] = 72
    with pytest.raises(AuthorityError):
        converge_ordinary_functions(connection, bound)
    assert connection.effects == []


@pytest.mark.parametrize("catalog_ok", [False, None, 1, True])
async def test_real_runtime_handoff_rechecks_object_acl_after_successful_role_proof(catalog_ok):
    from butlers.db_authority.catalog import _CATALOG_OBJECT_SQL

    connection = AsyncMock()
    connection.fetchrow.return_value = dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )
    connection.fetchval.return_value = catalog_ok
    database = Database("butlers", schema="general", role="butler_general_rw", min_pool_size=0)
    database._runtime_profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    database._role_verified = True
    database._verified_role = database.role
    if catalog_ok is True:
        await database._setup_connection(connection)
    else:
        with pytest.raises(RuntimeError, match="Runtime database role acquisition failed"):
            await database._setup_connection(connection)
    connection.execute.assert_awaited_once_with('SET ROLE "butler_general_rw"')
    assert connection.fetchrow.await_count == 1
    statement, bound = connection.fetchval.call_args.args
    assert statement == _CATALOG_OBJECT_SQL
    import json

    authority = json.loads(bound)
    assert all("body" not in row for row in authority["functions"])
    assert any(row["name"] == "context_dnd_mutate" for row in authority["functions"])
    assert all(
        not isinstance(executor, str) or not executor.startswith("manifest_")
        for row in authority["functions"]
        for executor in row["executors"]
    )
    # A prior successful handoff is not a cache: the next acquisition reads
    # fresh role and object facts and must refuse a revoked catalogue grant.
    if catalog_ok is True:
        connection.fetchval.return_value = False
        with pytest.raises(RuntimeError, match="Runtime database role acquisition failed"):
            await database._setup_connection(connection)
        assert connection.fetchval.await_count == 2


@pytest.mark.parametrize(
    ("name", "enabled", "forced"),
    [("task_continuity", True, True), ("insight_feedback", True, False), ("state", False, False)],
)
def test_actual_source_manifest_binds_both_row_security_flags(name, enabled, forced):
    """A grant convergence may neither disable nor silently add an RLS boundary."""
    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import public_objects

    row = next(row for row in public_objects("butlers", "normal_owner") if row.name == name)
    actual = dict(
        schema="public",
        name=name,
        kind="r",
        owner="normal_owner",
        oid=42,
        rls=enabled,
        forced_rls=forced,
    )
    assert validate_relations([actual], (row,)) == ((actual, row),)
    for changed in (actual | {"rls": not enabled}, actual | {"forced_rls": not forced}):
        with pytest.raises(AuthorityError):
            validate_relations([changed], (row,))


def test_ha_mapping_manifest_preserves_actual_core246_rls_and_policy_identities(monkeypatch):
    """Execute the landed producer; catalog declarations preserve its exact boundary."""
    import runpy
    from pathlib import Path

    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import (
        relation_security_flags,
        security_expression_tokens,
        serving_catalog_contract,
        shared_reader_objects,
    )

    producer = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2] / "alembic/versions/core/"
            "core_246_ha_person_mapping_receipts.py"
        )
    )
    emitted = []
    monkeypatch.setattr(producer["op"], "execute", lambda sql: emitted.append(str(sql)))
    producer["upgrade"]()
    normalized = [" ".join(sql.split()) for sql in emitted]
    identity = ("connectors", "home_assistant_persons")
    assert producer["_MAPPINGS"] == ".".join(identity)
    assert f"ALTER TABLE {producer['_MAPPINGS']} ENABLE ROW LEVEL SECURITY" in normalized
    assert f"ALTER TABLE {producer['_MAPPINGS']} NO FORCE ROW LEVEL SECURITY" in normalized
    row = next(
        row for row in shared_reader_objects("normal_owner") if (row.schema, row.name) == identity
    )
    assert relation_security_flags(row) == (True, False)
    actual = dict(
        schema=identity[0],
        name=identity[1],
        kind="r",
        owner="normal_owner",
        oid=42,
        rls=True,
        forced_rls=False,
    )
    assert validate_relations([actual], (row,)) == ((actual, row),)
    for changed in (actual | {"rls": False}, actual | {"forced_rls": True}):
        with pytest.raises(AuthorityError):
            validate_relations([changed], (row,))
    policies = [
        policy
        for policy in serving_catalog_contract("butlers")["policies"]
        if policy["name"] in {"ha_person_mappings_select", "ha_person_mappings_insert"}
    ]
    assert {(policy["schema"], policy["table"]) for policy in policies} == {identity}
    assert len(policies) == 2
    by_name = {policy["name"]: policy for policy in policies}
    read = by_name["ha_person_mappings_select"]
    write = by_name["ha_person_mappings_insert"]
    assert (read["command"], read["roles"], read["permissive"], read["check"]) == (
        "r",
        ["PUBLIC"],
        True,
        None,
    )
    assert read["using"] == security_expression_tokens(
        "(CURRENT_USER = ANY (ARRAY['butler_switchboard_rw'::name, 'butler_chronicler_rw'::name]))"
    )
    assert (write["command"], write["roles"], write["permissive"], write["using"]) == (
        "a",
        ["PUBLIC"],
        True,
        None,
    )
    assert write["check"] == security_expression_tokens(
        "(CURRENT_USER = 'butler_switchboard_rw'::name)"
    )
    assert (
        "CREATE POLICY ha_person_mappings_select ON connectors.home_assistant_persons "
        "FOR SELECT TO PUBLIC USING (current_user IN ('butler_switchboard_rw', 'butler_chronicler_rw'))"
    ) in normalized
    assert (
        "CREATE POLICY ha_person_mappings_insert ON connectors.home_assistant_persons "
        "FOR INSERT TO PUBLIC WITH CHECK (current_user = 'butler_switchboard_rw')"
    ) in normalized


def test_pinned_predecessor_retirement_never_adopts_discovered_function_authority():
    from butlers.db_authority.interfaces import (
        _RETIRED_PREDECESSOR_FUNCTIONS,
        classify_retired_interfaces,
        retire_predecessor_interfaces,
    )

    observed = [
        dict(schema=r[0], name=r[1], signature=r[2], digest=r[3], owner=r[4] or "maintenance")
        for r in _RETIRED_PREDECESSOR_FUNCTIONS
    ]
    selected = classify_retired_interfaces(observed, "maintenance")
    assert len(selected) == 23
    for change in (
        dict(name="unlisted"),
        dict(signature="text,text"),
        dict(digest="0" * 64),
        dict(owner="untrusted"),
    ):
        with pytest.raises(AuthorityError):
            classify_retired_interfaces([observed[0] | change], "maintenance")
    with pytest.raises(AuthorityError):
        classify_retired_interfaces([observed[0], observed[0]], "maintenance")
    connection = Mock()
    retire_predecessor_interfaces(connection, (selected, ("dnd_generation_admin",), True))
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements[0].startswith("DROP TRIGGER runtime_attention_plant_legacy")
    assert sum(statement.startswith("DROP FUNCTION ") for statement in statements) == 23
    assert statements[-2:] == [
        'DROP TABLE "dnd_generation_admin".bootstrap_configuration RESTRICT',
        'DROP SCHEMA "dnd_generation_admin" RESTRICT',
    ]
    assert all("CASCADE" not in statement for statement in statements)
    assert not any("DELETE" in statement or "UPDATE" in statement for statement in statements)


def _predecessor_capsule():
    """Only authority metadata; no user rows, credential values or SQL input."""
    import hashlib
    import json

    from butlers.db_authority.apply import _PREDECESSOR_COMMIT, predecessor_revision_vectors
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    catalog = dict(
        database=dict(name="butlers", owner="maintenance", acl=[]),
        schemas=[],
        relations=[],
        functions=[],
        memberships=[],
        defaults=[],
        roles=[
            dict(
                name="normal_owner",
                login=True,
                inherit=False,
                super=False,
                createdb=False,
                createrole=False,
                replication=False,
                bypassrls=False,
            )
        ],
        versions=[
            dict(schema=schema, revisions=list(revisions))
            for schema, revisions in sorted(predecessor_revision_vectors(manifest).items())
        ],
        configurations=[],
    )
    capsule = dict(
        format="butlers-authority-rollback-v1",
        predecessor_commit=_PREDECESSOR_COMMIT,
        migration_role="normal_owner",
        actor="maintenance",
        release_references=dict(
            chart="chart-previous", image="image-previous", old_secret="key-v1"
        ),
        catalog=catalog,
        catalog_sha256=hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
    )
    return manifest, capsule


@pytest.mark.parametrize("fault", ["foreign_role", "privileged_role", "duplicate", "configuration"])
def test_preserving_rollback_rejects_unbound_metadata_before_effects(fault):
    import hashlib
    import json

    from butlers.db_authority.apply import validate_rollback_capsule

    manifest, capsule = _predecessor_capsule()
    validate_rollback_capsule(capsule, manifest, "maintenance")
    catalog = capsule["catalog"]
    if fault == "foreign_role":
        catalog["roles"][0]["name"] = "unlisted_foreign_principal"
    elif fault == "privileged_role":
        catalog["roles"][0]["super"] = True
    elif fault == "duplicate":
        catalog["roles"].append(catalog["roles"][0].copy())
    else:
        catalog["configurations"].append(dict(schema="public", values=[dict(sql="untrusted")]))
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(AuthorityError):
        validate_rollback_capsule(capsule, manifest, "maintenance")


@pytest.mark.parametrize("fault", [None, "changed-owner", "active-serving"])
def test_actual_r0_executes_pinned_initializer_and_requires_independent_catalog_readback(fault):
    """SQL protocol only: the genuine old-profile GRANT proof is a separate PG case."""
    import hashlib
    import json
    from types import SimpleNamespace

    from butlers.db_authority import apply

    _, capsule = _predecessor_capsule()
    catalog = capsule["catalog"]
    catalog["database"]["acl"] = [dict(role="normal_owner", verb="CONNECT", grantable=False)]
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def one(self):
            return self.value

        def mappings(self):
            return self

        def __iter__(self):
            return iter(self.value)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            assert sql == apply.pinned_predecessor_initializer()
            assert "validated preserving operator rollback required" in sql
            assert "\\set ON_ERROR_STOP" not in sql
            db.effects.append("pinned-init")
            # A driver-bound response, not a simulated proof that SQL grants
            # work. Final readback MUST observe it rather than assuming success.
            db.database_acl = catalog["database"]["acl"].copy()

    class Connection:
        def __init__(self):
            self.info = {}
            self.effects = []
            self.database_acl = []
            self.connection = SimpleNamespace(driver_connection=SimpleNamespace(cursor=Cursor))

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "pg_locks" in sql:
                return Result(True)
            if "WITH RECURSIVE principals" in sql:
                return Result(fault != "active-serving")
            if "current_setting" in sql:
                return Result("normal_owner")
            if "set_config" in sql:
                return Result(None)
            if sql == apply._ROLLBACK_DATABASE_ACL:
                return Result(self.database_acl)
            if sql == apply._ROLLBACK_ROLES:
                return Result(catalog["roles"])
            if sql in {
                apply._NAMESPACE_CATALOG,
                apply._PARTITION_LINKS,
                apply._RELATIONS,
                apply._ROLLBACK_FUNCTIONS,
                apply._ROLLBACK_MEMBERSHIPS,
                apply._ROLLBACK_EFFECTIVE_DEFAULTS,
            }:
                return Result([])
            if "to_regclass(:target) IS NOT NULL" in sql:
                return Result(
                    any(
                        params["target"] == '"' + row["schema"] + '".alembic_version'
                        for row in catalog["versions"]
                    )
                )
            raise AssertionError("unexpected actual source query")

        def exec_driver_sql(self, sql):
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if sql == "SHOW search_path":
                return Result('"$user", public')
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql == "SELECT current_database(), session_user":
                return Result(("butlers", "maintenance"))
            for row in catalog["versions"]:
                if sql == 'SELECT version_num FROM "' + row["schema"] + '".alembic_version':
                    return Result([(revision,) for revision in row["revisions"]])
            if "FROM pg_catalog.pg_database d JOIN" in sql:
                owner = "foreign_owner" if fault == "changed-owner" else "maintenance"
                return Result(SimpleNamespace(one=lambda: dict(name="butlers", owner=owner)))
            if "set_config" in sql:
                return Result(None)
            raise AssertionError("unexpected actual source command")

    # The database query uses mappings().one(); retain that native result shape.
    old_mappings = Result.mappings

    def mappings(self):
        return self.value if isinstance(self.value, SimpleNamespace) else old_mappings(self)

    Result.mappings = mappings
    db = Connection()
    if fault is not None:
        with pytest.raises(AuthorityError):
            apply.replay_predecessor_r0(db, capsule)
        assert db.effects == []
    else:
        receipt = apply.replay_predecessor_r0(db, capsule)
        assert receipt["transition"] == "R0"
        assert db.effects == ["pinned-init"]
        assert db.database_acl == catalog["database"]["acl"]


def test_actual_selected_core_schemas_keep_source_wrappers_without_extra_serving_grants():
    from butlers.db_authority.apply import predecessor_function_sources
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    rows = compile_function_manifest(
        profiles,
        "normal_owner",
        (("core", "general", None), ("core", "qa", None)),
    )
    local = [r for r in rows if r.name == "connectors_filtered_events_ensure_partition"]
    assert {r.schema for r in local} == {"general", "qa", "connectors"}
    assert all(r.executors == () for r in local if r.schema in {"general", "qa"})
    assert all(r.owner == "connector_partition_owner" for r in local)
    old = predecessor_function_sources(profiles)
    general = [
        sql
        for key, sql in old.items()
        if key[:2] == ("general", "connectors_filtered_events_ensure_partition")
    ]
    assert len(general) == 1
    assert "SET search_path = connectors, public, pg_temp" in general[0]
    assert "SET search_path = pg_catalog, pg_temp" not in general[0]
    assert not any(
        key[:2] == ("chronicler_mem", "connectors_filtered_events_ensure_partition") for key in old
    )


def test_preserving_reverse_detaches_foreign_reach_without_adopting_its_identity():
    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    commands = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return iter(self.rows)

        def scalar_one(self):
            return 160000

    class Connection:
        def execute(self, sql, params):
            assert str(sql) == apply._ROLLBACK_MEMBERSHIPS
            assert "maintenance" not in params["roles"]
            return Result(
                [
                    dict(role="butler_chronicler_rw", member="new_serving_login"),
                    dict(role="foreign_capability", member="butler_chronicler_rw"),
                ]
            )

        def exec_driver_sql(self, sql):
            commands.append(sql)
            return Result([])

    capsule["catalog"]["memberships"] = [
        dict(
            role="butler_chronicler_rw", member="normal_owner", admin=False, inherit=True, set=True
        )
    ]
    apply._restore_predecessor_role_state(Connection(), capsule["catalog"], manifest, "maintenance")
    assert commands[:2] == [
        'REVOKE "butler_chronicler_rw" FROM "new_serving_login"',
        'REVOKE "foreign_capability" FROM "butler_chronicler_rw"',
    ]
    assert commands[-1] == (
        'GRANT "butler_chronicler_rw" TO "normal_owner" WITH INHERIT TRUE, SET TRUE'
    )
    assert all(
        not sql.startswith(("DROP ", 'ALTER ROLE "new_serving_login"', 'GRANT "foreign'))
        for sql in commands
    )


@pytest.mark.parametrize("fault", [None, "login", "membership", "owner", "default"])
def test_reverse_readback_accepts_only_inert_new_source_roles(fault):
    from copy import deepcopy

    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    expected = capsule["catalog"]
    current = deepcopy(expected)
    role = "credential_store_writer"
    current["roles"].append(
        dict(
            name=role,
            login=False,
            inherit=False,
            super=False,
            createdb=False,
            createrole=False,
            replication=False,
            bypassrls=False,
        )
    )
    current["defaults"] = [
        dict(owner=role, schema=None, kind="f", role="PUBLIC", verb="EXECUTE", grantable=False)
    ]
    if fault == "login":
        current["roles"][-1]["login"] = True
    elif fault == "membership":
        current["memberships"].append(dict(role=role, member="new_login"))
    elif fault == "owner":
        current["schemas"].append(dict(name="public", owner=role, acl=[]))
    elif fault == "default":
        current["defaults"][0]["role"] = "butler_chronicler_rw"
    if fault:
        with pytest.raises(AuthorityError):
            apply._predecessor_catalog_projection(current, expected, manifest, "maintenance")
    else:
        projected = apply._predecessor_catalog_projection(
            current, expected, manifest, "maintenance"
        )
        assert apply._catalog_equivalent(projected, expected)
        assert len(current["roles"]) == 2  # no cluster-wide DROP/mutation hidden by comparison


@pytest.mark.parametrize("fault", [None, "body", "owner", "settings", "missing"])
def test_reverse_callable_preflight_is_bound_to_live_fixed_sources(fault):
    import hashlib

    from butlers.db_authority import apply
    from butlers.db_authority.grants import compile_function_manifest, deployment_function_plans
    from butlers.db_authority.interfaces import final_functions

    manifest, capsule = _predecessor_capsule()
    ordinary = compile_function_manifest(
        manifest.profiles, manifest.migration_owner, deployment_function_plans(manifest.profiles)
    )[0]
    projection = next(
        row
        for row in final_functions(manifest.profiles, manifest.migration_owner)
        if row.name == "write_audit_projection"
    )

    def row(definition):
        return dict(
            schema=definition.schema,
            name=definition.name,
            signature=definition.signature,
            owner=definition.owner,
            definer=definition.security_definer,
            settings=list(getattr(definition, "settings", ("search_path=pg_catalog, pg_temp",))),
            digest=hashlib.sha256(definition.body.encode()).hexdigest(),
            acl=[],
        )

    current = dict(functions=[row(ordinary), row(projection)])
    predecessor = dict(functions=[row(ordinary)])
    if fault == "body":
        current["functions"][0]["digest"] = "0" * 64
    elif fault == "owner":
        current["functions"][0]["owner"] = "foreign_owner"
    elif fault == "settings":
        current["functions"][0]["settings"] = ["search_path=public"]
    elif fault == "missing":
        current["functions"].pop()
    if fault:
        with pytest.raises(AuthorityError):
            apply._verify_r1_callable_sources(manifest, current, predecessor)
    else:
        apply._verify_r1_callable_sources(manifest, current, predecessor)


@pytest.mark.parametrize("stage", range(6))
def test_actual_attention_installer_advances_only_verified_source_states_without_reset(stage):
    """Driver protocol control; migrated old-stamp/data cases remain genuine PG work."""

    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    definitions = {
        f'"{row.schema}"."{row.name}"({row.signature})': row
        for row in interfaces.final_functions(profiles, "normal_owner")
    }

    class Result:
        def __init__(self, value=None):
            self.value = value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

        def __iter__(self):
            return iter(self.value or ())

    class Connection:
        def __init__(self):
            self.names = set(interfaces._ATTENTION_STAGE_NAMES.get(stage, ()))
            self.marker = stage == 5
            self.scratch_markers = set()
            self.commands = []
            self.owner_selects = []

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "count(*)=:count" in sql:
                return Result(True)
            if "WHERE p.oid=pg_catalog.to_regprocedure(:signature)" in sql:
                definition = definitions[params["signature"]]
                return Result(
                    dict(
                        oid=params["signature"],
                        owner=definition.owner,
                        definer=definition.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body=definition.body,
                    )
                )
            if sql == interfaces._FUNCTION_ACL:
                definition = definitions[params["oid"]]
                return Result(
                    [
                        dict(role=role, privilege="EXECUTE", grantable=False)
                        for role in definition.executors
                        if role != definition.owner
                    ]
                )
            if "tgname='runtime_attention_condition_marker_sync_trigger'" in sql:
                return Result(self.marker)
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                self.owner_selects.append(params["name"])
                return Result(
                    None
                    if params["name"] not in self.names
                    else dict(relkind="r", owner="runtime_attention_outbox_owner")
                )
            target = params.get("relation", "")
            if sql == interfaces._SHAPE_SECURITY:
                enabled = any(
                    name in target
                    for name in (
                        "runtime_attention_outbox",
                        "runtime_attention_delivery_lease",
                        "runtime_attention_condition_episodes",
                    )
                )
                return Result(
                    [dict(owner="runtime_attention_outbox_owner", enabled=enabled, forced=enabled)]
                )
            if sql == interfaces._SHAPE_TRIGGERS:
                marker = (
                    target in self.scratch_markers
                    if target.startswith("pg_temp.")
                    else ("runtime_attention_outbox" in target and self.marker)
                )
                return Result([dict(marker=True)] if marker else [])
            if target:
                return Result([])
            raise AssertionError("unexpected installer driver query")

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            if sql.startswith("SELECT"):
                return Result(True)
            if sql.startswith('CREATE TABLE "public".'):
                self.names.add(sql.split('"')[3])
            elif sql == interfaces._FRESH_ATTENTION_SQL:
                self.names.update(interfaces._ATTENTION_STAGE_NAMES[5])
            elif sql == interfaces._ATTENTION_POLICY_SQL:
                self.marker = True
            elif (
                sql.startswith("CREATE TRIGGER")
                and "runtime_attention_condition_marker_sync_trigger" in sql
            ):
                if " ON pg_temp." in sql:
                    target = sql.split(" ON ", 1)[1].split(" FOR EACH", 1)[0]
                    self.scratch_markers.add(target)
                elif " ON public.runtime_attention_outbox " in sql:
                    self.marker = True
            return Result(None)

    connection = Connection()
    interfaces.install_protected_state(connection, profiles, "normal_owner", group="attention")
    assert connection.names == interfaces._ATTENTION_STAGE_NAMES[5]
    assert connection.marker is True
    assert not any(
        sql.startswith(("UPDATE ", "DELETE ", "TRUNCATE ")) for sql in connection.commands
    )
    actual_inserts = [sql for sql in connection.commands if sql.startswith("INSERT INTO")]
    for name in interfaces._ATTENTION_STAGE_NAMES.get(stage, ()):
        assert not any(f'"{name}"' in sql for sql in actual_inserts)
    # The parser receives the actual old source shape before final DDL.
    if 0 < stage < 4:
        old_body = interfaces._ATTENTION_OLD_OUTBOX_SHAPES[1 if stage < 3 else 3]
        assert any(old_body in sql for sql in connection.commands if sql.startswith("CREATE TEMP"))
    assert all(name in connection.owner_selects for name in interfaces._ATTENTION_STAGE_NAMES[5])


def test_external_capsule_timestamp_readback_preserves_native_configuration_value():
    import json
    from datetime import UTC, datetime

    from butlers.db_authority.apply import _catalog_equivalent

    stamp = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    catalog = dict(
        configurations=[
            dict(
                schema="runtime_attention_admin",
                values=[dict(producers_enabled=False, producer_activated_at=stamp)],
            )
        ]
    )
    external = json.loads(json.dumps(catalog, default=str))
    assert _catalog_equivalent(catalog, external)
    external["configurations"][0]["values"][0]["producers_enabled"] = True
    assert not _catalog_equivalent(catalog, external)


def test_genuine_catalog_fixture_selects_real_source_before_managed_migration(
    tmp_path, monkeypatch
):
    """The fixture calls the real compiler; only provisioning I/O is doubled."""
    import runpy
    from pathlib import Path

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    fixture = namespace["authority_database"].__wrapped__
    migrated = []

    def migrate(container, name, chains, schemas):
        profiles = roles.compile_deployment_profiles(name, source_database="butlers")
        assert len(profiles) == 1
        assert profiles[0].database == name
        assert profiles[0].role == "butler_chronicler_rw"
        assert profiles[0].private_schemas == ("chronicler_mem",)
        assert chains == ["core", "chronicler", "memory"]
        assert schemas == dict(core="chronicler", chronicler="chronicler", memory="chronicler_mem")
        migrated.append(name)
        return "synthetic-migrated-url"

    fixture.__globals__["create_migrated_test_db"] = migrate
    fixture.__globals__["migration_bootstrap_db_url"] = lambda container, name: (
        "synthetic-bootstrap-url"
    )
    assert fixture(object(), tmp_path, monkeypatch) == (
        "synthetic-migrated-url",
        "synthetic-bootstrap-url",
    )
    assert len(migrated) == 1
    serving = roles.load_runtime_profile(migrated[0], "chronicler", "butler_chronicler_rw")
    assert serving.private_schemas == ("chronicler_mem",)
    # A serving caller cannot reuse the administrative source-name mapping.
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("butlers", "chronicler", "butler_chronicler_rw")


def test_source_security_descriptors_bind_real_trigger_functions_and_preserve_sql_tokens():
    from butlers.db_authority.grants import (
        security_expression_tokens,
        serving_catalog_contract,
    )

    contract = serving_catalog_contract("butlers")
    functions = {(row["schema"], row["name"], row["signature"]) for row in contract["functions"]}
    assert contract["policies"] and contract["triggers"]
    assert len({(row["schema"], row["table"], row["name"]) for row in contract["policies"]}) == len(
        contract["policies"]
    )
    assert len({(row["schema"], row["table"], row["name"]) for row in contract["triggers"]}) == len(
        contract["triggers"]
    )
    assert all(
        (row["function_schema"], row["function_name"], row["signature"]) in functions
        for row in contract["triggers"]
    )
    assert security_expression_tokens("(CURRENT_USER = 'two  words'::name)") == (
        security_expression_tokens(" ( current_user\n= 'two  words' :: name ) ")
    )
    for altered in (
        "(current_user = 'two words'::name)",  # literal contents stay significant
        "(current_user = 'two  words'::text)",  # casts stay significant
        "current_user = 'two  words'::name",  # grouping is not discarded
        "(current_user <> 'two  words'::name)",
    ):
        assert security_expression_tokens(altered) != security_expression_tokens(
            "(CURRENT_USER = 'two  words'::name)"
        )
    for unsupported in ("true; SELECT 1", "true -- ignored", "true /* ignored */", ""):
        with pytest.raises(AuthorityError):
            security_expression_tokens(unsupported)


@pytest.mark.parametrize(
    "fault", ["unknown", "stale", "mixed", "missing_chain", "missing_schema", "private_foreign"]
)
def test_actual_rollback_refuses_unpinned_complete_revision_profile_before_initializer(
    monkeypatch, fault
):
    """Execute R0 with an otherwise intact capsule; no old-init on forged stamps."""
    import hashlib
    import json

    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    catalog = capsule["catalog"]
    selected = next(row for row in catalog["versions"] if row["schema"] == "chronicler")
    if fault == "unknown":
        selected["revisions"].append("unlisted_revision")
    elif fault == "stale":
        selected["revisions"].remove("core_280")
        selected["revisions"].append("core_279")
    elif fault == "mixed":
        selected["revisions"].append("core_279")
    elif fault == "missing_chain":
        selected["revisions"].remove("chronicler_026")
    elif fault == "missing_schema":
        catalog["versions"].remove(selected)
    else:
        next(row for row in catalog["versions"] if row["schema"] == "chronicler_mem")[
            "revisions"
        ] = ["core_280", "mem_015"]
    selected["revisions"].sort()
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()
    effects = []
    connection = object()
    monkeypatch.setattr(apply, "_rollback_actor", lambda conn: "maintenance")
    monkeypatch.setattr(apply, "require_rollback_lock", lambda conn: None)
    monkeypatch.setattr(apply, "_rollback_source_manifest", lambda conn, role: manifest)
    monkeypatch.setattr(
        apply, "_replay_predecessor_initializer", lambda *args: effects.append("init")
    )
    with pytest.raises(AuthorityError, match="complete revision profile"):
        apply.replay_predecessor_r0(connection, capsule)
    assert effects == []


def test_actual_rollback_pinned_revision_profile_keeps_module_and_private_heads():
    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    apply.validate_rollback_capsule(capsule, manifest, "maintenance")
    vectors = {r["schema"]: r["revisions"] for r in capsule["catalog"]["versions"]}
    assert vectors["chronicler"] == ["chronicler_026", "core_280"]
    assert vectors["chronicler_mem"] == ["mem_015"]
    assert "home_004" in vectors["health"]
    assert "sw_051" in vectors["switchboard"]
    assert all("core_281" not in v for v in vectors.values())


@pytest.mark.parametrize(
    "fault", [None, "other_shape", "existing_function", "unbound_function", "other_relation"]
)
def test_actual_r1_marker_shape_preview_is_exact_and_always_rolled_back(fault):
    """Execute the actual source transition consumer; native PG proof is separate."""
    import hashlib

    from butlers.db_authority import apply

    manifest, expected = _predecessor_capsule()
    expected = expected["catalog"]
    sources = apply.predecessor_function_sources(manifest.profiles)
    marker = next(
        key
        for key in sources
        if key[:3] == ("public", "runtime_attention_plant_legacy_debounce_marker", "")
    )
    expected["functions"] = [
        dict(schema=marker[0], name=marker[1], signature=marker[2], digest=marker[3])
    ]
    if fault == "unbound_function":
        expected["functions"][0]["digest"] = "0" * 64
    original_shape = hashlib.sha256(
        b"complete pinned columns policies indexes and marker"
    ).hexdigest()
    before = dict(
        schema="public",
        name="model_dispatch_attempts",
        kind="r",
        rls=False,
        forced_rls=False,
        partition=False,
        parents=[],
        shape=original_shape,
    )
    if fault == "other_relation":
        before["name"] = "sessions"
    expected["relations"] = [before]
    current = dict(relations=[before | {"shape": "f" * 64}])

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class Preview:
        def __init__(self, connection):
            self.connection = connection

        def rollback(self):
            self.connection.events.append("rollback-savepoint")
            self.connection.function = self.connection.trigger = False

    class Connection:
        def __init__(self):
            self.events = []
            self.function = self.trigger = False

        def begin_nested(self):
            self.events.append("savepoint")
            return Preview(self)

        def exec_driver_sql(self, sql):
            if sql == "SET LOCAL search_path=pg_catalog":
                self.events.append("canonical-deparse-path")
                return Result(None)
            if "to_regprocedure" in sql:
                return Result(fault != "existing_function")
            if sql == sources[marker]:
                self.function = True
                self.events.append("create-pinned-function")
                return Result(None)
            if sql == apply._PREDECESSOR_MARKER_TRIGGER:
                assert self.function
                self.trigger = True
                self.events.append("create-pinned-trigger")
                return Result(None)
            if sql == "SELECT 'public.model_dispatch_attempts'::pg_catalog.regclass::oid":
                return Result(41)
            raise AssertionError("unexpected transition command")

        def execute(self, statement, params):
            assert str(statement) == apply._ROLLBACK_RELATION_SHAPE
            assert params == {"oid": 41}
            self.events.append("read-full-relation-shape")
            return Result(
                original_shape
                if self.function and self.trigger and fault != "other_shape"
                else "a" * 64
            )

    connection = Connection()
    if fault is None:
        assert apply._verify_r1_relation_shapes(connection, manifest, current, expected) is True
        assert connection.events == [
            "savepoint",
            "canonical-deparse-path",
            "create-pinned-function",
            "create-pinned-trigger",
            "read-full-relation-shape",
            "rollback-savepoint",
        ]
    else:
        with pytest.raises(AuthorityError):
            apply._verify_r1_relation_shapes(connection, manifest, current, expected)
        if fault == "other_shape":
            assert connection.events[-1] == "rollback-savepoint"
        else:
            assert connection.events == []
    assert connection.function is False
    assert connection.trigger is False
    assert not any("upgrade" in event for event in connection.events)


def test_actual_retirement_accepts_only_exact_present_legacy_marker():
    from butlers.db_authority import interfaces

    marker = (
        "runtime_attention_plant_legacy_debounce_marker_trigger",
        "public",
        "model_dispatch_attempts",
        "public",
        "runtime_attention_plant_legacy_debounce_marker",
        7,
        False,
        True,
        "O",
        0,
        "",
        "",
        0,
        False,
        False,
        0,
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self.value

        def all(self):
            return self.value

    class Connection:
        observed = [marker]
        effects = []

        def exec_driver_sql(self, sql):
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if "FROM pg_catalog.pg_trigger" in sql:
                return Result(self.observed)
            raise AssertionError("unexpected retirement command")

        def execute(self, statement, params):
            sql = str(statement)
            if "FROM pg_catalog.pg_proc" in sql or "FROM pg_catalog.pg_namespace n JOIN" in sql:
                return Result([])
            raise AssertionError("unexpected retirement query")

    connection = Connection()
    assert interfaces.inspect_retired_interfaces(connection, "normal_owner") == ((), (), True)
    for index, value in (
        (1, "foreign"),
        (5, 5),
        (6, True),
        (7, False),
        (8, "D"),
        (9, 1),
        (11, "00"),
        (13, True),
    ):
        changed = list(marker)
        changed[index] = value
        connection.observed = [tuple(changed)]
        with pytest.raises(AuthorityError):
            interfaces.inspect_retired_interfaces(connection, "normal_owner")
    assert connection.effects == []


@pytest.mark.parametrize("primary", ["chronicler", "qa"])
def test_actual_selected_deployment_installer_never_grants_absent_runtime(
    tmp_path, monkeypatch, primary
):
    """Real roster/compiler/installer, with administrative driver IO doubled only."""
    from butlers.db_authority import grants, interfaces

    root = tmp_path / "selected_roster"
    entry = root / primary
    entry.mkdir(parents=True)
    (entry / "butler.toml").write_text(
        f'[butler]\nname="{primary}"\n[butler.db]\nname="selected_db"\nschema="{primary}"\n[modules.{primary}]\n'
    )
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    manifest = grants.compile_relation_manifest(
        "selected_db", "normal_owner", "maintenance", source_database="selected_db"
    )
    bootstrap = roles.bootstrap_role_manifest(
        "selected_db", "normal_owner", source_database="selected_db"
    )
    allowed = set(bootstrap["roles"]) | {
        "normal_owner",
        "maintenance",
        bootstrap["restore_executor"],
    }
    assert not {"butler_general_rw", "butler_switchboard_rw"} & allowed
    for row in manifest.relations:
        assert all(permission.role in allowed for permission in row.permissions)
    compiled = grants.compile_function_manifest(
        manifest.profiles, "normal_owner", grants.deployment_function_plans(manifest.profiles)
    )
    assert all(set(function.executors) <= allowed for function in compiled)
    contract = grants.compiled_security_contract(manifest)
    assert all(set(policy["roles"]) <= allowed | {"PUBLIC"} for policy in contract["policies"])
    policy_sql = interfaces._attention_policy_sql(manifest.profiles)
    assert "TO butler_switchboard_rw" not in policy_sql
    assert "runtime_attention_outbox_owner" in policy_sql
    definitions = interfaces.final_functions(manifest.profiles, "maintenance")
    index = {interfaces._signature(row): row for row in definitions}

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    class Connection:
        def __init__(self):
            self.created = set()
            self.acls = {key: set() for key in index}
            self.effects = []

        def execute(self, statement, params):
            sql = str(statement)
            if "count(*)=:count" in sql:
                return Result(True)
            if sql == interfaces._FUNCTION_CATALOG:
                key = params["signature"]
                row = index[key]
                return Result(
                    None
                    if key not in self.created
                    else dict(
                        oid=key,
                        owner=row.owner,
                        definer=row.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body=row.body,
                    )
                )
            if sql == interfaces._FUNCTION_ACL:
                return Result(
                    [
                        dict(role=role, privilege="EXECUTE", grantable=False)
                        for role in sorted(self.acls[params["oid"]])
                    ]
                )
            raise AssertionError("unexpected installer query")

        def exec_driver_sql(self, sql):
            # Decode the actual unbound driver template before catalog modeling.
            sql = sql % {}
            if sql.startswith("SELECT session_user=current_user AND rolsuper"):
                return Result(True)
            for key, row in index.items():
                if sql == row.sql:
                    self.created.add(key)
                    self.effects.append("create-fixed-function")
                    return Result(None)
            if sql.startswith("ALTER FUNCTION "):
                return Result(None)
            if sql.startswith("GRANT EXECUTE ON FUNCTION "):
                key, role = sql.removeprefix("GRANT EXECUTE ON FUNCTION ").split(" TO ")
                assert role.startswith('"') and role.endswith('"')
                role = role[1:-1]
                if role not in allowed:
                    raise AssertionError("undeclared runtime grant attempted")
                self.acls[key].add(role)
                self.effects.append("grant-declared-executor")
                return Result(None)
            raise AssertionError("unexpected installer effect")

    connection = Connection()
    interfaces.install_functions(connection, definitions)
    assert connection.created == set(index)
    assert "grant-declared-executor" in connection.effects
    # The native policy parser likewise gets no absent TO principal.
    statements = []
    connection.exec_driver_sql = statements.append
    for name in ("runtime_attention_outbox", "runtime_attention_delivery_lease"):
        interfaces._parse_security_shape(
            connection, "pg_temp.selected", name, profiles=manifest.profiles
        )
    assert statements
    assert all(" TO butler_switchboard_rw " not in sql for sql in statements)
    assert sum(sql.startswith("CREATE POLICY") for sql in statements) == 2


def test_actual_home_module_chain_alias_keeps_health_owned_objects_and_sequence():
    import tomllib

    from butlers.db_authority import grants
    from butlers.migrations import get_module_migration_chains
    from butlers.modules.registry import default_registry

    with (roles._ROSTER_ROOT / "health" / "butler.toml").open("rb") as stream:
        source_modules = tomllib.load(stream)["modules"]
    modules = default_registry().load_from_config(source_modules)
    assert get_module_migration_chains(modules, "health")["home_assistant"] == "home"
    manifest = grants.compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    assert ("home", "health", None) in grants.deployment_function_plans(manifest.profiles)
    selected = grants.select_migration_manifest(manifest, (("home", "health", None),))
    rows = {row.identity: row for row in selected.relations}
    table = rows["health", "ha_command_log", "table", ""]
    sequence = rows["health", "ha_command_log_id_seq", "sequence", ""]
    assert table.required is True
    assert table.owner == sequence.owner == "normal_owner"
    assert sequence.sequence_parent == ("health", "ha_command_log", "id")
    assert any(
        permission.role == "butler_health_rw" and "INSERT" in permission.verbs
        for permission in table.permissions
    )
    assert any(
        permission.role == "butler_health_rw" and permission.verbs == ("USAGE",)
        for permission in sequence.permissions
    )
    with pytest.raises(AuthorityError):
        grants.select_migration_manifest(manifest, (("home", "chronicler", None),))


@pytest.mark.parametrize(
    ("chain", "target", "expected"),
    [
        ("memory", "chronicler_mem", "trg_episodes_preserve_provenance"),
        ("memory", "isolated_memory", "trg_episodes_preserve_provenance"),
        ("approvals", "isolated_approvals", "trg_approval_events_immutable"),
    ],
)
def test_selected_module_security_uses_actual_plan_without_fabricated_runtime_profile(
    chain, target, expected
):
    from butlers.db_authority.grants import (
        compile_relation_manifest,
        compiled_security_contract,
        select_migration_manifest,
    )

    full = compile_relation_manifest(
        "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    selected = select_migration_manifest(full, ((chain, target, None),))
    contract = compiled_security_contract(selected)
    assert any(row["schema"] == target and row["name"] == expected for row in contract["triggers"])
    assert all(row["schema"] == target for row in contract["triggers"])
    assert selected.profiles == full.profiles
    if target.startswith("isolated_"):
        assert all(not row.permissions for row in selected.relations)
        assert not any(profile.primary_schema == target for profile in selected.profiles)


@pytest.mark.parametrize(
    ("revision", "present", "absent"),
    [
        ("core_100", (), ("restore_drill_executor", "dashboard_auth")),
        ("core_196", ("restore_drill_executor",), ("dashboard_auth",)),
        ("core_240", ("restore_drill_executor", "dashboard_auth"), ()),
    ],
)
def test_historical_core_namespace_selection_follows_real_source_graph(revision, present, absent):
    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    full = compile_relation_manifest(
        "test_history", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    selected = select_migration_manifest(full, (("core", None, revision),))
    assert set(present) <= set(selected.schemas)
    assert not set(absent) & set(selected.schemas)
    assert all(not row.permissions and not row.required for row in selected.relations)


@pytest.mark.parametrize(
    ("revision", "owners"),
    [
        ("core_100", set()),
        ("core_196", {"restore_drill_executor_owner", "restore_drill_executor_audit_writer"}),
        (
            "core_197",
            {
                "restore_drill_executor_owner",
                "restore_drill_executor_audit_writer",
                "dnd_generation_owner",
            },
        ),
        (
            "core_198",
            {
                "restore_drill_executor_owner",
                "restore_drill_executor_audit_writer",
                "dnd_generation_owner",
                "runtime_attention_outbox_owner",
            },
        ),
    ],
)
def test_historical_protected_definitions_follow_real_edges_with_no_serving_grants(
    revision, owners
):
    from butlers.db_authority.grants import compile_relation_manifest
    from butlers.db_authority.interfaces import selected_protected_functions

    manifest = compile_relation_manifest(
        "test_history", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    definitions = selected_protected_functions(
        manifest.profiles, manifest.migration_owner, (("core", None, revision),)
    )
    assert {row.owner for row in definitions} == owners
    assert all(not row.executors for row in definitions)
    assert all(not row.schema.endswith("_admin") for row in definitions)


def test_historical_administration_closes_function_acl_without_rewriting_body():
    from types import SimpleNamespace

    from butlers.db_authority.apply import close_administrative_function_privileges
    from butlers.db_authority.grants import FunctionAuthority
    from butlers.db_authority.interfaces import _FUNCTION_ACL

    definition = FunctionAuthority(
        "public",
        "historical_owned",
        "",
        "normal_owner",
        (),
        False,
        "CREATE FUNCTION public.historical_owned() RETURNS void LANGUAGE plpgsql ",
        "BEGIN RETURN; END",
        "actual source declaration",
        ("search_path=pg_catalog, pg_temp",),
    )

    class Connection:
        def __init__(self):
            self.acls = [{"role": "PUBLIC", "privilege": "EXECUTE", "grantable": False}]
            self.effects = []

        def execute(self, statement, params):
            assert str(statement) == _FUNCTION_ACL
            assert params == {"oid": 51}
            return SimpleNamespace(mappings=lambda: iter(self.acls))

        def exec_driver_sql(self, sql):
            assert sql == 'REVOKE ALL ON FUNCTION "public"."historical_owned"() FROM PUBLIC'
            self.effects.append(sql)
            self.acls = []

    connection = Connection()
    close_administrative_function_privileges(connection, (({"oid": 51}, definition),))
    assert len(connection.effects) == 1
    assert connection.acls == []


def test_bounded_historical_administration_never_certifies_current_rls_or_serving_acl():
    from dataclasses import replace

    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind, Permission

    declared = ObjectAuthority(
        "public",
        "user_context",
        ObjectKind.TABLE,
        "normal_owner",
        (),
        "bounded source-owned history",
        required=False,
    )
    actual = dict(
        schema="public",
        name="user_context",
        kind="r",
        owner="normal_owner",
        oid=31,
        rls=False,
        forced_rls=False,
    )
    # A pre-core197 table is administrative history, never a current serving
    # catalog. The ordinary admission path retains its strict RLS check.
    with pytest.raises(AuthorityError, match="row security"):
        validate_relations((actual,), (declared,))
    assert validate_relations((actual,), (declared,), administrative_history=True) == (
        (actual, declared),
    )
    for invalid in (
        actual | {"owner": "foreign_owner"},
        actual | {"rls": None},
        actual | {"name": "unlisted_relation"},
    ):
        with pytest.raises(AuthorityError):
            validate_relations((invalid,), (declared,), administrative_history=True)
    serving = replace(declared, permissions=(Permission("butler_general_rw", ("SELECT",)),))
    with pytest.raises(AuthorityError, match="serving privilege"):
        validate_relations((actual,), (serving,), administrative_history=True)


async def test_actual_checkpoint_consumers_require_only_source_owned_connector_operations():
    """Actual production cursor/settings calls; controlled driver is not PG proof."""
    from butlers.connectors import cursor_store
    from butlers.db_authority.apply import namespace_permissions
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    row = next(
        row
        for row in manifest.relations
        if (row.schema, row.name) == ("switchboard", "connector_registry")
    )
    allowed = {
        verb
        for permission in row.permissions
        if permission.role == "connector_writer"
        for verb in permission.verbs
    }
    state = {"checkpoint_cursor": None, "settings": {}}

    class Driver:
        def require(self, verbs):
            if not set(verbs) <= allowed:
                raise PermissionError("Source manifest lacks a required checkpoint operation")

        async def execute(self, sql, *args):
            assert sql == cursor_store._UPSERT_SQL
            self.require(("SELECT", "INSERT", "UPDATE"))
            state["checkpoint_cursor"] = args[2]

        async def fetchrow(self, sql, *args):
            if sql == cursor_store._UPSERT_SETTINGS_SQL:
                self.require(("SELECT", "INSERT", "UPDATE"))
                state["settings"] |= args[2]
            else:
                assert sql in (cursor_store._SELECT_SQL, cursor_store._SELECT_SETTINGS_SQL)
                self.require(("SELECT",))
            return state.copy()

        @asynccontextmanager
        async def acquire(self):
            yield self

    driver = Driver()
    await cursor_store.save_cursor(
        driver, "synthetic", "endpoint", "first", parent_endpoint_identity=None
    )
    await cursor_store.save_cursor(
        driver, "synthetic", "endpoint", "second", parent_endpoint_identity=None
    )
    assert await cursor_store.load_cursor(driver, "synthetic", "endpoint") == "second"
    assert await cursor_store.save_connector_settings(
        driver, "synthetic", "endpoint", {"first": 1}
    ) == {"first": 1}
    assert await cursor_store.save_connector_settings(
        driver, "synthetic", "endpoint", {"second": 2}
    ) == {"first": 1, "second": 2}
    assert await cursor_store.load_connector_settings(driver, "synthetic", "endpoint") == {
        "first": 1,
        "second": 2,
    }
    assert allowed == {"SELECT", "INSERT", "UPDATE"}
    assert "connector_writer" in namespace_permissions(manifest.relations)["switchboard"]


async def test_actual_chronicler_readers_use_only_named_source_evidence():
    """Actual adapter SQL consumers with manifest-derived visibility, not PG execution."""
    import uuid
    from datetime import UTC, datetime

    from butlers.chronicler.adapters import CalendarCompletedAdapter, CoreSessionsAdapter
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    readable = {
        (row.schema, row.name)
        for row in manifest.relations
        if any(
            permission.role == "butler_chronicler_rw" and "SELECT" in permission.verbs
            for permission in row.permissions
        )
    }
    observed = set()

    class Driver:
        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetchval(self, sql, *args):
            assert "information_schema.tables" in sql
            if "SELECT ARRAY" in sql:
                return [name for name in args[1] if (args[0], name) not in readable]
            name = "sessions" if "'sessions'" in sql else "calendar_event_entities"
            return (args[0], name) in readable

        async def fetch(self, sql, *args):
            identities = (
                (("education", "sessions"),)
                if '"education".sessions' in sql
                else (("general", "calendar_event_entities"),)
                if '"general".calendar_event_entities' in sql
                else (
                    ("general", "calendar_event_instances"),
                    ("general", "calendar_events"),
                    ("general", "calendar_sources"),
                )
            )
            assert set(identities) <= readable
            observed.update(identities)
            return []

    pool = Driver()
    sessions = CoreSessionsAdapter(("education",))
    calendar = CalendarCompletedAdapter(("general",))
    assert await sessions._fetch_sessions(pool, "education", None) == ([], None)
    assert await calendar._fetch_instances(pool, "general", None, datetime.now(UTC)) == []
    assert await calendar._fetch_event_entities(pool, "general", [uuid.uuid4()]) == {}
    assert observed == {
        ("education", "sessions"),
        ("general", "calendar_event_instances"),
        ("general", "calendar_sources"),
        ("general", "calendar_events"),
        ("general", "calendar_event_entities"),
    }
    assert ("education", "state") not in readable
    assert ("education", "state_store") not in readable


@pytest.mark.parametrize("predecessor", ["absent", "pinned", "forged", "foreign-owner", "unknown"])
def test_actual_historical_restore_edge_checks_retired_provenance_before_ddl(predecessor):
    """Execute both real preflights; only the physical catalog/DDL driver is doubled."""
    from butlers.db_authority import interfaces

    class ReachedOwnedDDL(Exception):
        pass

    class Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    pinned = next(
        row
        for row in interfaces._RETIRED_PREDECESSOR_FUNCTIONS
        if row[0] == "restore_drill_executor_admin"
    )

    class Connection:
        info = {}

        def __init__(self):
            self.effects = []
            self.commands = []

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            if "actor.rolsuper AND actor.rolname=session_user" in sql:
                return Result(("butlers", "normal_owner", True))
            if sql == "SELECT current_database(), current_user":
                return Result(("butlers", "normal_owner"))
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if "FROM pg_catalog.pg_trigger" in sql:
                return Result(())
            if sql.startswith("SELECT singleton, migration_role::text"):
                return Result([(True, "normal_owner", "maintenance")])
            if sql in ("RESET ROLE", 'SET ROLE "normal_owner"'):
                return Result(None)
            if sql == interfaces._FRESH_RESTORE_SQL:
                self.effects.append("restore-ddl")
                raise ReachedOwnedDDL
            raise AssertionError("unexpected historical-edge driver command")

        def execute(self, statement, params):
            sql = str(statement)
            if "FROM pg_catalog.pg_proc p" in sql:
                if predecessor == "absent":
                    return Result([])
                return Result(
                    [
                        dict(
                            schema=pinned[0],
                            name=pinned[1],
                            signature=pinned[2],
                            owner=pinned[4] or "maintenance",
                            digest="0" * 64 if predecessor == "forged" else pinned[3],
                        )
                    ]
                )
            if "FROM pg_catalog.pg_namespace n JOIN" in sql:
                return Result(
                    []
                    if predecessor == "absent"
                    else [
                        dict(
                            name=pinned[0],
                            owner="foreign" if predecessor == "foreign-owner" else "maintenance",
                        )
                    ]
                )
            if "FROM pg_catalog.pg_class c JOIN" in sql and "relkind NOT IN" in sql:
                return Result(
                    [
                        dict(
                            name="unlisted"
                            if predecessor == "unknown"
                            else "bootstrap_configuration",
                            kind="r",
                            owner="maintenance",
                        )
                    ]
                )
            if "FROM pg_catalog.pg_attribute a" in sql:
                return Result(
                    [
                        ("singleton", "boolean", True),
                        ("migration_role", "name", True),
                        ("bootstrap_role", "name", True),
                    ]
                )
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                return Result(None)
            raise AssertionError("unexpected historical-edge catalog query")

    connection = Connection()
    if predecessor in ("absent", "pinned"):
        with pytest.raises(ReachedOwnedDDL):
            interfaces.install_revision_group(connection, "restore")
        assert connection.effects == ["restore-ddl"]
    else:
        with pytest.raises(AuthorityError):
            interfaces.install_revision_group(connection, "restore")
        assert connection.effects == []
    assert connection.commands[-1] == 'SET ROLE "normal_owner"'


@pytest.mark.parametrize(
    ("component", "enabled", "fault"),
    [
        ("producers", False, None),
        ("producers", True, None),
        ("reissue", False, None),
        ("reissue", True, None),
        ("conditions", False, None),
        ("conditions", True, None),
        ("marker", False, None),
        ("marker", True, None),
        ("reissue", False, "actor"),
        ("reissue", False, "lock"),
        ("reissue", False, "serving"),
        ("reissue", False, "body"),
        ("reissue", False, "owner"),
        ("marker", True, "partial"),
        ("producers", False, "write"),
    ],
)
def test_actual_operator_attention_transition_owns_source_and_atomic_state(
    component, enabled, fault
):
    """Real operator/installer predicates; physical SQL catalog/transaction protocol only is doubled.

    These controls do not prove PostgreSQL parsing, lock behavior or installed
    shape. Those remain the genuine migration/attention PG case obligations.
    """
    import copy

    from butlers.db_authority import apply, interfaces
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    definitions = interfaces.protected_group_functions(profiles, "normal_owner", ("attention",))
    by_signature = {interfaces._signature(d): d for d in definitions}

    class Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    class Transaction:
        def __init__(self, driver):
            self.driver = driver
            self.before = copy.deepcopy(driver.state)

        def rollback(self):
            self.driver.state = self.before
            self.driver.rollbacks += 1

        def commit(self):
            self.driver.commits += 1

    class Driver:
        info = {"butlers.source_database": "butlers"}

        def __init__(self):
            self.state = {"marker": component != "marker" or not enabled, "control": None}
            self.commands = []
            self.rollbacks = 0
            self.commits = 0
            self.effects = []

        def begin_nested(self):
            return Transaction(self)

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            if "actor.rolsuper AND actor.rolname=session_user" in sql:
                return Result(("butlers", "normal_owner", fault != "actor"))
            if (
                "SELECT current_user=session_user AND rolsuper" in sql
                or "SELECT session_user=current_user AND rolsuper" in sql
            ):
                return Result(True)
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql == "SELECT current_database(), session_user":
                return Result(("butlers", "maintenance"))
            if "p.proname='runtime_attention_plant_legacy_debounce_marker'" in sql:
                return Result(())
            if sql.startswith("SELECT to_regprocedure("):
                marker = self.state["marker"]
                return Result((marker, not marker if fault == "partial" else marker))
            if sql.startswith("LOCK TABLE "):
                return Result(None)
            if sql == apply._ATTENTION_MARKER_TRIGGER:
                self.state["marker"] = True
                self.effects.append("marker-create")
                return Result(None)
            if sql.startswith("DROP TRIGGER runtime_attention_condition_marker_sync_trigger"):
                self.state["marker"] = False
                self.effects.append("marker-drop")
                return Result(None)
            if sql.startswith("DROP FUNCTION public.runtime_attention_sync_condition_marker"):
                return Result(None)
            if sql.lstrip().startswith(
                "UPDATE public.runtime_attention_condition_episodes AS marker"
            ):
                self.effects.append("backfill")
                return Result(None)
            if sql.lstrip().startswith(
                "CREATE OR REPLACE FUNCTION public.runtime_attention_sync_condition_marker"
            ):
                self.state["marker"] = True
                return Result(None)
            if sql.startswith(("ALTER FUNCTION ", "REVOKE ", "GRANT ")):
                return Result(None)
            if sql.startswith(
                (
                    "CREATE TEMPORARY TABLE ",
                    "CREATE UNIQUE INDEX ",
                    "CREATE INDEX ",
                    "CREATE POLICY ",
                    "CREATE TRIGGER ",
                    "DROP TABLE ",
                )
            ):
                return Result(None)
            raise AssertionError("unexpected operator driver command")

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "pg_catalog.pg_locks" in sql:
                return Result(fault != "lock")
            if "pg_catalog.pg_stat_activity" in sql:
                return Result(fault != "serving")
            if "WHERE n.nspname=ANY(:schemas)" in sql:
                return Result([])
            if "WHERE n.nspname='public' AND p.proname=ANY(:names)" in sql:
                return Result(
                    [
                        (d.name, d.signature)
                        for d in definitions
                        if self.state["marker"]
                        or d.name != "runtime_attention_sync_condition_marker"
                    ]
                )
            if "WHERE p.oid=pg_catalog.to_regprocedure(:signature)" in sql:
                d = by_signature[params["signature"]]
                if d.name == "runtime_attention_sync_condition_marker" and not self.state["marker"]:
                    return Result(None)
                return Result(
                    dict(
                        oid=d.name,
                        owner=d.owner,
                        definer=d.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body="foreign" if fault == "body" else d.body,
                    )
                )
            if "WHERE p.oid=:oid" in sql:
                d = next(d for d in definitions if d.name == params["oid"])
                return Result(
                    [
                        dict(role=r, privilege="EXECUTE", grantable=False)
                        for r in d.executors
                        if r != d.owner
                    ]
                )
            if "WITH owners AS" in sql:
                return Result(fault != "owner")
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                return Result(dict(relkind="r", owner="runtime_attention_outbox_owner"))
            if "c.relrowsecurity AS enabled" in sql:
                name = params["relation"].split(".")[-1].strip('"')
                rls = name in interfaces._RELATION_POLICIES
                return Result(
                    [dict(owner="runtime_attention_outbox_owner", enabled=rls, forced=rls)]
                )
            if "relation" in params:
                # Both parser/native result sets are driver facts here, not SQL
                # evidence. The genuine PG controls compare populated catalogs.
                return Result([])
            if sql.startswith("UPDATE public.runtime_attention_"):
                self.state["control"] = params["enabled"]
                self.effects.append("control-update")
                if fault == "write":
                    raise ValueError("driver write failed")
                return Result([(params["enabled"],)])
            raise AssertionError("unexpected operator driver query")

    driver = Driver()
    original = copy.deepcopy(driver.state)
    if fault:
        with pytest.raises(ValueError if fault == "write" else AuthorityError):
            apply.transition_runtime_attention(driver, component=component, enabled=enabled)
        assert driver.state == original
        assert driver.commits == 0
        if fault != "write":
            assert not driver.effects
    else:
        result = apply.transition_runtime_attention(driver, component=component, enabled=enabled)
        assert result == {"component": component, "enabled": enabled}
        assert driver.commits == 1
        if component == "marker":
            assert driver.state["marker"] is enabled
            assert ("backfill" in driver.effects) is enabled
        else:
            assert driver.state["control"] is enabled
        assert not any(sql.lstrip().startswith(("DELETE ", "TRUNCATE ")) for sql in driver.commands)
        assert driver.rollbacks == 2


@pytest.mark.parametrize(
    "name",
    (
        "observe_runtime_attention_models",
        "observe_runtime_attention_fleet_halt",
        "observe_runtime_attention_conditions",
        "reissue_runtime_attention_episode",
    ),
)
def test_actual_dashboard_attention_source_has_only_fixed_dashboard_authority(name):
    """Compile the real final producer; SQL execution remains genuine PG proof.

    The actual API's pool is dashboard_api, not the normal migration owner.
    These four function definitions are the exact installer/catalog inputs.
    """
    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in interfaces.final_functions(runtime_profiles("butlers"), "normal_owner")
        if row.name == name
    )
    assert definition.executors == ("dashboard_api",)
    assert definition.owner == "runtime_attention_outbox_owner"
    assert definition.security_definer is True
    assert "IF current_setting('role', true) IS DISTINCT FROM 'dashboard_api' THEN" in (
        definition.body
    )
    assert "USING ERRCODE = '42501'" in definition.body
    assert "forbids SET ROLE" not in definition.body
    assert "normal_owner" not in definition.body
    # Preserve the actual producer's remaining query/state/CAS logic exactly;
    # independent PG controls exercise the guarded statements under real roles.
    assert "SET search_path = pg_catalog, pg_temp" in definition.header


@pytest.mark.parametrize("selected_schema", ("switchboard", "selected_registry"))
def test_finance_boot_witness_manifest_preserves_landed_selected_producer(selected_schema):
    """Current sw052 is a fixed Finance read, not a catalog-discovered grant."""
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import runtime_profiles

    profiles = runtime_profiles("butlers")
    plans = (("switchboard", selected_schema, None),)
    rows = compile_function_manifest(profiles, "ordinary_migration_owner", plans)
    matches = [row for row in rows if row.name == "finance_warning_boot_current"]
    assert len(matches) == 1
    row = matches[0]
    assert row.identity == ("public", "finance_warning_boot_current", "uuid,bigint")
    assert row.owner == "registry_control_owner"  # REQ021 fixed pure-reader purpose.
    assert row.executors == ("butler_finance_rw",)
    assert row.security_definer is True
    assert row.settings == ("search_path=pg_catalog, pg_temp",)
    assert "LANGUAGE plpgsql STABLE SECURITY DEFINER" in row.header
    assert row.producer == "roster/switchboard/migrations/052_finance_warning_boot_read.py"
    assert f'FROM "{selected_schema}".butler_registry_control_plane c' in row.body
    assert f'LEFT JOIN "{selected_schema}".butler_boot_registrations h' in row.body
    assert (
        "pg_catalog.current_setting('role',true) IS DISTINCT FROM 'butler_finance_rw'" in row.body
    )
    assert "WHERE c.name='finance'" in row.body
    assert "p_epoch<=0 THEN RETURN NULL" in row.body
    assert not any(token in row.body for token in ("INSERT ", "UPDATE ", "DELETE ", "GRANT "))
    for absent in ((), (("core", "general", None),), (("switchboard", selected_schema, "sw_051"),)):
        assert not any(
            item.name == row.name
            for item in compile_function_manifest(profiles, "ordinary_migration_owner", absent)
        )
    without_finance = tuple(profile for profile in profiles if profile.role != "butler_finance_rw")
    no_finance_row = next(
        item
        for item in compile_function_manifest(without_finance, "ordinary_migration_owner", plans)
        if item.name == row.name
    )
    assert no_finance_row.executors == ()


def test_actual_serving_catalog_requires_isolated_owners_for_every_managed_definer():
    """Exercise the real source-to-admission compiler, not a diagnostic manifest."""
    from butlers.db_authority.grants import serving_catalog_contract
    from butlers.db_authority.roles import PURPOSE_OWNERS, bootstrap_role_manifest

    contract = serving_catalog_contract("butlers")
    definers = [row for row in contract["functions"] if row["definer"]]
    assert definers
    assert all(row["owner"] in PURPOSE_OWNERS for row in definers)
    bootstrap = bootstrap_role_manifest(
        "butlers", "ordinary_migration_owner", source_database="butlers"
    )
    assert all(row["owner"] in bootstrap["roles"] for row in definers)
    assert all(row["owner"] not in row["executors"] for row in definers)
    relations = {(row["schema"], row["name"]): row for row in contract["relations"]}
    assert relations["connectors", "filtered_events"]["owner"] == "connector_partition_owner"
    assert relations["switchboard", "message_inbox"]["owner"] == "switchboard_partition_owner"
    schemas = {row["name"]: row for row in contract["schemas"]}
    assert schemas["connectors"]["creators"] == ["connector_partition_owner"]
    assert schemas["switchboard"]["creators"] == ["switchboard_partition_owner"]
    assert all(
        not row["creators"]
        for row in contract["schemas"]
        if row["name"] not in {"connectors", "switchboard"}
    )
    probe = [
        p
        for p in relations["public", "model_catalog"]["permissions"]
        if p["role"] == "model_probe_owner"
    ]
    assert {(p["verb"], tuple(p["columns"])) for p in probe} == {
        ("SELECT", ("id",)),
        (
            "UPDATE",
            (
                "last_verified_at",
                "last_verified_latency_ms",
                "last_verified_ok",
                "last_verified_error",
                "updated_at",
            ),
        ),
    }
    restore_sequence = [
        p
        for p in relations["public", "cost_claim_events_id_seq"]["permissions"]
        if p["role"] == "cost_claim_restore_owner"
    ]
    assert restore_sequence == [
        {"role": "cost_claim_restore_owner", "verb": "UPDATE", "columns": []}
    ]


def test_ordinary_purpose_policy_reverse_preserves_full_shape_and_savepoint_cleanup():
    """Execute the source reverse planner; native PostgreSQL hashes are separate proof."""
    from types import SimpleNamespace

    from butlers.db_authority import apply, grants, interfaces

    manifest = grants.compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    changes = interfaces.ordinary_security_policy_changes(manifest)
    assert {key[:2] for key in changes} == {
        ("public", "cost_claims"),
        ("public", "cost_claim_events"),
        ("public", "cost_claim_resolutions"),
        ("public", "insight_amendments"),
        ("switchboard", "butler_registry_control_plane"),
        ("switchboard", "butler_boot_registrations"),
    }
    before = dict(schema="public", name="cost_claims", shape="complete-captured-shape")

    class Connection:
        def __init__(self, wrong_shape=False):
            self.events = []
            self.wrong_shape = wrong_shape

        def begin_nested(self):
            self.events.append("SAVEPOINT")
            return SimpleNamespace(rollback=lambda: self.events.append("ROLLBACK SAVEPOINT"))

        def exec_driver_sql(self, sql):
            self.events.append(sql)
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return SimpleNamespace(scalar_one=lambda: True)

        def execute(self, sql, params):
            if str(sql) == "SELECT pg_catalog.to_regclass(:target)::oid":
                assert params == {"target": '"public"."cost_claims"'}
                return SimpleNamespace(scalar_one=lambda: 81)
            assert str(sql) == apply._ROLLBACK_RELATION_SHAPE and params == {"oid": 81}
            restored = any(
                s.startswith('CREATE POLICY "cost_claims_restore_owner"')
                and "cost_claim_restore_owner" not in s
                for s in self.events
            )
            return SimpleNamespace(
                scalar_one=lambda: (
                    before["shape"] if restored and not self.wrong_shape else "wrong-full-shape"
                )
            )

    successful = Connection()
    apply._preview_predecessor_purpose_policy_shape(successful, manifest, before)
    assert successful.events[0] == "SAVEPOINT"
    assert successful.events[-1] == "ROLLBACK SAVEPOINT"
    assert not any("INSERT INTO" in sql or "UPDATE " in sql for sql in successful.events)
    rejected = Connection(wrong_shape=True)
    with pytest.raises(AuthorityError, match="full shape"):
        apply._preview_predecessor_purpose_policy_shape(rejected, manifest, before)
    assert rejected.events[-1] == "ROLLBACK SAVEPOINT"


def test_disposable_serving_deployment_keeps_control_and_native_identities_separate():
    """Execute producer orchestration; physical SQL is doubled, never PG credit."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    calls = []
    fail_create = [None]
    fail_revoke = [False]
    create_count = [0]
    held = object()
    container = SimpleNamespace(
        get_container_host_ip=lambda: "localhost",
        get_exposed_port=lambda _: 5432,
    )

    class PhysicalConnection:
        def execute(self, statement, params=None):
            calls.append((str(statement), params))
            if str(statement).startswith("CREATE ROLE"):
                create_count[0] += 1
                if create_count[0] == fail_create[0]:
                    raise RuntimeError("Selected CREATE was not acknowledged")
            return SimpleNamespace(scalar_one=lambda: True)

        def exec_driver_sql(self, statement):
            calls.append((statement, None))
            if statement.startswith("REVOKE") and fail_revoke[0]:
                raise RuntimeError("Independent cleanup failure")

    @contextmanager
    def connect():
        yield PhysicalConnection()

    engine = SimpleNamespace(connect=connect, dispose=Mock())

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/butlers"
        assert source_database == "butlers"
        yield held

    with (
        patch.object(migration, "create_migration_db", return_value="normal-owner-url") as create,
        patch.object(
            migration,
            "migration_bootstrap_db_url",
            return_value="postgresql://selected_control@localhost/butlers",
        ),
        patch.object(migration, "create_engine", return_value=engine),
        patch.object(migration, "_upgrade_chain_to_revision") as stage,
        patch("butlers.migrations.migration_session", maintenance),
        patch("butlers.migrations.run_migrations", new_callable=AsyncMock) as upgrade,
        patch("butlers.migrations.finalize_authority") as finalize,
    ):
        with migration.serving_test_deployment(container) as deployment:
            create.assert_called_once_with(container, "butlers", _owned_creation=[])
            assert deployment.migration_url == "normal-owner-url"
            assert stage.call_count > 0
            assert upgrade.await_count > stage.call_count
            assert all(call.kwargs["connection"] is held for call in stage.call_args_list)
            assert all(call.kwargs["connection"] is held for call in upgrade.await_args_list)
            assert all(call.args[0] == "normal-owner-url" for call in upgrade.await_args_list)
            finalize.assert_called_once()
            assert finalize.call_args.args == (held,)
            assert finalize.call_args.kwargs["source_database"] == "butlers"
            runtime = deployment.runtime_database("general")
            assert runtime.user != "selected_control"
            assert runtime.role == "butler_general_rw"
            assert runtime.db_name == "butlers"
            assert deployment.purpose_logins["dashboard"].role == "dashboard_api"
            assert deployment.purpose_logins["credential"].role == "credential_store_writer"
            created = [sql for sql, _ in calls if sql.startswith("CREATE ROLE")]
            assert len(created) == len(deployment.runtime_logins) + 2
            assert all("LOGIN NOINHERIT NOSUPERUSER" in sql for sql in created)
            grants = [sql for sql, _ in calls if sql.startswith("GRANT")]
            assert len(grants) == len(created)
            assert all(" ON " not in sql and " TO " in sql for sql in grants)
        drops = [sql for sql, _ in calls if sql.startswith("DROP ROLE")]
        assert len(drops) == len(created)
        engine.dispose.assert_called()
        # An unacknowledged/ambiguous CREATE never owns a drop, while an
        # earlier acknowledged role still receives independent cleanup.
        for failed_ordinal in (1, 2):
            calls.clear()
            create_count[0] = 0
            fail_create[0] = failed_ordinal
            fail_revoke[0] = failed_ordinal == 2
            with pytest.raises(RuntimeError, match="not acknowledged") as original:
                with migration.serving_test_deployment(container):
                    pytest.fail("Partial serving identity setup was published")
            attempted = [sql for sql, _ in calls if sql.startswith("CREATE ROLE")]
            dropped = [sql for sql, _ in calls if sql.startswith("DROP ROLE")]
            assert len(dropped) == failed_ordinal - 1
            unacknowledged = attempted[-1].split('"')[1]
            assert all(unacknowledged not in sql for sql in dropped)
            if failed_ordinal == 2:
                assert "cleanup was incomplete" in " ".join(original.value.__notes__)


@pytest.mark.parametrize("cancel_phase", ["enter", "resources", "exit"])
@pytest.mark.parametrize("resource_fails", [False, True])
async def test_disposable_serving_deployment_joins_cancelled_worker_and_cleanup(
    cancel_phase, resource_fails
):
    """No cancelled caller abandons a creating or closing testcontainer worker."""
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    loop = asyncio.get_running_loop()
    reached = asyncio.Event()
    release = threading.Event()
    order = []

    async def close_resources():
        order.append("resources_closed")
        if cancel_phase == "resources":
            reached.set()
            await asyncio.to_thread(release.wait, 5)
        if resource_fails:
            raise ValueError("Actual resource close failed")

    resource_closed = AsyncMock(side_effect=close_resources)

    @contextmanager
    def physical_producer(container):
        if cancel_phase == "enter":
            loop.call_soon_threadsafe(reached.set)
            release.wait(5)
        order.append("prepared")
        try:
            yield SimpleNamespace(close_resources=resource_closed)
        finally:
            if cancel_phase == "exit":
                loop.call_soon_threadsafe(reached.set)
                release.wait(5)
            order.append("identities_closed")

    async def consumer():
        async with migration.async_serving_test_deployment(object()):
            order.append("body")

    with patch.object(migration, "serving_test_deployment", physical_producer):
        task = asyncio.create_task(consumer())
        try:
            await asyncio.wait_for(reached.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert "identities_closed" not in order
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            resource_closed.assert_awaited_once()
            assert order.index("resources_closed") < order.index("identities_closed")
            if cancel_phase == "enter":
                assert "body" not in order
            else:
                assert "body" in order
        finally:
            release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)


async def test_disposable_serving_deployment_preserves_body_and_resource_failure_identity():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    for body_fails in (False, True):
        resource_error = LookupError("Actual resource cleanup error")
        body_error = ValueError("Actual body error")
        identities_closed = []

        @contextmanager
        def physical_producer(
            container, resource_error=resource_error, identities_closed=identities_closed
        ):
            try:
                yield SimpleNamespace(close_resources=AsyncMock(side_effect=resource_error))
            finally:
                identities_closed.append(True)

        with patch.object(migration, "serving_test_deployment", physical_producer):
            with pytest.raises(ValueError if body_fails else LookupError) as observed:
                async with migration.async_serving_test_deployment(object()):
                    if body_fails:
                        raise body_error
            assert observed.value is (body_error if body_fails else resource_error)
            assert identities_closed == [True]


def test_explicit_disposable_migration_plans_keep_one_held_session_and_normal_subject():
    """Execute the real test producer; physical driver boundaries alone are doubled."""
    from contextlib import contextmanager

    from butlers.testing import migration

    held = object()
    container = object()
    normal = "postgresql://migration_selected@localhost/selected"
    plans = (
        ("core", "general", None),
        ("memory", "general_memory", None),
        ("core", "switchboard", None),
        ("switchboard", "switchboard", "sw_021"),
        ("switchboard", "switchboard", "sw_021"),
    )
    lifetime = []

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/selected"
        assert source_database == "butlers"
        lifetime.append("enter")
        try:
            yield held
        finally:
            lifetime.append("exit")

    with (
        patch.object(
            migration,
            "migration_bootstrap_db_url",
            return_value="postgresql://selected_control@localhost/selected",
        ) as selected,
        patch("butlers.migrations.migration_session", maintenance),
        patch("butlers.migrations.run_migrations", new_callable=AsyncMock) as upgrade,
        patch.object(migration, "_upgrade_chain_to_revision") as bounded,
        patch("butlers.migrations.finalize_authority") as finalize,
    ):
        assert migration.migrate_test_database(container, normal, plans=plans) == normal
        selected.assert_called_once_with(container, "selected")
        assert upgrade.await_args_list == [
            call(normal, chain="core", schema="general", connection=held),
            call(normal, chain="memory", schema="general_memory", connection=held),
            call(normal, chain="core", schema="switchboard", connection=held),
        ]
        assert bounded.call_args_list == [
            call(
                normal,
                chain="switchboard",
                schema="switchboard",
                revision="sw_021",
                connection=held,
            ),
            call(
                normal,
                chain="switchboard",
                schema="switchboard",
                revision="sw_021",
                connection=held,
            ),
        ]
        finalize.assert_called_once_with(held, source_database="butlers", plans=plans)
        assert lifetime == ["enter", "exit"]
        finalize.reset_mock()
        lifetime.clear()
        failure = RuntimeError("Bounded revision failed")
        bounded.side_effect = failure
        with pytest.raises(RuntimeError) as actual:
            migration.migrate_test_database(container, normal, plans=plans)
        assert actual.value is failure
        finalize.assert_not_called()
        assert lifetime == ["enter", "exit"]


@pytest.mark.parametrize("credential_tail_preexists", [False, True])
async def test_disposable_deployment_joins_late_credential_tail_before_dependent_close(
    credential_tail_preexists,
):
    """Compose actual lifecycle, diagnostic ownership and disposable cleanup without SQL."""
    import asyncio
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.db_authority.catalog import observe_catalog_owned
    from butlers.lifecycle import _shutdown_resources
    from butlers.testing import migration

    release = asyncio.Event()
    entered = asyncio.Event()
    settle_owner = asyncio.Event()
    main_closed = asyncio.Event()
    order = []

    async def physical_diagnostic():
        entered.set()
        await release.wait()
        order.append("credential_released")

    async def credential_close():
        order.append("credential_closed")

    async def main_close():
        if not main_closed.is_set():
            order.append("main_closed")
            main_closed.set()

    pool = SimpleNamespace(close=AsyncMock(side_effect=credential_close))
    main = SimpleNamespace(close=AsyncMock(side_effect=main_close))
    daemon = SimpleNamespace(
        config=SimpleNamespace(shutdown_timeout_s=0),
        _chronicler_refresh_service=SimpleNamespace(
            closed=False,
            close=AsyncMock(return_value=False),
            settle=settle_owner.wait,
        ),
        _server=None,
        _server_task=None,
        _mcp_socket=None,
        _buffer=None,
        _route_inbox_tasks=set(),
        spawner=None,
        _switchboard_heartbeat_task=None,
        _disconnect_switchboard=AsyncMock(),
        _scheduler_loop_task=None,
        _route_inbox_recovery_task=None,
        _liveness_reporter_task=None,
        _active_modules=[],
        _modules=[],
        blob_store=None,
        _db_log_handler=None,
        _audit_db=None,
        _credential_purpose_pool=pool,
        db=main,
    )

    async def shutdown():
        await _shutdown_resources(daemon)

    daemon.shutdown = shutdown
    deployment = migration.ServingTestDeployment("localhost", 5432, "normal", {}, {})
    deployment.retain(main)
    deployment.retain(daemon, "shutdown")

    @contextmanager
    def physical_producer(container):
        try:
            yield deployment
        finally:
            order.append("identities_closed")

    diagnostic = asyncio.create_task(observe_catalog_owned(pool, physical_diagnostic))
    await entered.wait()
    diagnostic.cancel()
    with pytest.raises(asyncio.CancelledError):
        await diagnostic
    if credential_tail_preexists:
        with pytest.raises(TimeoutError):
            await _shutdown_resources(daemon)
        settle_owner.set()
        for _ in range(100):
            if hasattr(daemon, "_credential_dependents_close_task"):
                break
            await asyncio.sleep(0.01)
        assert hasattr(daemon, "_credential_dependents_close_task")
    body_done = asyncio.Event()

    async def consumer():
        async with migration.async_serving_test_deployment(object()):
            body_done.set()

    with patch.object(migration, "serving_test_deployment", physical_producer):
        caller = asyncio.create_task(consumer())
        try:
            await asyncio.wait_for(body_done.wait(), 1)
            for _ in range(100):
                if hasattr(daemon, "_chronicler_cleanup_task"):
                    break
                await asyncio.sleep(0.01)
            assert hasattr(daemon, "_chronicler_cleanup_task")
            if not credential_tail_preexists:
                assert not hasattr(daemon, "_credential_dependents_close_task")
            caller.cancel()
            await asyncio.sleep(0)
            settle_owner.set()
            first = daemon._chronicler_cleanup_task
            with pytest.raises(TimeoutError):
                # Exercise the actual independent five-second lifecycle bound.
                await asyncio.wait_for(asyncio.shield(first), 6)
            caller.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not caller.done()
            assert not daemon._credential_dependents_close_task.done()
            assert not main_closed.is_set()
            pool.close.assert_not_awaited()
            assert "identities_closed" not in order
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(caller, 1)
            assert order == [
                "credential_released",
                "credential_closed",
                "main_closed",
                "identities_closed",
            ]
            assert daemon._credential_dependents_close_task.done()
            assert daemon._credential_purpose_pool is None
        finally:
            settle_owner.set()
            release.set()
            tasks = [
                caller,
                *[
                    vars(daemon)[n]
                    for n in ("_chronicler_cleanup_task", "_credential_dependents_close_task")
                    if n in vars(daemon)
                ],
            ]
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_disposable_held_transition_settles_real_worker_before_caller_cancellation(
    monkeypatch,
):
    """The actual async adapter retains a running selected migration through cancellation."""
    import threading
    from types import SimpleNamespace

    from butlers.testing import migration

    loop = asyncio.get_running_loop()
    started, release, exited = asyncio.Event(), threading.Event(), threading.Event()
    config = SimpleNamespace()
    failure = LookupError("selected exact transition failed")

    def transition(container, operation, selected_config, revision):
        assert container is marker and operation is actual_operation
        assert selected_config is config and revision == "core@core_255"
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5)
            raise failure
        finally:
            exited.set()

    marker, actual_operation = object(), object()
    monkeypatch.setattr(migration, "run_test_alembic_command", transition)
    task = asyncio.create_task(
        migration.async_run_test_alembic_command(marker, actual_operation, config, "core@core_255")
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not exited.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()

    monkeypatch.setattr(migration, "run_test_alembic_command", Mock(side_effect=failure))
    with pytest.raises(LookupError) as actual:
        await migration.async_run_test_alembic_command(
            marker, actual_operation, config, "core@core_255"
        )
    assert actual.value is failure


def test_historical_disposable_command_keeps_exact_graph_normal_role_and_owned_session(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.db_authority import interfaces
    from butlers.testing import migration

    events = []
    connection = SimpleNamespace(commit=Mock(), rollback=Mock())
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://migration_subject@localhost/test_exact_history"
    marker = LookupError("historical SQL denial")

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/test_exact_history"
        assert source_database == "butlers"
        events.append("maintenance_enter")
        try:
            yield connection
        finally:
            events.append("maintenance_exit")

    @contextmanager
    def ordinary(selected):
        assert selected is connection
        events.append("ordinary_enter")
        try:
            yield
        finally:
            events.append("ordinary_exit")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "ordinary_migration_identity", ordinary)
    monkeypatch.setattr(interfaces, "stage_dashboard_interfaces", lambda c: events.append("stage"))
    config = migrations._build_alembic_config(normal, ["core"], target_schema="health")

    def upgrade(selected, target):
        assert selected is config and selected.attributes["connection"] is connection
        assert selected.get_main_option("sqlalchemy.url") == normal
        assert selected.get_main_option("butlers.target_schema") == "health"
        assert target in {"core@core_255", "core@core_280"}
        assert events[-1] == "ordinary_enter"
        events.append(target)
        if target == "core@core_255":
            raise marker
        return "actual command result"

    with pytest.raises(LookupError) as failure:
        migration.run_test_alembic_command(container, upgrade, config, "core@core_255")
    assert failure.value is marker
    assert events == [
        "maintenance_enter",
        "ordinary_enter",
        "core@core_255",
        "ordinary_exit",
        "maintenance_exit",
    ]
    assert "connection" not in config.attributes
    events.clear()
    assert (
        migration.run_test_alembic_command(container, upgrade, config, "core@core_280")
        == "actual command result"
    )
    assert events == [
        "maintenance_enter",
        "stage",
        "ordinary_enter",
        "core@core_280",
        "ordinary_exit",
        "maintenance_exit",
    ]
    assert "connection" not in config.attributes
    config.attributes["connection"] = connection
    events.clear()
    with pytest.raises(RuntimeError, match="already owns a connection"):
        migration.run_test_alembic_command(container, upgrade, config, "core@core_255")
    assert not events and config.attributes["connection"] is connection


def test_disposable_partial_transition_finalizes_complete_prior_plan_without_replaying_seeds(
    monkeypatch,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    connection = object()
    events = []
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/selected_history"
    prior = (("core", "relationship", None), ("relationship", "relationship", None))
    transitions = (("core", "health", None), ("memory", "health", "memory_020"))
    complete = prior + transitions + (("memory", "health", "memory_020"),)

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/selected_history"
        assert source_database == "butlers"
        events.append("enter")
        try:
            yield connection
        finally:
            events.append("exit")

    async def run(url, *, chain, schema, connection):
        assert url == normal and connection is held
        events.append((chain, schema, None))

    def bounded(url, *, chain, schema, revision, connection):
        assert url == normal and connection is held
        events.append((chain, schema, revision))

    def finalize(selected, *, source_database, plans):
        assert selected is held and source_database == "butlers"
        assert plans == complete
        assert events == ["enter", *transitions]
        events.append("finalize")

    held = connection
    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "run_migrations", run)
    monkeypatch.setattr(migrations, "finalize_authority", finalize)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", bounded)
    assert (
        migration.migrate_test_database(container, normal, plans=transitions, final_plans=complete)
        == normal
    )
    assert events == ["enter", *transitions, "finalize", "exit"]


def test_historical_window_uses_real_pre_cutover_graph_without_installing_current_authority(
    monkeypatch,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    events = []
    held = object()
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/history_window"

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/history_window"
        assert source_database == "butlers"
        events.append("enter")
        try:
            yield held
        finally:
            events.append("exit")

    def bounded(url, *, chain, schema, revision, connection):
        assert url == normal and connection is held
        events.append((chain, schema, revision))

    async def run(url, *, chain, schema, connection):
        assert url == normal and connection is held
        events.append((chain, schema, None))

    def forbidden_finalizer(*args, **kwargs):
        pytest.fail("Historical window must not install current authority before its observations")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "run_migrations", run)
    monkeypatch.setattr(migrations, "finalize_authority", forbidden_finalizer)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", bounded)
    plans = (("core", "general", "core_280"), ("relationship", "relationship", None))
    assert migration.stage_test_migration_history(container, normal, plans=plans) == normal
    assert events == ["enter", *plans, "exit"]
    events.clear()
    for revision in (None, "core_281"):
        with pytest.raises(ValueError, match="explicit pre-core_281"):
            migration.stage_test_migration_history(
                container, normal, plans=(("core", "general", revision),)
            )
        assert events == []
    with pytest.raises(RuntimeError, match="not source-owned"):
        migration.stage_test_migration_history(
            container, normal, plans=(("core", "general", "invented_revision"),)
        )
    assert events == []


async def test_serving_deployment_reentry_drops_only_acknowledged_database_after_owned_resources(
    monkeypatch,
):
    """Execute actual producer/wrapper twice; only network/SQL drivers are doubled."""
    import asyncio
    import re
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    events, roles, databases = [], {}, {}
    serial = [100]
    ambiguous_database = [False]

    def next_oid():
        serial[0] += 1
        return serial[0]

    class Result:
        def __init__(self, row):
            self.row = row

        def scalar_one(self):
            return self.row[0]

        def one(self):
            assert self.row is not None
            return self.row

        def one_or_none(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            sql = str(statement).strip()
            if sql.startswith("SELECT oid FROM pg_catalog.pg_roles"):
                return Result(
                    None
                    if params.get("role", params.get("name")) not in roles
                    else (roles[params.get("role", params.get("name"))],)
                )
            if sql.startswith("SELECT oid,datdba FROM pg_catalog.pg_database"):
                return Result(databases.get(params.get("database", params.get("name"))))
            if sql.startswith("CREATE DATABASE"):
                name, owner = re.findall(r'"([^\"]+)"', sql)
                if name in databases:
                    raise RuntimeError("Database already exists")
                databases[name] = (next_oid(), roles[owner])
                events.append("database_created")
                if ambiguous_database[0]:
                    raise RuntimeError("CREATE acknowledgement unavailable")
            elif sql.startswith("CREATE ROLE"):
                name = re.findall(r'"([^\"]+)"', sql)[0]
                assert name not in roles
                roles[name] = next_oid()
            elif sql.startswith("SELECT NOT rolcanlogin"):
                return Result((True,))
            else:
                pytest.fail("Unexpected disposable driver statement")
            return Result((True,))

        def exec_driver_sql(self, sql):
            if sql.startswith("DROP DATABASE"):
                assert events[-1] == "resources_closed"
                name = re.findall(r'"([^\"]+)"', sql)[0]
                del databases[name]
                events.append("database_dropped")
            elif sql.startswith("DROP ROLE"):
                name = re.findall(r'"([^\"]+)"', sql)[0]
                if any(owner == roles[name] for _, owner in databases.values()):
                    raise RuntimeError("Role still owns a database")
                del roles[name]
            elif not sql.startswith(("GRANT ", "REVOKE ")):
                pytest.fail("Unexpected disposable driver command")

    @contextmanager
    def connect():
        yield Connection()

    engine = SimpleNamespace(connect=connect, dispose=lambda: None)
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://selected_control@localhost/postgres",
        get_container_host_ip=lambda: "localhost",
        get_exposed_port=lambda _: 5432,
    )

    def create_role(url, role, password):
        assert url == container.get_connection_url() and password
        if role in roles:
            raise RuntimeError("Role already exists")
        roles[role] = next_oid()

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/butlers"
        assert source_database == "butlers"
        yield object()

    class Resource:
        async def close(self):
            events.append("resources_closed")

    monkeypatch.setattr(migration, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(migration, "_create_test_migration_role", create_role)
    monkeypatch.setattr(migration, "bootstrap_extensions", lambda *_: None)
    monkeypatch.setattr(migration, "_bootstrap_migration_prerequisites", lambda *_: None)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", lambda *a, **k: None)
    monkeypatch.setattr(migrations, "migration_session", maintenance)
    monkeypatch.setattr(migrations, "run_migrations", AsyncMock())
    monkeypatch.setattr(migrations, "finalize_authority", lambda *a, **k: None)
    for _ in range(2):
        async with migration.async_serving_test_deployment(container) as deployment:
            assert "butlers" in databases
            deployment.retain(Resource())
        assert not databases and not roles
    assert events == [
        "database_created",
        "resources_closed",
        "database_dropped",
        "database_created",
        "resources_closed",
        "database_dropped",
    ]
    # Cancellation cannot advance identity teardown while a retained physical
    # resource is still closing, including repeated cancellation of the caller.
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class BlockedResource:
        async def close(self):
            closing.set()
            await release.wait()
            events.append("resources_closed")

    async def cancelled_deployment():
        async with migration.async_serving_test_deployment(container) as deployment:
            deployment.retain(BlockedResource())
            entered.set()
            await asyncio.Future()

    caller = asyncio.create_task(cancelled_deployment())
    await entered.wait()
    caller.cancel()
    await closing.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done() and "butlers" in databases and roles
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not databases and not roles
    assert events[-2:] == ["resources_closed", "database_dropped"]

    # A failed CREATE ROLE never owns an existing role, even with the expected
    # disposable name. An obscured CREATE DATABASE acknowledgement also cannot
    # authorize dropping the database merely because it now exists.
    roles["migration_butlers"] = 700
    with pytest.raises(RuntimeError, match="Role already exists"):
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Preexisting normal role was admitted as ours")
    assert roles == {"migration_butlers": 700} and not databases
    roles.clear()
    ambiguous_database[0] = True
    with pytest.raises(RuntimeError, match="acknowledgement unavailable") as ambiguous:
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Ambiguous database creation was admitted as owned")
    assert "butlers" in databases and "migration_butlers" in roles
    assert any("cleanup was incomplete" in note for note in ambiguous.value.__notes__)
    # Only the IO double's catalog is reset between independent scenarios.
    databases.clear()
    roles.clear()
    ambiguous_database[0] = False
    # A preexisting database is not a creator receipt. Only our newly
    # acknowledged normal role may be removed after CREATE DATABASE refuses.
    databases["butlers"] = (777, 888)
    with pytest.raises(RuntimeError, match="already exists"):
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Preexisting database was admitted as ours")
    assert databases == {"butlers": (777, 888)} and not roles
    # Even an old valid receipt must refuse a replaced foreign catalog identity.
    roles["foreign"] = 901
    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        migration._close_created_test_database(container, [("role", "foreign", 900, None)])
    assert roles == {"foreign": 901}


def test_pinned_historical_environment_preserves_physical_transaction_and_failure_ownership(
    monkeypatch,
    tmp_path,
):
    """Actual pinned online/Alembic/SQLAlchemy transaction protocol; no PostgreSQL IO."""
    import asyncio
    import hashlib
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from butlers import migrations
    from butlers.testing import migration

    source = (
        Path(__file__).resolve().parents[1] / "migrations/data/core_255_env_a090a8ac.py.txt"
    ).read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert digest == "d8a717988914d2c697db58d3e13212ed3ca3d69df6f9e66f8ffdc61487b46a1d"
    engine = create_engine(f"sqlite:///{tmp_path / 'transactions.sqlite'}")
    events, held = [], []
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/history_adapter"
    control = "postgresql://control@localhost/history_adapter"

    @contextmanager
    def session(url, *, source_database):
        assert url == control and source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            connection.exec_driver_sql("SELECT 1")  # actual SQLAlchemy autobegin
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                events.append("lock_exit")
                held.pop()

    @contextmanager
    def ordinary(connection):
        connection.exec_driver_sql("SET ROLE normal")
        try:
            yield connection
        finally:
            connection.exec_driver_sql("RESET ROLE")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "ordinary_migration_identity", ordinary)
    versions = tmp_path / "versions"
    versions.mkdir()
    attempt = 0
    try:
        for schema in (None, "general"):
            for administrative in (False, True):
                for failure in (None, "error", "cancel"):
                    attempt += 1
                    config = Config()
                    config.set_main_option("sqlalchemy.url", control if administrative else normal)
                    config.set_main_option("version_locations", str(versions))
                    config.set_main_option("path_separator", "os")
                    if schema is not None:
                        config.set_main_option("butlers.target_schema", schema)
                    migration.bind_historical_test_environment(
                        config, tmp_path / f"online_{attempt}", source, expected_sha256=digest
                    )
                    primary = (
                        asyncio.CancelledError("historical caller cancelled")
                        if failure == "cancel"
                        else LookupError("actual historical operation failed")
                    )

                    def command(
                        config, revision, *, value=attempt, failure_kind=failure, original=primary
                    ):
                        assert revision == "historical_protocol_control"
                        script = ScriptDirectory.from_config(config)

                        def actual_operation(revision, context):
                            assert context.connection is held[-1]
                            assert not context._in_external_transaction
                            context.connection.exec_driver_sql(
                                "INSERT INTO retained(value) VALUES (?)", (value,)
                            )
                            if failure_kind is not None:
                                raise original
                            return []

                        with EnvironmentContext(config, script, fn=actual_operation):
                            load_python_file(script.dir, "env.py")

                    if failure is not None:
                        with pytest.raises(type(primary)) as error:
                            migration.run_historical_test_alembic_command(
                                container,
                                command,
                                config,
                                "historical_protocol_control",
                                normal_url=normal,
                                administrative=administrative,
                            )
                        assert error.value is primary
                    else:
                        migration.run_historical_test_alembic_command(
                            container,
                            command,
                            config,
                            "historical_protocol_control",
                            normal_url=normal,
                            administrative=administrative,
                        )
                    assert not held and events[-1] == "lock_exit"
                    assert not any(k.startswith("historical.") for k in config.attributes)
                    with engine.connect() as independent:
                        observed = independent.exec_driver_sql(
                            "SELECT count(*) FROM retained WHERE value=?", (attempt,)
                        ).scalar_one()
                    assert observed == (0 if failure is not None else 1)
        assert any(s.startswith("CREATE SCHEMA ") for s in events)
        assert "SET ROLE normal" in events and "RESET ROLE" in events
        before = len(events)
        config.set_main_option("sqlalchemy.url", "postgresql://foreign@localhost/history_adapter")
        with pytest.raises(AssertionError, match="explicit selected subject"):
            migration.run_historical_test_alembic_command(
                container, command, config, "historical_protocol_control", normal_url=normal
            )
        assert len(events) == before
        with pytest.raises(AssertionError, match="Pinned historical environment"):
            migration.bind_historical_test_environment(
                config, tmp_path / "invalid", source + "\n", expected_sha256=digest
            )
    finally:
        engine.dispose()


def test_core255_historical_fixture_binds_pinned_sources_without_redirecting_current_replay(
    monkeypatch,
    tmp_path,
):
    import ast
    import asyncio
    import hashlib
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from butlers.testing import migration

    owner = migration.load_migration(
        Path(__file__).resolve().parents[1]
        / "migrations/test_core_255_insight_premise_binding_migration.py"
    )
    calls, current, created_environments = [], [], []
    normal = "postgresql://normal@localhost/history_source"
    control = "postgresql://control@localhost/history_source"
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )

    class Connection:
        def exec_driver_sql(self, statement, parameters=None):
            calls.append((statement, parameters))

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(
        owner,
        "create_engine",
        lambda *a, **k: SimpleNamespace(connect=connection, dispose=lambda: None),
    )
    monkeypatch.setattr(owner, "_create_current_migration_db", lambda *_: normal)
    monkeypatch.setattr(
        owner, "_prepare_current_migration_prerequisites", lambda *a: current.append(a)
    )
    monkeypatch.setattr(owner, "_run_current_test_alembic_command", lambda *a: current.append(a))

    def historical(container_arg, operation, config, revision, *, normal_url, administrative):
        assert container_arg is container and normal_url == normal
        assert administrative == (config.get_main_option("sqlalchemy.url") == control)
        environment = Path(config.get_main_option("script_location")) / "env.py"
        created_environments.append(environment)
        old, new = ast.parse(owner._PINNED_ENV.read_text()), ast.parse(environment.read_text())

        def online(tree):
            return next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "run_migrations_online"
            )

        assert ast.dump(online(old)) == ast.dump(online(new))
        calls.append(("historical", revision))
        return operation(config, revision)

    monkeypatch.setattr(owner, "run_historical_test_alembic_command", historical)
    pinned = owner._PINNED_INIT.read_text()
    assert hashlib.sha256(pinned.encode()).hexdigest() == owner._PINNED_INIT_DIGEST

    @owner._pinned_historical_producers
    def exercise(primary=None):
        assert owner.create_migration_db(container, "history_source") == normal
        for selected in (normal, control):
            config = owner._build_alembic_config(selected, ["core"])
            assert (
                owner.run_test_alembic_command(container, lambda *_: "done", config, "core_255")
                == "done"
            )
        neutral = tmp_path / "neutral.sql"
        start = pinned.index("-- AMENDMENT_OWNER_CONVERGENCE_BEGIN")
        end = pinned.index("-- AMENDMENT_OWNER_CONVERGENCE_END") + len(
            "-- AMENDMENT_OWNER_CONVERGENCE_END"
        )
        neutral.write_text(pinned[:start] + pinned[end:])
        with monkeypatch.context() as scoped:
            scoped.setattr(migration, "_INIT_DB", neutral)
            owner._bootstrap_migration_prerequisites(control, "normal")
        if primary is not None:
            raise primary

    exercise()
    for primary in (LookupError("body failure"), asyncio.CancelledError("body cancelled")):
        with pytest.raises(type(primary)) as raised:
            exercise(primary)
        assert raised.value is primary
        assert owner._HISTORICAL_CONTEXT.get() is None
    assert all(not p.exists() for p in created_environments)
    assert (pinned.replace("\\set ON_ERROR_STOP on\n", "", 1), None) in calls
    assert sum(statement == "historical" for statement, _ in calls) == 6
    assert current == []
    owner._bootstrap_migration_prerequisites(control, "normal")
    owner.run_test_alembic_command(container, lambda *_: None, object(), "core_254")
    assert len(current) == 2
    # The actual canonical negative retains separate current preparation,
    # current stamp and migrate_test_database calls, not the pinned adapter.
    negative = ast.parse(Path(owner.__file__).read_text())
    negative = next(
        n
        for n in negative.body
        if isinstance(n, ast.FunctionDef) and n.name == "_ordinary_bootstrap_owned_core_replay"
    )
    invoked = {
        n.func.id
        for n in ast.walk(negative)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {
        "_prepare_current_migration_prerequisites",
        "_run_current_test_alembic_command",
        "migrate_test_database",
    } <= invoked
    assert "run_test_alembic_command" not in invoked


def test_current_held_alembic_command_commits_public_and_schema_or_rolls_back_primary(
    monkeypatch, tmp_path
):
    """Actual current env + SQLAlchemy/Alembic transaction ownership, without PG IO."""
    import asyncio
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from butlers import bootstrap_prerequisite, migrations
    from butlers.testing import migration

    engine = create_engine(f"sqlite:///{tmp_path / 'current_transactions.sqlite'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")
    events, held = [], []

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        if statement.startswith("SELECT pg_catalog.to_regclass("):
            return "SELECT NULL", ()  # fresh selected version table, before real operation
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/current_protocol"
    control = "postgresql://control@localhost/current_protocol"

    @contextmanager
    def session(url, *, source_database):
        assert url == control and source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                events.append("lock_exit")
                held.pop()

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "maintenance_profile", lambda connection: "normal")
    monkeypatch.setattr(
        bootstrap_prerequisite, "check_bootstrap_connection", lambda connection: None
    )
    attempt = 0
    try:
        for schema in (None, "general"):
            for failure in (None, "error", "cancel"):
                attempt += 1
                config = Config()
                config.set_main_option("sqlalchemy.url", normal)
                config.set_main_option("script_location", str(Path(migrations.ALEMBIC_DIR)))
                config.set_main_option("path_separator", "os")
                if schema is not None:
                    config.set_main_option("butlers.target_schema", schema)
                primary = (
                    asyncio.CancelledError("current operation cancelled")
                    if failure == "cancel"
                    else LookupError("current operation failed")
                )
                observed_context = []

                def command(
                    config,
                    revision,
                    *,
                    observed=observed_context,
                    value=attempt,
                    failure_kind=failure,
                    original=primary,
                ):
                    assert revision == "current_protocol"
                    script = ScriptDirectory.from_config(config)

                    def actual_operation(revision, context):
                        assert context.connection is held[-1]
                        observed.append(context._in_external_transaction)
                        context.connection.exec_driver_sql(
                            "INSERT INTO retained(value) VALUES (?)", (value,)
                        )
                        if failure_kind is not None:
                            raise original
                        return []

                    # PostgreSQL uses transactional DDL. The real SQLite connection
                    # supplies SQLAlchemy transaction behavior; only PG control IO
                    # above is translated, and the actual current env runs unchanged.
                    with EnvironmentContext(
                        config,
                        script,
                        fn=actual_operation,
                        destination_rev="current_protocol",
                        transactional_ddl=True,
                    ):
                        load_python_file(script.dir, "env.py")
                    return "actual current command result"

                if failure is None:
                    result = migration.run_test_alembic_command(
                        container, command, config, "current_protocol"
                    )
                    assert result == "actual current command result"
                else:
                    with pytest.raises(type(primary)) as error:
                        migration.run_test_alembic_command(
                            container, command, config, "current_protocol"
                        )
                    assert error.value is primary
                assert observed_context == [False]
                assert "connection" not in config.attributes
                assert not held and events[-1] == "lock_exit"
                with engine.connect() as independent:
                    count = independent.exec_driver_sql(
                        "SELECT count(*) FROM retained WHERE value=?", (attempt,)
                    ).scalar_one()
                assert count == (1 if failure is None else 0)
        assert sum(s == 'SET ROLE "normal"' for s in events) == 12
        assert sum(s == "RESET ROLE" for s in events) == 12
    finally:
        engine.dispose()


def test_current_environment_preserves_successive_public_and_schema_chain_transactions(
    monkeypatch, tmp_path
):
    """Real current-env transaction boundaries across actual selected helper routes."""
    import asyncio
    from contextlib import contextmanager
    from types import SimpleNamespace

    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from alembic import command
    from butlers import bootstrap_prerequisite, migrations
    from butlers.db_authority import interfaces
    from butlers.testing import migration

    engine = create_engine(f"sqlite:///{tmp_path / 'successive_transactions.sqlite'}")

    @event.listens_for(engine, "connect")
    def schema_transport(dbapi_connection, record):
        dbapi_connection.execute(
            "ATTACH DATABASE ? AS general", (str(tmp_path / "general.sqlite"),)
        )

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")
    held, events, observations = [], [], []

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        if statement.startswith("SELECT pg_catalog.to_regclass("):
            return "SELECT NULL", ()
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/current_successive"

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/current_successive"
        assert source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                held.pop()
                events.append("lock_exit")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "maintenance_profile", lambda connection: "normal")
    monkeypatch.setattr(migrations, "check_bootstrap_connection", lambda connection: None)
    monkeypatch.setattr(
        bootstrap_prerequisite, "check_bootstrap_connection", lambda connection: None
    )
    monkeypatch.setattr(migrations, "_bootstrap_extensions", lambda *args, **kwargs: None)
    monkeypatch.setattr(interfaces, "stage_dashboard_interfaces", lambda connection: None)
    state = {}

    def execute_chain(config, target):
        assert config.attributes["connection"] is held[-1]
        state["targets"].append(target)
        script = ScriptDirectory.from_config(config)
        value = state["base"] + len(state["targets"])

        def actual_operation(revision, context):
            assert context.connection is held[-1]
            observations.append(context._in_external_transaction)
            context.connection.exec_driver_sql("INSERT INTO retained(value) VALUES (?)", (value,))
            if len(state["targets"]) == 2 and state["failure"] is not None:
                raise state["primary"]
            return []

        with EnvironmentContext(
            config, script, fn=actual_operation, destination_rev=target, transactional_ddl=True
        ):
            load_python_file(script.dir, "env.py")
        assert not config.attributes["connection"].closed

    def finalize(connection, *, source_database, plans):
        assert connection is held[-1] and source_database == "butlers"
        assert plans == state["plans"]
        # Read independently BEFORE the real finalizer's concluding commit.
        # It cannot retroactively save the first public chain from the next
        # current env's deliberate preflight rollback.
        with engine.connect() as independent:
            values = (
                independent.exec_driver_sql(
                    "SELECT value FROM retained WHERE value > ? ORDER BY value", (state["base"],)
                )
                .scalars()
                .all()
            )
        assert values == [state["base"] + 1, state["base"] + 2]
        state["finalized"] = True
        connection.commit()

    monkeypatch.setattr(command, "upgrade", execute_chain)
    monkeypatch.setattr(migrations, "finalize_authority", finalize)
    attempt = 0
    try:
        for route in ("run_migrations", "migrate_test_database", "stage_test_migration_history"):
            for schema in (None, "general"):
                for failure in (None, "error", "cancel"):
                    attempt += 1
                    plans = (("core", schema, "core_114"), ("switchboard", schema, None))
                    primary = (
                        asyncio.CancelledError("second current chain cancelled")
                        if failure == "cancel"
                        else LookupError("second current chain failed")
                    )
                    state.clear()
                    state.update(
                        base=attempt * 10,
                        targets=[],
                        plans=plans,
                        failure=failure,
                        primary=primary,
                        finalized=False,
                    )
                    observations.clear()

                    def operation(route=route, schema=schema, plans=plans):
                        if route == "run_migrations":
                            with session(
                                "postgresql://control@localhost/current_successive",
                                source_database="butlers",
                            ) as connection:
                                for chain in ("core", "switchboard"):
                                    asyncio.run(
                                        migrations.run_migrations(
                                            normal,
                                            chain=chain,
                                            schema=schema,
                                            connection=connection,
                                        )
                                    )
                        else:
                            selected = getattr(migration, route)
                            assert selected(container, normal, plans=plans) == normal

                    if failure is None:
                        operation()
                    else:
                        with pytest.raises(type(primary)) as error:
                            operation()
                        assert error.value is primary
                    assert not held and events[-1] == "lock_exit"
                    with engine.connect() as independent:
                        values = (
                            independent.exec_driver_sql(
                                "SELECT value FROM retained WHERE value > ? ORDER BY value",
                                (state["base"],),
                            )
                            .scalars()
                            .all()
                        )
                    assert values == [state["base"] + 1] + (
                        [state["base"] + 2] if failure is None else []
                    )
                    assert observations == [False, False]
                    assert state["finalized"] is (
                        route == "migrate_test_database" and failure is None
                    )
                    assert state["targets"] == (
                        ["core@head", "switchboard@head"]
                        if route == "run_migrations"
                        else ["core@core_114", "switchboard@head"]
                    )
    finally:
        engine.dispose()


def test_actual_core046_uses_physical_tables_without_acl_filtered_destination(monkeypatch):
    """Execute the real migration; its SQL/DDL IO alone is doubled."""
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    producer = runpy.run_path(
        str(Path("alembic/versions/core/core_046_migrate_user_context_to_public.py"))
    )
    upgrade = producer["upgrade"]

    def run_case(shared, public, remaining):
        events = []
        probes = []
        destination = object() if public else None
        state = {"shared": shared, "public": destination}

        def query(statement):
            sql = str(statement)
            if "count(*)" in sql:
                return SimpleNamespace(scalar=lambda: remaining)
            probes.append(sql)
            namespace = "shared" if "= 'shared'" in sql else "public"
            # The protected destination exists but is invisible through the
            # ordinary migrator's ACL-filtered information_schema view.
            if "information_schema.tables" in sql:
                present = shared if namespace == "shared" else False
            else:
                assert "pg_catalog.pg_class" in sql and "pg_catalog.pg_namespace" in sql
                assert "c.relname = 'user_context'" in sql and "c.relkind IN ('r', 'p')" in sql
                present = shared if namespace == "shared" else public
            events.append(("probe", namespace))
            return SimpleNamespace(scalar=lambda: 1 if present else None)

        def ddl(sql):
            events.append(sql)
            if sql.startswith("ALTER TABLE"):
                if state["public"] is not None:
                    raise RuntimeError("physical destination collision")
                state["public"], state["shared"] = object(), False
            elif sql == "DROP TABLE shared.user_context":
                state["shared"] = False
            else:
                assert sql == "DROP SCHEMA IF EXISTS shared"
                assert not state["shared"] and not remaining

        with monkeypatch.context() as patcher:
            patcher.setattr(
                upgrade.__globals__["op"], "get_bind", lambda: SimpleNamespace(execute=query)
            )
            patcher.setattr(upgrade.__globals__["op"], "execute", ddl)
            if remaining:
                with pytest.raises(RuntimeError, match="shared schema still contains"):
                    upgrade()
            else:
                upgrade()
        assert events[:2] == [("probe", "shared"), ("probe", "public")]
        assert len(probes) == 2 and all("pg_catalog.pg_class" in sql for sql in probes)
        assert state["shared"] is False
        if public:
            assert state["public"] is destination
        assert ("ALTER TABLE shared.user_context SET SCHEMA public" in events) is (
            shared and not public
        )
        assert ("DROP TABLE shared.user_context" in events) is (shared and public)
        assert ("DROP SCHEMA IF EXISTS shared" in events) is (not remaining)

    for case in (
        (True, False, 0),
        (True, True, 0),
        (False, True, 0),
        (False, False, 0),
        (True, True, 1),
    ):
        run_case(*case)

    primary = OSError("catalog transport refused")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            upgrade.__globals__["op"],
            "get_bind",
            lambda: SimpleNamespace(execute=Mock(side_effect=primary)),
        )
        execute = Mock()
        patcher.setattr(upgrade.__globals__["op"], "execute", execute)
        with pytest.raises(OSError) as error:
            upgrade()
        assert error.value is primary
        execute.assert_not_called()


def test_actual_outbox_selected_upgrade_stages_history_then_finalizes_complete_head(monkeypatch):
    """Run the existing wrapper and core198 edge, doubling external IO only."""
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(
        str(Path("tests/migrations/test_runtime_attention_outbox_migration.py"))
    )
    selected = namespace["_selected_upgrade"]
    bound = selected.__globals__
    edge = runpy.run_path(str(Path("alembic/versions/core/core_198_runtime_attention_outbox.py")))[
        "upgrade"
    ]
    normal = "postgresql://normal:synthetic@invalid/selected"
    admin = "postgresql://control:synthetic@invalid/selected"
    connection = object()

    def run_case(failure):
        events = []
        plans = {"selected": {"general": None}}
        primary = (
            asyncio.CancelledError()
            if failure == "cancel"
            else RuntimeError("owned operation refused")
        )

        @contextmanager
        def session(url, *, source_database):
            assert url == admin and source_database == "butlers"
            events.append("enter")
            try:
                yield connection
                if failure == "exit":
                    raise primary
            finally:
                events.append("exit")

        def bounded(url, *, chain, schema, revision, connection):
            assert url == normal and chain == "core" and schema == "switchboard"
            assert connection is held
            events.append(revision)
            if failure in {"bounded", "cancel"}:
                raise primary
            if revision == "core_198":
                edge()

        async def head(url, *, chain, schema, connection):
            assert (url, chain, schema, connection) == (normal, "core", "switchboard", held)
            events.append("head")
            if failure == "head":
                raise primary

        def finalize(connection, *, source_database, plans):
            assert connection is held and source_database == "butlers"
            events.append("finalize")
            assert plans == (("core", "general", None), ("core", "switchboard", None))
            if failure == "finalize":
                raise primary

        held = connection
        config = SimpleNamespace(
            get_main_option=lambda key: normal if key == "sqlalchemy.url" else "switchboard"
        )
        with monkeypatch.context() as patcher:
            for key, value in {
                "_NORMAL_URLS": {"selected": normal},
                "_MAINTENANCE_URLS": {"selected": admin},
                "_SELECTED_PLANS": plans,
                "migration_session": session,
                "_upgrade_chain_to_revision": bounded,
                "run_migrations": head,
                "finalize_authority": finalize,
            }.items():
                patcher.setitem(bound, key, value)
            patcher.setattr(edge.__globals__["op"], "get_bind", lambda: held)
            patcher.setitem(
                edge.__globals__,
                "install_revision_group",
                lambda conn, group: events.append(("installer", conn is held, group)),
            )
            if failure in {"bounded", "cancel", "exit"}:
                with pytest.raises(type(primary)) as error:
                    selected(config, "core_197")
                assert error.value is primary
                assert plans == {"selected": {"general": None}}
                assert events[-1] == "exit"
                return
            selected(config, "core_197")
            assert events == ["enter", "core_197", "exit"]
            events.append("seed")
            selected(config, "core_198")
            assert events == [
                "enter",
                "core_197",
                "exit",
                "seed",
                "enter",
                "core_198",
                ("installer", True, "attention"),
                "exit",
            ]
            staged = dict(plans["selected"])
            assert staged == {"general": None, "switchboard": "core_198"}
            if failure is None:
                selected(config, "core@head")
                assert plans["selected"] == {"general": None, "switchboard": None}
            else:
                with pytest.raises(RuntimeError) as error:
                    selected(config, "core@head")
                assert error.value is primary
                assert plans["selected"] == staged
            assert events[-1] == "exit"
            assert events.count("finalize") == (0 if failure == "head" else 1)

    for failure in (None, "bounded", "head", "finalize", "exit", "cancel"):
        run_case(failure)


def test_pinned_predecessor_archive_is_offline_complete_and_refuses_before_pg(
    tmp_path, monkeypatch
):
    """Actual extractor/fixture; no historical Git, network or PostgreSQL execution."""
    import gzip
    import hashlib
    import io
    import runpy
    import socket
    import subprocess
    import tarfile
    from pathlib import Path

    import testcontainers.postgres

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    extract = namespace["_extract_pinned_predecessor"]
    fixture = namespace["rollback_database"].__wrapped__
    bound = extract.__globals__
    archive = (
        Path(__file__).parents[1] / "integration/data/db_authority_predecessor_30b376.tar.gz"
    ).read_bytes()
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive))) as source:
        for member in source:
            if member.isfile():
                expected[member.name] = hashlib.sha256(
                    source.extractfile(member).read()
                ).hexdigest()
    expected["deploy/helm/butlers/files/pg_dump.sh"] = expected["deploy/backup/pg_dump.sh"]
    refused = []

    def no_io(*args, **kwargs):
        refused.append("unexpected IO")
        raise AssertionError("predecessor extraction attempted external IO")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", no_io)
        patch.setattr(socket.socket, "connect", no_io)
        for name in ("ordinary", ".tmp/hidden"):
            destination = tmp_path / name
            destination.mkdir(parents=True)
            extract(destination)
            actual = {
                path.relative_to(destination).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in destination.rglob("*")
                if path.is_file()
            }
            assert actual == expected
            assert not any(path.is_symlink() for path in destination.rglob("*"))
        assert refused == []

    # Exercise the real fixture ordering too: every damaged source refuses
    # before even constructing its disposable PostgreSQL container.
    created = []

    def no_container(*args, **kwargs):
        created.append("constructed")
        raise AssertionError("damaged archive reached PostgreSQL setup")

    with monkeypatch.context() as patch:
        patch.setattr(testcontainers.postgres, "PostgresContainer", no_container)
        patch.setattr(subprocess, "run", no_io)
        patch.setattr(socket.socket, "connect", no_io)
        for index, payload in enumerate(
            (
                None,
                archive[:-1],
                b"X" + archive[1:],
                archive + b"\0",
                gzip.compress(b"wrong predecessor"),
            )
        ):
            root = tmp_path / f"damaged-{index}"
            data = root / "tests/integration/data/db_authority_predecessor_30b376.tar.gz"
            data.parent.mkdir(parents=True)
            if payload is not None:
                data.write_bytes(payload)
            patch.setitem(bound, "_REPOSITORY", root)
            attempt = root / "fixture"
            attempt.mkdir()
            with pytest.raises((FileNotFoundError, RuntimeError)):
                next(fixture(attempt, monkeypatch))
        assert created == []
        assert refused == []


def test_pinned_predecessor_extractor_refuses_unsafe_members_independent_of_destination(
    tmp_path, monkeypatch
):
    """Real extraction guards with source-pin literals rebound to synthetic archives.

    Only the isolated function copy's size/digest fixture literals change. The
    committed consumer's complete released archive pins remain untouched.
    """
    import ast
    import gzip
    import hashlib
    import inspect
    import io
    import runpy
    import tarfile
    from pathlib import Path

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    extract = namespace["_extract_pinned_predecessor"]
    real_source = inspect.getsource(extract)
    source_root = tmp_path / "source"
    data = source_root / "tests/integration/data/db_authority_predecessor_30b376.tar.gz"
    data.parent.mkdir(parents=True)
    real_packed = (
        Path(__file__).parents[1] / "integration/data/db_authority_predecessor_30b376.tar.gz"
    ).read_bytes()
    real_released = gzip.decompress(real_packed)
    # These cases pass actual SHA verification with explicit synthetic pins,
    # then demand the unchanged real path/link/target guard to refuse.
    for case, name, target in (
        ("parent", "../escape", None),
        ("absolute", "/absolute/escape", None),
        ("foreign", "foreign-link", "../outside"),
        ("wrong-helm-link", "deploy/helm/butlers/files/pg_dump.sh", "../../../../outside"),
        (
            "missing-helm-target",
            "deploy/helm/butlers/files/pg_dump.sh",
            "../../../backup/pg_dump.sh",
        ),
        (
            "corrupt-helm-target",
            "deploy/helm/butlers/files/pg_dump.sh",
            "../../../backup/pg_dump.sh",
        ),
    ):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo(name)
            if target is None:
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
            else:
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)
            if case == "corrupt-helm-target":
                member = tarfile.TarInfo("deploy/backup/pg_dump.sh")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
        released = stream.getvalue()
        packed = gzip.compress(released, mtime=0)
        changes = {
            len(real_packed): len(packed),
            len(real_released): len(released),
            hashlib.sha256(real_packed).hexdigest(): hashlib.sha256(packed).hexdigest(),
            hashlib.sha256(real_released).hexdigest(): hashlib.sha256(released).hexdigest(),
        }

        class FixturePins(ast.NodeTransformer):
            def __init__(self, pins):
                self.pins = pins

            def visit_Constant(self, node):
                if node.value in self.pins:
                    return ast.copy_location(ast.Constant(self.pins[node.value]), node)
                return node

        tree = ast.fix_missing_locations(FixturePins(changes).visit(ast.parse(real_source)))
        isolated = dict(extract.__globals__, _REPOSITORY=source_root)
        exec(compile(tree, "<pinned-source-archive-control>", "exec"), isolated)
        data.write_bytes(packed)
        for destination_name in ("ordinary", ".tmp/hidden"):
            destination = tmp_path / case / destination_name
            destination.mkdir(parents=True)
            with pytest.raises((RuntimeError, FileNotFoundError)):
                isolated["_extract_pinned_predecessor"](destination)
            assert not (tmp_path / case / "escape").exists()
            assert not any(path.is_symlink() for path in destination.rglob("*"))
