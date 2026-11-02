"""The one database factory for DB-backed tests (bu-rvgzmf.1).

Every DB-backed test gets its database here, never from a per-file ``pool`` or
``migrated_db_url`` fixture or from hand-written ``CREATE TABLE`` DDL:

- :func:`migrated_url` returns a fresh database migrated by the real Alembic
  chains. Each process (xdist worker) migrates once per distinct
  ``(chains, schemas, revisions)`` request into a template and every call
  clones it (:mod:`butlers.testing.migration`), so a clone costs a
  ``CREATE DATABASE ... TEMPLATE`` instead of a migration run.
- :func:`migrated_pool` opens an asyncpg pool on such a clone with the same
  JSONB codec and search-path behaviour as production
  :class:`butlers.db.Database` pools.

The root ``conftest.py`` exposes them as fixtures:

``db``
    Async factory: ``pool = await db("core", "relationship", schema="relationship")``.
    Pools close at test teardown.
``db_pool``
    A core-chain pool, the common case.
``fresh_db``
    Sync factory returning a URL, for migration-subject tests:
    ``url = fresh_db("core", revisions={"core": "core_170"})``.

As with :func:`~butlers.testing.migration.create_migrated_test_db`, a chain
migrates into ``public`` unless ``schemas`` maps it to a schema, for example
``schemas={"relationship": "relationship"}``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import asyncpg

from butlers.testing.migration import create_migrated_test_db, migration_db_name

Init = Callable[[asyncpg.Connection], Awaitable[None]]


def migrated_url(
    postgres_container: object,
    *chains: str,
    schemas: dict[str, str] | None = None,
    revisions: dict[str, str] | None = None,
) -> str:
    """A fresh clone of the migrated template for *chains* (default ``core``)."""
    return create_migrated_test_db(
        postgres_container, migration_db_name(), list(chains or ("core",)), schemas, revisions
    )


async def open_pool(
    db_url: str,
    *,
    schema: str | None = None,
    init: Init | None = None,
    min_size: int = 1,
    max_size: int = 3,
) -> asyncpg.Pool:
    """Open a pool on *db_url* the way production pools connect.

    *schema* sets the production search path for that butler schema; *init*
    runs after the JSONB codec is registered on each connection (for example
    ``SET ROLE``).
    """
    from butlers.db import register_jsonb_codec, schema_search_path

    async def _init(conn: asyncpg.Connection) -> None:
        await register_jsonb_codec(conn)
        if init is not None:
            await init(conn)

    kwargs: dict[str, object] = {"min_size": min_size, "max_size": max_size, "init": _init}
    search_path = schema_search_path(schema)
    if search_path is not None:
        kwargs["server_settings"] = {"search_path": search_path}
    return await asyncpg.create_pool(db_url, **kwargs)


async def migrated_pool(
    postgres_container: object,
    *chains: str,
    schema: str | None = None,
    schemas: dict[str, str] | None = None,
    revisions: dict[str, str] | None = None,
    init: Init | None = None,
    min_size: int = 1,
    max_size: int = 3,
) -> asyncpg.Pool:
    """A pool on a fresh migrated clone; the clone is made off the event loop."""
    db_url = await asyncio.to_thread(
        migrated_url, postgres_container, *chains, schemas=schemas, revisions=revisions
    )
    return await open_pool(db_url, schema=schema, init=init, min_size=min_size, max_size=max_size)
