"""The HTTP fixture uses migrated clones and production authentication (bu-rvgzmf.3)."""

from __future__ import annotations

import asyncio
import threading

import asyncpg
import pytest

from butlers.api import deps
from butlers.api.db import DatabaseManager
from tests.api.http_fixtures import ApiButler, ApiDatabaseSpec, _connect, api_database, leased_api

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_api_reads_committed_jsonb_through_the_real_manager(api, api_db):
    payload = {"nested": [True, None, {"number": 7}]}
    await api_db.pool.execute("INSERT INTO state(key, value) VALUES($1, $2)", "http-seam", payload)
    response = await api.get("/api/butlers/switchboard/state/http-seam")
    assert response.status_code == 200
    assert response.json()["data"]["value"] == payload
    assert isinstance(deps.get_db_manager(), DatabaseManager)
    assert deps.get_db() is api_db.manager
    assert deps.get_db_optional() is api_db.manager
    assert api_db.manager.role_enforcement_disabled is True
    assert await api_db.pool.fetchval("SELECT current_schema()") == "switchboard"
    shared = api_db.manager.credential_shared_pool()
    assert shared is not api_db.pool
    assert await shared.fetchval("SELECT current_schema()") == "public"
    assert await shared.fetchval("SELECT current_database()") == api_db.name
    assert api_db.manager.memory_schema_for_butler("switchboard") == "switchboard"
    assert await api_db.pool.fetchval(
        "SELECT NOT rolsuper FROM pg_roles WHERE rolname=current_user"
    )
    # A discovered roster router consumes the same public dependency identity.
    roster = await api.get("/api/switchboard/ingestion-rules")
    assert roster.status_code == 200
    assert isinstance(roster.json()["data"], list)


async def test_api_rejects_missing_and_wrong_authority_before_a_real_positive_read(api, api_db):
    await api_db.pool.execute(
        "INSERT INTO state(key,value) VALUES('auth-control', $1)", {"ok": True}
    )
    path = "/api/butlers/switchboard/state/auth-control"
    assert (await api.get(path)).json()["data"]["value"] == {"ok": True}
    api.headers.pop("X-API-Key")
    missing = await api.get(path)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"
    wrong = await api.get(path, headers={"X-API-Key": "wrong-synthetic-key"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "UNAUTHORIZED"


async def test_api_auth_login_cannot_reset_to_host_or_read_domain_tables(api, api_db):
    host_role = await api_db.pool.fetchval("SELECT rolname FROM pg_roles WHERE rolsuper LIMIT 1")
    async with api_db.auth.pool.acquire() as connection:
        await connection.execute("RESET ROLE")
        assert await connection.fetchval("SELECT session_user = current_user")
        for statement in (
            "SELECT dashboard_auth.host('reconcile_mode','{}')",
            "SELECT * FROM public.entities",
            'SET ROLE "' + host_role.replace('"', '""') + '"',
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)
    assert (await api.get("/api/butlers/switchboard/state")).status_code == 200


async def test_api_clone_starts_empty_and_does_not_share_rows(api, api_db, db):
    response = await api.get("/api/butlers/switchboard/state")
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert await api_db.pool.fetchval("SELECT count(*) FROM state") == 0
    other = await db(*api_db.spec.chains, schemas=dict(api_db.spec.schemas), schema="switchboard")
    assert await other.fetchval("SELECT current_database()") != api_db.name
    await api_db.pool.execute("INSERT INTO state(key,value) VALUES('private', $1)", [1, True])
    assert (await api.get("/api/butlers/switchboard/state/private")).json()["data"]["value"] == [
        1,
        True,
    ]
    assert await other.fetchval("SELECT count(*) FROM state WHERE key='private'") == 0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_http_lease_restores_state_and_dependencies_after_failure(
    api_db, production_api_app, _api_lease, failure
):
    app = production_api_app
    state = app.state._state.copy()
    overrides = app.dependency_overrides.copy()
    singletons = (deps._db_manager, deps._mcp_manager, deps._butler_configs)
    with pytest.raises(failure):
        async with leased_api(api_db, app, _api_lease) as client:
            assert (await client.get("/api/butlers/switchboard/state")).status_code == 200
            app.state.ready = True
            app.dependency_overrides[object()] = object()
            raise failure("synthetic body failure")
    assert client.is_closed
    assert app.state._state == state
    assert app.dependency_overrides == overrides
    assert all(
        old is new
        for old, new in zip(
            singletons, (deps._db_manager, deps._mcp_manager, deps._butler_configs), strict=True
        )
    )
    async with leased_api(api_db, app, _api_lease) as next_client:
        assert (await next_client.get("/api/butlers/switchboard/state")).status_code == 200


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_api_database_releases_clone_auth_login_and_connections_after_failure(
    postgres_container, production_api_app, failure
):
    admin = await _connect(postgres_container.get_connection_url())
    try:
        with pytest.raises(failure):
            async with api_database(
                postgres_container, production_api_app, ApiDatabaseSpec()
            ) as database:
                name = database.name
                role = await database.auth.pool.fetchval("SELECT session_user")
                assert await admin.fetchval(
                    "SELECT EXISTS(SELECT FROM pg_database WHERE datname=$1)", name
                )
                assert await admin.fetchval(
                    "SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role
                )
                assert await database.pool.fetchval("SELECT 1") == 1
                raise failure("synthetic database borrower failure")
        assert database.pool.is_closing()
        assert database.auth.pool.is_closing()
        assert database.manager.credential_shared_pool().is_closing()
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_database WHERE datname=$1)", name
        )
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role
        )
        assert (
            await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=$1", name)
            == 0
        )
    finally:
        await admin.close()


