"""Unit tests for DB SSL configuration parsing and wiring — condensed."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from butlers.db import Database, schema_search_path

pytestmark = pytest.mark.unit


def test_ssl_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL sslmode parsed; POSTGRES_SSLMODE fallback; public schema_search_path correct."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/postgres?sslmode=disable")
    assert Database.from_env("test_db").ssl == "disable"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    assert Database.from_env("test_db").ssl == "verify-full"

    assert schema_search_path("public") == "public"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_ssl_forwarded_to_provision_and_connect_and_retry(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """provision() and connect() forward ssl to asyncpg; search_path set for schema;
    retry on connection_lost."""
    # ssl forwarded to provision
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn
    await Database(db_name="test_db", ssl="disable").provision()
    assert mock_connect.await_args.kwargs["ssl"] == "disable"

    # ssl forwarded to connect
    pool = AsyncMock()
    mock_create_pool.return_value = pool
    out = await Database(db_name="test_db", ssl="require").connect()
    assert out is pool and mock_create_pool.await_args.kwargs["ssl"] == "require"

    # search_path set for schema-scoped topology
    mock_create_pool.reset_mock()
    await Database(db_name="butlers", schema="general", role="butler_general_rw").connect()
    assert mock_create_pool.await_args.kwargs["server_settings"] == {
        "search_path": "general,public"
    }

    # SSL retry on connection_lost: provision
    mock_connect.reset_mock()
    mock_connect.side_effect = [ConnectionError("unexpected connection_lost() call"), conn]
    await Database(db_name="test_db2").provision()
    assert mock_connect.await_count == 2
    assert mock_connect.await_args_list[1].kwargs["ssl"] == "disable"

    # SSL retry on connection_lost: connect
    mock_create_pool.reset_mock()
    pool2 = AsyncMock()
    mock_create_pool.side_effect = [ConnectionError("unexpected connection_lost() call"), pool2]
    out2 = await Database(db_name="test_db3").connect()
    assert out2 is pool2 and mock_create_pool.await_args_list[1].kwargs["ssl"] == "disable"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_verified_setup_callback_registered(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """When role exists, _role_verified=True and setup callback is passed to create_pool."""
    check_conn = AsyncMock()
    check_conn.terminate = Mock()
    check_conn.fetchval = AsyncMock(return_value=True)
    check_conn.close = AsyncMock()
    mock_connect.return_value = check_conn

    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role="butler_general_rw")
    assert db._role_verified is False

    out = await db.connect()
    assert out is pool
    assert db._role_verified is True
    # setup callback must be passed to create_pool
    setup_cb = mock_create_pool.await_args.kwargs["setup"]
    assert callable(setup_cb) and setup_cb.__func__ is Database._setup_connection


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_missing_refuses_pool(mock_connect, mock_create_pool) -> None:
    connection = AsyncMock()
    connection.terminate = Mock()
    connection.fetchval.return_value = False
    mock_connect.return_value = connection
    db = Database(db_name="test_db", role="nonexistent_role")
    with pytest.raises(RuntimeError, match="role establishment failed"):
        await db.connect()
    assert db._role_verified is False
    mock_create_pool.assert_not_awaited()
    connection.terminate.assert_called_once()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_none_no_verification_no_callback(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """When role=None, no check connection is opened and no setup callback passed."""
    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role=None)
    out = await db.connect()
    assert out is pool
    # asyncpg.connect should NOT be called for role verification (no role set)
    mock_connect.assert_not_awaited()
    assert "setup" not in mock_create_pool.await_args.kwargs


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_verification_error_refuses_pool(mock_connect, mock_create_pool) -> None:
    cause = ConnectionError("synthetic-only")
    mock_connect.side_effect = cause
    db = Database(db_name="test_db", role="butler_general_rw")
    with pytest.raises(RuntimeError) as error:
        await db.connect()
    assert error.value.__cause__ is cause
    assert db._role_verified is False
    mock_create_pool.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_verified_setup_callback_in_ssl_retry(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """When role is verified, setup callback is also present in the SSL retry pool kwargs."""
    check_conn = AsyncMock()
    check_conn.terminate = Mock()
    check_conn.fetchval = AsyncMock(return_value=True)
    check_conn.close = AsyncMock()
    mock_connect.return_value = check_conn

    pool2 = AsyncMock()
    mock_create_pool.side_effect = [ConnectionError("unexpected connection_lost() call"), pool2]

    db = Database(db_name="test_db", role="butler_general_rw")
    out = await db.connect()
    assert out is pool2
    assert db._role_verified is True
    # Both the initial and retry create_pool calls should carry setup callback
    for call in mock_create_pool.await_args_list:
        setup_cb = call.kwargs.get("setup")
        assert callable(setup_cb) and setup_cb.__func__ is Database._setup_connection


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_role_verify_connection_retries_with_ssl_disable(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """When check-conn fails with SSL upgrade loss, role verification retries with ssl=disable."""
    check_conn = AsyncMock()
    check_conn.terminate = Mock()
    check_conn.fetchval = AsyncMock(return_value=True)
    check_conn.close = AsyncMock()
    # First connect (ssl=None) raises SSL upgrade loss error; second (ssl=disable) succeeds
    mock_connect.side_effect = [ConnectionError("unexpected connection_lost() call"), check_conn]

    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role="butler_general_rw")
    out = await db.connect()
    assert out is pool
    assert db._role_verified is True
    # Second connect call should use ssl=disable
    assert mock_connect.await_args_list[1].kwargs["ssl"] == "disable"
    # setup callback should be registered since role was verified on retry
    assert callable(mock_create_pool.await_args.kwargs.get("setup"))


async def test_setup_connection_skips_when_role_is_none_despite_verified() -> None:
    """_setup_connection skips when self.role is None (defensive guard), even if _role_verified."""
    conn = AsyncMock()
    conn.execute = AsyncMock()

    db = Database(db_name="test_db", role=None)
    db._role_verified = True  # Administrative schema-less utility

    await db._setup_connection(conn)
    conn.execute.assert_not_awaited()


async def test_setup_connection_runs_set_role() -> None:
    """_setup_connection executes SET ROLE with properly quoted identifier."""
    conn = AsyncMock()
    conn.execute = AsyncMock()

    db = Database(db_name="test_db", role="butler_general_rw")
    db._role_verified = True
    db._verified_role = db.role

    await db._setup_connection(conn)
    conn.execute.assert_awaited_once_with('SET ROLE "butler_general_rw"')


async def test_setup_connection_refuses_when_not_verified() -> None:
    conn = AsyncMock()
    db = Database(db_name="test_db", role="butler_general_rw")
    with pytest.raises(RuntimeError, match="not been established"):
        await db._setup_connection(conn)
    conn.execute.assert_not_awaited()


async def test_setup_connection_quotes_role_with_double_quotes() -> None:
    """_setup_connection escapes embedded double-quotes in role name."""
    conn = AsyncMock()
    conn.execute = AsyncMock()

    db = Database(db_name="test_db", role='role"with"quotes')
    db._role_verified = True
    db._verified_role = db.role

    await db._setup_connection(conn)
    conn.execute.assert_awaited_once_with('SET ROLE "role""with""quotes"')


async def test_verify_role_exists_returns_false_when_role_none() -> None:
    """_verify_role_exists returns False immediately when self.role is None."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock()

    db = Database(db_name="test_db", role=None)
    result = await db._verify_role_exists(conn)
    assert result is False
    conn.fetchval.assert_not_awaited()


