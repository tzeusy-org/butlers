"""Explicit opt-in administrative PostgreSQL fixture for ad-hoc test databases.

This function-scoped factory selects the testcontainer administrative login.
It provides no serving-role admission and registers no global fixture or hook.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from asyncpg.pool import Pool
    from testcontainers.postgres import PostgresContainer


def _unique_test_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def provisioned_postgres_pool(
    postgres_container: PostgresContainer,
) -> Callable[..., AbstractAsyncContextManager[Pool]]:
    """Create a fresh database and asyncpg pool for a single test usage.

    Tests should use this as:
        async with provisioned_postgres_pool() as pool:
            ...

    Pass ``schema=`` to scope the pool's search_path (mirrors one-db/
    multi-schema production topology, e.g. a real "switchboard"-scoped pool
    for tests that need schema-qualified queries to resolve without an
    explicit prefix, or "public" to reproduce a caller whose search_path
    lacks the schema entirely).
    """
    import asyncpg

    from butlers.db import (
        Database,
        register_jsonb_codec,
        schema_search_path,
        should_retry_with_ssl_disable,
    )

    @asynccontextmanager
    async def _provision(
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 3,
        schema: str | None = None,
    ) -> AsyncIterator[Pool]:
        # Validate before provisioning: this scopes administrative test queries,
        # and does not establish or bypass a runtime role.
        search_path = schema_search_path(schema)
        db = Database(
            db_name=_unique_test_db_name(),
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
        # This is an explicitly administrative fixture for ad-hoc schemas,
        # not a production serving/provisioning pool. Select only the owning
        # testcontainer control login; Database.provision deliberately requires
        # a separate normal-owner contract and must never be an implicit path.
        from butlers.db_authority.apply import quote_identifier
        from butlers.db_authority.roles import AuthorityError

        admin_options = dict(
            host=db.host,
            port=db.port,
            user=db.user,
            password=db.password,
            database="postgres",
            timeout=5,
        )
        if db.ssl is not None:
            admin_options["ssl"] = db.ssl
        try:
            admin = await asyncpg.connect(**admin_options)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, db.ssl):
                raise
            admin = await asyncpg.connect(**admin_options, ssl="disable")
        primary = None
        try:
            selected = await admin.fetchval(
                "SELECT session_user=$1 AND current_user=$1 AND EXISTS "
                "(SELECT FROM pg_catalog.pg_roles WHERE rolname=session_user "
                "AND (rolsuper OR rolcreatedb))",
                db.user,
            )
            if selected is not True:
                raise AuthorityError(
                    "Selected testcontainer administrative authority is unavailable"
                )
            await admin.execute(
                f"CREATE DATABASE {quote_identifier(db.db_name)} "
                f"OWNER {quote_identifier(db.user)} TEMPLATE template0"
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                await admin.close(timeout=5)
            except BaseException:
                admin.terminate()
                if primary is None:
                    raise
                primary.add_note("Administrative fixture connection cleanup was incomplete")
        pool_kwargs = {
            "host": db.host,
            "port": db.port,
            "user": db.user,
            "password": db.password,
            "database": db.db_name,
            "min_size": min_pool_size,
            "max_size": max_pool_size,
            "init": register_jsonb_codec,
        }
        if search_path is not None:
            pool_kwargs["server_settings"] = {"search_path": search_path}
        if db.ssl is not None:
            pool_kwargs["ssl"] = db.ssl

        async def _open_pool():
            # Keep the native pool while initialization is awaited so a failed
            # or cancelled initialization cannot orphan its owned connections.
            candidate = asyncpg.create_pool(**pool_kwargs)
            try:
                return await candidate
            except BaseException:
                candidate.terminate()
                raise

        try:
            pool = await _open_pool()
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, db.ssl):
                raise
            pool_kwargs["ssl"] = "disable"
            pool = await _open_pool()
        try:
            yield pool
        finally:
            try:
                await pool.close()
            except BaseException:
                pool.terminate()
                raise

    return _provision
