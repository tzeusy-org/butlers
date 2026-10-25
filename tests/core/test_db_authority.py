"""Executing serving admission controls; driver boundaries only are doubled."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, Mock, patch

import pytest

from butlers.db import Database
from butlers.db_authority import roles
from butlers.db_authority.roles import AuthorityError, load_runtime_profile

pytestmark = pytest.mark.unit


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