async def test_from_env_does_not_set_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env() does not set role; role remains None (caller sets it)."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/postgres")
    db = Database.from_env("test_db")
    assert db.role is None
    assert db._role_verified is False


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_provision_collation_refresh(mock_connect: AsyncMock) -> None:
    """provision() refreshes template1 collation before CREATE DATABASE; continues on failure."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn

    await Database(db_name="test_db").provision()
    calls = [c.args[0] for c in conn.execute.await_args_list]
    assert calls[0] == "ALTER DATABASE template1 REFRESH COLLATION VERSION"
    assert "CREATE DATABASE" in calls[1]

    # Failure in collation refresh does not stop provisioning
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value=1)
    conn2.execute = AsyncMock(side_effect=[RuntimeError("permission denied"), None])
    conn2.close = AsyncMock()
    mock_connect.return_value = conn2
    await Database(db_name="test_db2").provision()  # must not raise


# ---------------------------------------------------------------------------
# Strict role enforcement (hardened posture) tests
# ---------------------------------------------------------------------------


def test_missing_schema_role_has_no_runtime_opt_out() -> None:
    db = Database(db_name="butlers", schema="general")
    with pytest.raises(RuntimeError, match="configured role"):
        db._validate_runtime_role()


def test_runtime_role_must_match_schema() -> None:
    db = Database(db_name="butlers", schema="general", role="butler_health_rw")
    with pytest.raises(RuntimeError, match="match its schema"):
        db._validate_runtime_role()


@pytest.mark.parametrize("role", ["", " "])
def test_empty_runtime_role_refuses(role) -> None:
    db = Database(db_name="butlers", role=role)
    with pytest.raises(RuntimeError, match="configured role"):
        db._validate_runtime_role()


def test_role_configuration_is_deferred_until_connect() -> None:
    db = Database(db_name="butlers")
    db.set_schema("general")
    db.role = "butler_general_rw"
    db._validate_runtime_role()
    assert db.role_enforcement_disabled is True


async def test_mutated_role_is_not_previously_verified() -> None:
    db = Database(db_name="butlers", role="butler_general_rw")
    db._role_verified = True
    db._verified_role = db.role
    db.role = "butler_health_rw"
    conn = AsyncMock()
    with pytest.raises(RuntimeError, match="not been established"):
        await db._setup_connection(conn)
    conn.execute.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_strict_mode_role_missing_raises(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """A missing runtime role raises in every environment."""
    check_conn = AsyncMock()
    check_conn.terminate = Mock()
    check_conn.fetchval = AsyncMock(return_value=False)  # role does not exist
    check_conn.close = AsyncMock()
    mock_connect.return_value = check_conn

    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role="butler_missing_role")
    with pytest.raises(RuntimeError, match="role establishment failed"):
        await db.connect()

    # Pool must NOT be created — we raised before create_pool
    mock_create_pool.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_strict_mode_role_verify_error_raises(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """Unreadable runtime-role verification refuses a usable pool."""
    mock_connect.side_effect = ConnectionError("connection refused")

    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role="butler_general_rw")
    with pytest.raises(RuntimeError, match="role establishment failed"):
        await db.connect()

    mock_create_pool.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_missing_role_refuses_without_posture_setting(mock_connect, mock_create_pool) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = False
    mock_connect.return_value = conn
    db = Database(db_name="test_db", role="butler_missing_role")
    with pytest.raises(RuntimeError):
        await db.connect()
    assert db.pool is None
    assert db._role_verified is False
    mock_create_pool.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_unreadable_role_refuses_without_posture_setting(
    mock_connect, mock_create_pool
) -> None:
    mock_connect.side_effect = ConnectionError("synthetic-only")
    db = Database(db_name="test_db", role="butler_general_rw")
    with pytest.raises(RuntimeError):
        await db.connect()
    assert db.pool is None
    assert db._role_verified is False
    mock_create_pool.assert_not_awaited()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_strict_mode_role_found_proceeds_normally(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """A verified and assumable runtime role registers per-acquire enforcement."""
    check_conn = AsyncMock()
    check_conn.terminate = Mock()
    check_conn.fetchval = AsyncMock(return_value=True)  # role exists
    check_conn.close = AsyncMock()
    mock_connect.return_value = check_conn

    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", role="butler_general_rw")
    out = await db.connect()

    assert out is pool
    assert db._role_verified is True
    assert callable(mock_create_pool.await_args.kwargs.get("setup"))


@pytest.mark.parametrize("failure", ["unconfigured", "missing", "unreadable", "assumption"])
async def test_runtime_role_failure_never_returns_shared_user_pool(failure) -> None:
    """The actual runtime connect consumer must not publish an unscoped pool."""
    role = None if failure == "unconfigured" else "butler_general_rw"
    db = Database(db_name="butlers", schema="general", role=role)
    connection = AsyncMock()
    connection.terminate = Mock()
    connection.fetchval.return_value = failure != "missing"
    cause = ConnectionError("synthetic-private-connection-value")
    if failure == "unreadable":
        connection.fetchval.side_effect = cause
    if failure == "assumption":
        connection.execute.side_effect = cause
    with (
        patch("butlers.db.asyncpg.connect", new=AsyncMock(return_value=connection)),
        patch("butlers.db.asyncpg.create_pool", new=AsyncMock(return_value=AsyncMock())) as create,
    ):
        with pytest.raises(RuntimeError) as error:
            await db.connect()
        assert "synthetic-private-connection-value" not in str(error.value)
        assert db.pool is None
        assert db.role_enforcement_disabled is True
        create.assert_not_called()
        if failure in {"unreadable", "assumption"}:
            assert error.value.__cause__ is cause


async def test_acquire_reapplies_role_and_refuses_revoked_membership() -> None:
    db = Database(db_name="butlers", schema="general", role="butler_general_rw")
    check = AsyncMock()
    check.fetchval.return_value = True
    pool = AsyncMock()
    with (
        patch("butlers.db.asyncpg.connect", new=AsyncMock(return_value=check)),
        patch("butlers.db.asyncpg.create_pool", new=AsyncMock(return_value=pool)) as create,
    ):
        await db.connect()
        setup = create.call_args.kwargs["setup"]
        conn = AsyncMock()
        await setup(conn)
        await setup(conn)  # release/reset does not waive the next acquire
        assert conn.execute.await_count == 2
        cause = PermissionError("synthetic-private-membership-error")
        conn.execute.side_effect = cause
        with pytest.raises(RuntimeError) as error:
            await setup(conn)
        assert error.value.__cause__ is cause
        assert "synthetic-private-membership-error" not in str(error.value)


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_pool_partial_initialization_is_terminated(monkeypatch, cancel, runtime) -> None:
    """Use the actual native Pool initializer, with zero network connections."""
    import asyncio

    import asyncpg

    original = asyncpg.Pool._initialize
    terminated = Mock()
    cause = asyncio.CancelledError() if cancel else ConnectionError("synthetic-pool-failure")
    pools = []

    async def fail_after_owned_holder(pool):
        await original(pool)  # min_size=0: allocate real holders, no connection
        pools.append(pool)
        pool._holders[0]._con = Mock(terminate=terminated)
        raise cause

    monkeypatch.setattr(asyncpg.Pool, "_initialize", fail_after_owned_holder)
    check = AsyncMock()
    check.fetchval.return_value = True
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=check))
    db = Database(
        db_name="synthetic",
        schema="general" if runtime else None,
        role="butler_general_rw" if runtime else None,
        min_pool_size=0,
        max_pool_size=1,
    )
    expected = RuntimeError if runtime and not cancel else type(cause)
    with pytest.raises(expected) as error:
        await db.connect()
    if runtime and not cancel:
        assert error.value.__cause__ is cause
        assert "synthetic-pool-failure" not in str(error.value)
    else:
        assert error.value is cause
    assert type(pools[0]) is asyncpg.Pool
    terminated.assert_called_once()
    assert pools[0]._closed is True
    assert db.pool is None


async def test_role_verification_cancellation_terminates_owned_connection() -> None:
    import asyncio

    db = Database(db_name="butlers", schema="general", role="butler_general_rw")
    conn = AsyncMock()
    conn.terminate = Mock()
    conn.fetchval.side_effect = asyncio.CancelledError()
    with patch("butlers.db.asyncpg.connect", new=AsyncMock(return_value=conn)):
        with pytest.raises(asyncio.CancelledError):
            await db.connect()
    conn.terminate.assert_called_once()
    assert db.pool is None
    assert db._role_verified is False
