"""Executing successors for retired init-source/body-variant diagnostics.

Historical test names remain as population identities. The adopted DB authority
contract supersedes path overrides, nested installer parsing and acceptance of
old function bodies. Each control exercises source compilation/catalog facts;
none executes PostgreSQL or claims SQL/catalog permission proof.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.stored_function_drift import (
    compare_catalog_functions,
    compute_database_authority,
    log_database_authority,
)
from butlers.db_authority.grants import serving_catalog_contract

pytestmark = pytest.mark.unit


def _source():
    return dict(
        schema="public",
        name="fixed",
        signature="uuid",
        owner=None,
        digest="a" * 64,
        executors=[],
        settings=["search_path=pg_catalog, pg_temp"],
    )


def _observed(**changes):
    return (
        dict(
            schema_name="public",
            name="fixed",
            signature="uuid",
            present=True,
            owner_ok=True,
            settings_ok=True,
            acl_ok=True,
            digest="a" * 64,
        )
        | changes
    )


def _driver(*, catalog=True):
    connection = MagicMock()
    connection.fetchval = AsyncMock(return_value=catalog)
    source = serving_catalog_contract("butlers")
    connection.fetch = AsyncMock(
        return_value=[
            dict(
                schema_name=row["schema"],
                name=row["name"],
                signature=row["signature"],
                present=True,
                owner_ok=True,
                settings_ok=True,
                acl_ok=True,
                digest=row["digest"],
            )
            for row in source["functions"]
        ]
    )
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, connection


def test_parses_top_level_and_nested_committed_function_bodies():
    # Explicit successor: current typed source registry, no runtime SQL parser.
    source = serving_catalog_contract("butlers")
    identities = [(row["schema"], row["name"], row["signature"]) for row in source["functions"]]
    assert len(identities) == len(set(identities))
    assert (
        "public",
        "context_dnd_mutate",
        "uuid,text,text,timestamp with time zone,text,real,jsonb",
    ) in identities
    assert all("body" not in row for row in source["functions"])
    assert not any("legacy_debounce" in row["name"] for row in source["functions"])


def test_an_overload_the_committed_source_does_not_describe_is_reported():
    with pytest.raises(ValueError, match="unsupported identity"):
        compare_catalog_functions([_source()], [_observed(signature="text")])
    with pytest.raises(ValueError, match="unsupported identity"):
        compare_catalog_functions([_source()], [_observed(), _observed()])


def test_a_deployed_body_matching_the_second_committed_variant_is_matched():
    # Explicit successor: only the one final body may conform, not old variants.
    current = compare_catalog_functions([_source()], [_observed()])
    old = compare_catalog_functions([_source()], [_observed(digest="b" * 64)])
    assert current[0].status == "conforming"
    assert old[0].status == "mismatch"
    assert old[0].actual_digest == "b" * 64


def test_a_committed_function_absent_from_the_catalog_is_named():
    facts = compare_catalog_functions(
        [_source()],
        [_observed(present=False, digest=None, owner_ok=False, settings_ok=False, acl_ok=False)],
    )
    assert facts[0].status == "missing"
    assert facts[0].function == "public.fixed(uuid)"
    with pytest.raises(ValueError, match="incomplete"):
        compare_catalog_functions([_source()], [])


async def test_an_unreadable_committed_source_degrades_rather_than_reporting_all_clear():
    pool, connection = _driver()
    connection.fetchval.side_effect = RuntimeError("synthetic-private-driver-payload")
    report = await compute_database_authority(pool)
    assert not report.is_available
    assert report.status == "unavailable" and report.objects_ok is None
    assert report.functions == ()
    assert report.check_error == "RuntimeError"


async def test_configured_committed_source_path_is_used(monkeypatch, tmp_path):
    # Explicit successor: an environment path cannot override source authority.
    monkeypatch.setenv("STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH", str(tmp_path / "not-a-source"))
    pool, connection = _driver()
    report = await compute_database_authority(pool)
    assert report.is_available and report.status == "conforming"
    assert report.objects_ok is True
    connection.fetch.assert_awaited_once()
    assert all(row.owner_ok and row.settings_ok and row.acl_ok for row in report.functions)


async def test_missing_configured_committed_source_degrades_with_a_reason():
    pool, connection = _driver()
    connection.fetch.return_value = []
    report = await compute_database_authority(pool)
    assert not report.is_available
    assert report.functions == () and report.check_error == "ValueError"


async def test_invalid_utf8_committed_source_degrades_rather_than_raising():
    # Catalog transport/type failure cannot produce a green diagnostic.
    pool, connection = _driver()
    connection.fetch.side_effect = UnicodeError("synthetic-private-driver-payload")
    report = await compute_database_authority(pool)
    assert not report.is_available
    assert report.status == "unavailable" and report.functions == ()
    assert report.check_error == "UnicodeError"


async def test_configured_source_is_named_in_drift_remediation_log(caplog):
    pool, _ = _driver(catalog=False)
    report = await compute_database_authority(pool)
    assert report.is_available and report.status == "mismatch"
    log_database_authority(report)
    assert "controlled migrator" in caplog.text
    assert "init-db.sql" not in caplog.text


async def test_configured_source_is_named_in_unavailable_log(caplog):
    pool, connection = _driver()
    connection.fetch.side_effect = RuntimeError("synthetic-private-driver-payload")
    report = await compute_database_authority(pool)
    log_database_authority(report)
    assert "unavailable" in caplog.text
    assert "synthetic-private-driver-payload" not in caplog.text
    assert "RuntimeError" not in caplog.text


@pytest.mark.parametrize("field", ["owner_ok", "settings_ok", "acl_ok", "present"])
def test_actual_catalog_strict_fields_cannot_be_integer_or_unknown(field):
    for value in (1, None, "true"):
        with pytest.raises(ValueError, match="untyped"):
            compare_catalog_functions([_source()], [_observed(**{field: value})])
    if field != "present":
        assert (
            compare_catalog_functions([_source()], [_observed(**{field: False})])[0].status
            == "mismatch"
        )


async def test_diagnostic_release_is_owned_across_cancel_and_actual_database_close():
    """Software release resistance is ownership proof, not a deployed leak claim."""
    import asyncio
    from contextlib import asynccontextmanager

    from butlers.db import Database

    pool, conn = _driver()
    releasing = asyncio.Event()
    settled = asyncio.Event()
    closed = []

    @asynccontextmanager
    async def acquire(**_options):
        try:
            yield conn
        finally:
            releasing.set()
            await settled.wait()

    pool.acquire.side_effect = acquire
    pool.close = AsyncMock(side_effect=lambda: closed.append(True))
    diagnostic = asyncio.create_task(compute_database_authority(pool))
    await releasing.wait()
    diagnostic.cancel()
    with pytest.raises(asyncio.CancelledError):
        await diagnostic
    db = Database("butlers")
    db.pool = pool
    close = asyncio.create_task(db.close())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert closed == []
    assert db.pool is pool
    # Repeated caller cancellation must not cancel the retained physical release.
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert closed == []
    settled.set()
    await db.close()
    assert closed == [True]
    assert db.pool is None


@pytest.mark.parametrize("cancel_diagnostic", [False, True])
async def test_native_diagnostic_default_release_is_joined_before_manager_close(
    monkeypatch, cancel_diagnostic
):
    """Actual Pool/Connection/reset/close, with an isolated protocol, no socket.

    Blocking protocol IO models release still in flight. It does not establish
    a deployed leak, nor substitute for genuine PostgreSQL/catalog execution.
    """
    import asyncio
    from types import SimpleNamespace

    import asyncpg
    from asyncpg.connect_utils import _ClientConfiguration

    from butlers.api.db import DatabaseManager
    from butlers.db_authority.catalog import _DIAGNOSTIC_OWNERS

    _, facts = _driver()
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = []
    live = True

    async def query(statement, timeout):  # noqa: ASYNC109 - native protocol signature
        assert "RESET ALL" in statement
        entered.set()
        await release.wait()
        return "RESET"

    async def close(timeout):  # noqa: ASYNC109 - native protocol signature
        nonlocal live
        closed.append(True)
        live = False

    protocol = MagicMock()
    protocol.get_settings.return_value = SimpleNamespace(server_version="16.0")
    protocol.is_connected.side_effect = lambda: live
    protocol.is_in_transaction.return_value = False
    protocol._is_cancelling.return_value = False
    protocol.queries_count = 0
    protocol.query = query
    protocol.close = close
    connection = asyncpg.Connection(
        protocol,
        MagicMock(),
        asyncio.get_running_loop(),
        ("synthetic", 0),
        _ClientConfiguration(None, 0, 0, 0),
        None,
    )
    monkeypatch.setattr(asyncpg.Connection, "fetchval", facts.fetchval)
    monkeypatch.setattr(asyncpg.Connection, "fetch", facts.fetch)
    pool = await asyncpg.create_pool(min_size=0, max_size=1, max_inactive_connection_lifetime=0)
    holder = pool._holders[0]
    holder._con = connection
    holder._generation = pool._generation
    manager = DatabaseManager()
    manager._pools["general"] = pool
    healthy_closed = asyncio.Event()
    healthy_pool = MagicMock()
    healthy_pool.close = AsyncMock(side_effect=healthy_closed.set)
    manager._pools["health"] = healthy_pool
    diagnostic = asyncio.create_task(compute_database_authority(pool))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert type(pool) is asyncpg.Pool and type(connection) is asyncpg.Connection
        assert pool._reset is None  # Native default Connection.reset, not custom reset.
        assert not diagnostic.done()  # No reference/success before full release.
        if cancel_diagnostic:
            diagnostic.cancel()
            with pytest.raises(asyncio.CancelledError):
                await diagnostic
            assert id(pool) in _DIAGNOSTIC_OWNERS
            refused = await compute_database_authority(pool)
            assert refused.status == "unavailable"
        healthy, _ = _driver()
        assert (await compute_database_authority(healthy)).status == "conforming"
        for _ in range(2):
            closing = asyncio.create_task(manager.close())
            await asyncio.wait_for(healthy_closed.wait(), 1)
            await asyncio.sleep(0)
            assert closed == [] and not pool._closing
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert manager.pool("general") is pool
        release.set()
        await manager.close()
        if not cancel_diagnostic:
            assert (await diagnostic).status == "conforming"
        healthy_pool.close.assert_awaited_once()
        assert closed == [True] and pool._closed
        assert connection.is_closed() and holder._in_use is None
        assert id(pool) not in _DIAGNOSTIC_OWNERS
        assert manager._pools == {}
        protocol.abort.assert_not_called()
    finally:
        release.set()
        await manager.close()
        if not pool._closed:
            await pool.close()


async def test_diagnostic_original_deadline_retains_query_release_and_late_close():
    import asyncio
    from contextlib import asynccontextmanager

    from butlers.db import Database
    from butlers.db_authority.catalog import _DIAGNOSTIC_OWNERS

    pool, conn = _driver()
    release = asyncio.Event()
    entered_release = asyncio.Event()
    query_cancelled = asyncio.Event()

    async def query(*args):
        try:
            await asyncio.Event().wait()
        finally:
            query_cancelled.set()

    @asynccontextmanager
    async def acquire(**options):
        assert options == {"timeout": 5}
        try:
            yield conn
        finally:
            entered_release.set()
            await release.wait()

    pool.acquire.side_effect = acquire
    pool.close = AsyncMock()
    conn.fetchval.side_effect = query
    db = Database("butlers")
    db.pool = pool
    started = asyncio.get_running_loop().time()
    try:
        report = await compute_database_authority(pool)
        elapsed = asyncio.get_running_loop().time() - started
        assert report.status == "unavailable" and report.check_error == "TimeoutError"
        assert 4.8 <= elapsed < 6.5  # Original five-second caller, not a widened budget.
        await asyncio.wait_for(query_cancelled.wait(), 1)
        await asyncio.wait_for(entered_release.wait(), 1)
        assert id(pool) in _DIAGNOSTIC_OWNERS
        pool.close.assert_not_awaited()
    finally:
        release.set()
        await db.close()
    pool.close.assert_awaited_once()
    assert db.pool is None and id(pool) not in _DIAGNOSTIC_OWNERS
