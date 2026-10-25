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
    connection.fetchval = AsyncMock(return_value="butlers")
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


async def test_actual_credential_factory_refuses_an_unproved_writer(driver):
    from butlers.credential_store import create_system_global_credential_store_from_env

    connection, pools, _ = driver
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
            original.role, original.primary_schema, original.private_schemas, original.modules
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
    actual = dict(schema="general", name="state", kind="r", owner="migrator",
                  rls=False, forced_rls=False, oid=1)
    assert validate_relations([actual], (expected,)) == ((actual, expected),)
    with pytest.raises(AuthorityError):
        validate_relations([], (expected,))
    for change in (dict(name="undeclared"), dict(kind="f"), dict(owner="runtime")):
        with pytest.raises(AuthorityError):
            validate_relations([actual | change], (expected,))
    protected = ObjectAuthority("general", "state", ObjectKind.TABLE, "migrator", (),
                                "core", forced_rls=True)
    with pytest.raises(AuthorityError):
        validate_relations([actual | dict(rls=True)], (protected,))
    assert validate_relations([actual | dict(rls=True, forced_rls=True)], (protected,))
