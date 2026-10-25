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
    assert len([q for q in connection.calls if "WITH RECURSIVE" in q]) == 7
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