async def test_api_database_releases_resources_even_when_a_borrower_blocks_pool_close(
    postgres_container, production_api_app
):
    resource = api_database(postgres_container, production_api_app, ApiDatabaseSpec())
    database = await resource.__aenter__()
    role = await database.auth.pool.fetchval("SELECT session_user")
    held = await database.pool.acquire()
    connection = held._con
    assert await held.fetchval("SELECT current_database()") == database.name
    with pytest.raises(TimeoutError):
        await resource.__aexit__(None, None, None)
    assert database.auth.pool.is_closing()
    assert database.manager.credential_shared_pool().is_closing()
    assert connection.is_closed()
    await database.pool.release(held)
    admin = await _connect(postgres_container.get_connection_url())
    try:
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_database WHERE datname=$1)", database.name
        )
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role
        )
    finally:
        await admin.close()


async def test_api_database_cleans_up_after_partial_setup(
    postgres_container, production_api_app, monkeypatch
):
    from butlers.testing import migration

    created = []
    original = migration._storage_database

    def observe(event, server, name, kind):
        if event == "created" and kind == "C":
            created.append(name)
        return original(event, server, name, kind)

    monkeypatch.setattr(migration, "_storage_database", observe)
    spec = ApiDatabaseSpec(butlers=(ApiButler("switchboard", "invalid;schema"),))
    with pytest.raises(ValueError, match="schema"):
        async with api_database(postgres_container, production_api_app, spec):
            pytest.fail("Invalid schema unexpectedly completed setup")
    assert len(created) == 1
    admin = await _connect(postgres_container.get_connection_url())
    try:
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_database WHERE datname=$1)", created[0]
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=$1", created[0]
            )
            == 0
        )
    finally:
        await admin.close()


async def test_api_database_joins_clone_thread_before_cancelled_setup_cleanup(
    postgres_container, production_api_app, monkeypatch
):
    from butlers.testing import migration

    # Build the real template before timing the deliberate in-thread pause.
    async with api_database(postgres_container, production_api_app, ApiDatabaseSpec()) as warm:
        assert await warm.pool.fetchval("SELECT 1") == 1
    created = []
    started, resume = threading.Event(), threading.Event()
    original = migration._storage_database

    def observe(event, server, name, kind):
        original(event, server, name, kind)
        if event == "created" and kind == "C":
            created.append(name)
            started.set()
            if not resume.wait(timeout=10):
                raise RuntimeError("Synthetic clone pause was not released")

    monkeypatch.setattr(migration, "_storage_database", observe)
    resource = api_database(postgres_container, production_api_app, ApiDatabaseSpec())
    entering = asyncio.create_task(resource.__aenter__())
    try:
        assert await asyncio.to_thread(started.wait, timeout=10)
        entering.cancel()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await entering
    finally:
        resume.set()
        if not entering.done():
            entering.cancel()
            with pytest.raises(asyncio.CancelledError):
                await entering
    assert len(created) == 1
    admin = await _connect(postgres_container.get_connection_url())
    try:
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT FROM pg_database WHERE datname=$1)", created[0]
        )
    finally:
        await admin.close()
