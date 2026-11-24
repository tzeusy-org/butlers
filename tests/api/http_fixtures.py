"""Production-built HTTP clients on private migrated databases (bu-rvgzmf.3).

This seam binds existing singleton/app-state resources; it does not boot or replace
lifespan. API pools deliberately match production's SET ROLE-disabled behavior.
The owner-auth pool uses a separate restricted LOGIN and the real auth service.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api import deps
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.owner_auth.service import (
    OwnerAuthService,
    _initialize_auth_connection,
)
from butlers.testing.db import migrated_url, open_pool
from butlers.testing.db_manager import db_manager
from butlers.testing.migration import drop_test_db, migration_bootstrap_db_url

ORIGIN = "https://butlers.example.test"


@dataclass(frozen=True)
class ApiButler:
    name: str
    schema: str
    modules: frozenset[str] = frozenset()
    memory_schema: str | None = None


@dataclass(frozen=True)
class ApiDatabaseSpec:
    """Select real migration chains/schema mappings; no hand-written test DDL."""

    chains: tuple[str, ...] = ("core", "switchboard")
    schemas: tuple[tuple[str, str], ...] = (("core", "switchboard"), ("switchboard", "switchboard"))
    butlers: tuple[ApiButler, ...] = (ApiButler("switchboard", "switchboard"),)
    revisions: tuple[tuple[str, str], ...] = ()


@dataclass
class ApiDatabase:
    manager: DatabaseManager
    pool: asyncpg.Pool
    auth: OwnerAuthService
    name: str
    spec: ApiDatabaseSpec


async def _close_pool(pool: asyncpg.Pool) -> None:
    try:
        await asyncio.wait_for(pool.close(), timeout=10)
    except BaseException:
        pool.terminate()
        raise


async def _connect(url: str) -> asyncpg.Connection:
    """Use the server adapter's privileged connection only for auth provisioning."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return await asyncpg.connect(
        host=query.get("host", [parsed.hostname])[0],
        port=int(query.get("port", [parsed.port or 5432])[0]),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(parsed.path.lstrip("/")),
        timeout=5,
    )


async def _drop_auth_login(admin: asyncpg.Connection, role: str, database: str) -> None:
    await admin.execute(f'REVOKE CONNECT ON DATABASE "{database}" FROM "{role}"')
    await admin.execute(f'DROP ROLE "{role}"')


