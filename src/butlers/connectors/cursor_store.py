"""DB-backed cursor persistence for connector runtimes.

Replaces file-based checkpoint/cursor storage with direct reads and writes
to ``switchboard.connector_registry.checkpoint_cursor``.

All functions accept an asyncpg pool that can reach the ``switchboard``
schema.  The SQL uses explicit ``switchboard.connector_registry``
qualification so the pool does not need ``switchboard`` on its search_path.

Typical usage inside a connector::

    from butlers.connectors.cursor_store import load_cursor, save_cursor

    cursor = await load_cursor(pool, "gmail", "gmail:user:alice@gmail.com")
    ...
    await save_cursor(pool, "gmail", "gmail:user:alice@gmail.com", new_value)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_UPSERT_SQL = """\
INSERT INTO switchboard.connector_registry
    (connector_type, endpoint_identity, checkpoint_cursor, checkpoint_updated_at)
VALUES ($1, $2, $3, $4)
ON CONFLICT (connector_type, endpoint_identity)
DO UPDATE SET
    checkpoint_cursor     = EXCLUDED.checkpoint_cursor,
    checkpoint_updated_at = EXCLUDED.checkpoint_updated_at
"""

_SELECT_SQL = """\
SELECT checkpoint_cursor
FROM switchboard.connector_registry
WHERE connector_type = $1
  AND endpoint_identity = $2
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def save_cursor(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
    cursor_value: str,
) -> None:
    """Upsert checkpoint cursor into ``switchboard.connector_registry``.

    If no row exists for (connector_type, endpoint_identity), one is inserted.
    """
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_SQL,
            connector_type,
            endpoint_identity,
            cursor_value,
            now,
        )
    logger.debug(
        "Saved cursor to DB: connector_type=%s, endpoint=%s",
        connector_type,
        endpoint_identity,
    )


async def load_cursor(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
) -> str | None:
    """Read checkpoint cursor from ``switchboard.connector_registry``.

    Returns ``None`` when the row is missing or the cursor column is NULL.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_SQL,
            connector_type,
            endpoint_identity,
        )
    if row is None:
        return None
    return row["checkpoint_cursor"]


async def create_cursor_pool(
    *,
    host: str = "localhost",
    port: int = 5432,
    user: str = "butlers",
    password: str = "butlers",
    database: str = "butlers",
    ssl: str | None = None,
    min_size: int = 1,
    max_size: int = 2,
) -> asyncpg.Pool:
    """Create an asyncpg pool suitable for cursor read/write operations.

    The pool connects to the target database.  SQL statements in this module
    use explicit ``switchboard.`` schema qualification, so no special
    ``search_path`` is needed.
    """
    import asyncpg as _asyncpg

    from butlers.db import should_retry_with_ssl_disable

    pool_kwargs: dict = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "min_size": min_size,
        "max_size": max_size,
        "command_timeout": 5,
    }
    if ssl is not None:
        pool_kwargs["ssl"] = ssl

    try:
        return await _asyncpg.create_pool(**pool_kwargs)
    except Exception as exc:
        if should_retry_with_ssl_disable(exc, ssl):
            pool_kwargs["ssl"] = "disable"
            return await _asyncpg.create_pool(**pool_kwargs)
        raise


async def create_cursor_pool_from_env() -> asyncpg.Pool:
    """Create a cursor pool using standard DB env vars.

    Uses ``db_params_from_env()`` with the database name from
    ``CONNECTOR_BUTLER_DB_NAME`` (default ``butlers``).
    """
    import os

    from butlers.db import db_params_from_env

    params = db_params_from_env()
    db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"

    return await create_cursor_pool(
        host=str(params["host"] or "localhost"),
        port=int(params["port"] or 5432),
        user=str(params["user"] or "butlers"),
        password=str(params["password"] or "butlers"),
        database=db_name,
        ssl=str(params["ssl"]) if params.get("ssl") is not None else None,
    )