@pytest.fixture(scope="session")
def production_api_app(tmp_path_factory: pytest.TempPathFactory) -> FastAPI:
    """One unmodified production factory invocation per process/xdist worker."""
    static = tmp_path_factory.mktemp("api-static")
    (static / "index.html").write_text("synthetic dashboard", encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        patch.setenv("DASHBOARD_AUTH_ORIGIN", ORIGIN)
        patch.setenv("DASHBOARD_AUTH_RP_ID", "butlers.example.test")
        patch.setenv("DASHBOARD_AUTH_TRUSTED_PROXY_PEERS", "")
        patch.setenv("DASHBOARD_AUTH_DEPLOYMENT", "http-tests")
        return create_app(api_key=uuid4().hex, static_dir=str(static))


@pytest.fixture(scope="session")
def _api_lease() -> asyncio.Lock:
    return asyncio.Lock()


@asynccontextmanager
async def api_database(postgres_container, production_api_app, spec: ApiDatabaseSpec):
    """Own one clone, its pools and its dedicated authentication LOGIN."""
    if not isinstance(spec, ApiDatabaseSpec) or not spec.butlers:
        raise ValueError("api_db requires an ApiDatabaseSpec with at least one butler")
    provisioning = asyncio.create_task(
        asyncio.to_thread(
            migrated_url,
            postgres_container,
            *spec.chains,
            schemas=dict(spec.schemas),
            revisions=dict(spec.revisions),
        )
    )
    try:
        url = await asyncio.shield(provisioning)
    except asyncio.CancelledError:
        # Cancellation cannot stop a migration/clone thread. Join it before
        # deleting its clone, even if the caller cancels again during cleanup.
        async def release_clone():
            cloned_url = await provisioning
            await asyncio.to_thread(
                drop_test_db, postgres_container, unquote(urlparse(cloned_url).path.lstrip("/"))
            )

        cleanup = asyncio.create_task(release_clone())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise
    name = unquote(urlparse(url).path.lstrip("/"))
    role = "api_auth_" + uuid4().hex
    password = uuid4().hex
    # A clone borrows the template's migration LOGIN; never drop that shared role.
    async with AsyncExitStack() as resources:
        resources.push_async_callback(asyncio.to_thread, drop_test_db, postgres_container, name)
        admin = await _connect(migration_bootstrap_db_url(postgres_container, name))
        resources.push_async_callback(admin.close, timeout=10)
        await admin.execute(
            f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
            f"NOBYPASSRLS NOREPLICATION PASSWORD '{password}'"
        )
        # DROP ROLE must follow all borrower pool closures, before the database drop.
        resources.push_async_callback(_drop_auth_login, admin, role, name)
        await admin.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{role}"')
        await admin.execute(f'GRANT dashboard_auth_api TO "{role}"')
        from butlers.db import register_jsonb_codec

        await register_jsonb_codec(admin)
        reconciled = await admin.fetchval(
            "SELECT dashboard_auth.host('reconcile_mode', $1::jsonb)",
            OwnerAuthService(None, production_api_app.state.owner_auth_config)._config()
            | {"confirm_revoke": True},
        )
        if "error" in reconciled:
            raise RuntimeError("Synthetic owner-auth mode reconciliation failed")
        parsed = urlparse(url)
        # Keep the adapter's host/query, changing only the isolated auth login.
        auth_netloc = f"{role}:{password}@{parsed.netloc.rsplit('@', 1)[-1]}"
        auth_url = parsed._replace(netloc=auth_netloc).geturl()
        auth_pool = await open_pool(auth_url, init=_initialize_auth_connection)
        resources.push_async_callback(_close_pool, auth_pool)
        auth = OwnerAuthService(auth_pool, production_api_app.state.owner_auth_config)
        pools = {}
        for butler in spec.butlers:
            pool = await open_pool(url, schema=butler.schema)
            resources.push_async_callback(_close_pool, pool)
            pools[butler.name] = pool
        shared = await open_pool(url, schema="public")
        resources.push_async_callback(_close_pool, shared)
        manager = db_manager(
            pools,
            schemas={b.name: b.schema for b in spec.butlers},
            memory_schemas={b.name: b.memory_schema for b in spec.butlers},
            modules={b.name: b.modules for b in spec.butlers},
            credential_pool=shared,
        )
        for butler in spec.butlers:
            await manager.snapshot_relation_presence(
                butler.name, deps._OPTIONAL_DOMAIN_RELATIONS_TO_SNAPSHOT
            )
            await manager.snapshot_memory_relation_presence(
                butler.name, deps._OPTIONAL_MEMORY_RELATIONS_TO_SNAPSHOT
            )
        yield ApiDatabase(manager, pools[spec.butlers[0].name], auth, name, spec)


@pytest.fixture
async def api_db(request, postgres_container, production_api_app) -> AsyncIterator[ApiDatabase]:
    """A fresh .1 clone. Parametrize indirectly with an ApiDatabaseSpec for a family."""
    async with api_database(
        postgres_container, production_api_app, getattr(request, "param", ApiDatabaseSpec())
    ) as database:
        yield database


@asynccontextmanager
async def leased_api(api_db, app, lease) -> AsyncIterator[httpx.AsyncClient]:
    """Lease the app exclusively; restore exact prior state and singleton identities."""
    async with lease:
        previous_state = app.state._state.copy()
        previous_overrides = app.dependency_overrides.copy()
        peers = deps.MCPClientManager()  # Unregistered peers cannot make live calls.
        try:
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(deps, "_db_manager", api_db.manager)
                patch.setattr(deps, "_mcp_manager", peers)
                patch.setattr(
                    deps,
                    "_butler_configs",
                    [
                        deps.ButlerConnectionInfo(
                            name=b.name,
                            port=0,
                            db_name=api_db.name,
                            db_schema=b.schema,
                            modules=b.modules,
                            memory_schema=b.memory_schema,
                        )
                        for b in api_db.spec.butlers
                    ],
                )
                app.state.owner_auth_service = api_db.auth
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url=ORIGIN,
                    headers={"X-API-Key": app.state.owner_auth_config.api_key},
                    follow_redirects=False,
                ) as client:
                    yield client
        finally:
            try:
                await asyncio.wait_for(peers.close(), timeout=10)
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous_overrides)
                app.state._state.clear()
                app.state._state.update(previous_state)


@pytest.fixture
async def api(api_db, production_api_app, _api_lease) -> AsyncIterator[httpx.AsyncClient]:
    """Authenticated production HTTP client, closed before its clone is released."""
    async with leased_api(api_db, production_api_app, _api_lease) as client:
        yield client
