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

# A row created by this module is storage state, not a running process, so it
# is stamped ``operational_role = 'checkpoint'`` on INSERT. The conflict branch
# deliberately does NOT touch ``operational_role``: most connectors use their
# canonical heartbeat identity as the cursor key, and for those the row is a
# ``runtime_instance`` that also happens to carry a cursor. Re-stamping it here
# would demote a live connector out of the fleet roster on its next checkpoint
# save. Ownership is one-way — the heartbeat producer promotes a row to
# ``runtime_instance``, and nothing here demotes it back.
#
# ``parent_endpoint_identity`` only ever fills in a value: an explicit parent
# supplied by the caller wins, otherwise any parent already recorded is kept.
_UPSERT_SQL = """\
INSERT INTO switchboard.connector_registry
    (connector_type, endpoint_identity, checkpoint_cursor, checkpoint_updated_at,
     operational_role, parent_endpoint_identity)
VALUES ($1, $2, $3, $4, 'checkpoint', $5)
ON CONFLICT (connector_type, endpoint_identity)
DO UPDATE SET
    checkpoint_cursor        = EXCLUDED.checkpoint_cursor,
    checkpoint_updated_at    = EXCLUDED.checkpoint_updated_at,
    parent_endpoint_identity = COALESCE(
        EXCLUDED.parent_endpoint_identity,
        connector_registry.parent_endpoint_identity
    )
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
    *,
    parent_endpoint_identity: str | None = None,
) -> None:
    """Upsert checkpoint cursor into ``switchboard.connector_registry``.

    If no row exists for (connector_type, endpoint_identity), one is inserted
    with ``operational_role = 'checkpoint'`` — storage state, with no runtime
    liveness or fleet-health authority. An existing row keeps whatever role its
    producer already stamped, so a connector that checkpoints under its own
    heartbeat identity stays a ``runtime_instance``.

    Args:
        pool: asyncpg pool that can reach the ``switchboard`` schema.
        connector_type: Connector type this cursor belongs to.
        endpoint_identity: Cursor key. May encode extra dimensions (account,
            resource) beyond the connector's heartbeat identity.
        cursor_value: Opaque cursor to persist.
        parent_endpoint_identity: The ``endpoint_identity`` of the runtime
            instance this cursor belongs to, when the cursor key is not itself
            that identity. Pass it whenever the key carries extra dimensions so
            the checkpoint stays inspectable under its parent instead of
            floating unattached. ``None`` leaves any recorded parent unchanged.
    """
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_SQL,
            connector_type,
            endpoint_identity,
            cursor_value,
            now,
            parent_endpoint_identity,
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


_SELECT_SETTINGS_SQL = """\
SELECT settings
FROM switchboard.connector_registry
WHERE connector_type = $1
  AND endpoint_identity = $2
"""

_UPSERT_SETTINGS_SQL = """\
INSERT INTO switchboard.connector_registry
    (connector_type, endpoint_identity, settings)
VALUES ($1, $2, $3)
ON CONFLICT (connector_type, endpoint_identity)
DO UPDATE SET
    settings = COALESCE(connector_registry.settings, '{}'::jsonb) || $3
RETURNING settings
"""


async def load_connector_settings(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
) -> dict | None:
    """Read the ``settings`` JSONB column from ``switchboard.connector_registry``.

    Returns the settings dict when present, or ``None`` when the row is missing
    or the settings column is NULL.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_SETTINGS_SQL,
            connector_type,
            endpoint_identity,
        )
    if row is None or row["settings"] is None:
        return None
    return row["settings"]


async def save_connector_settings(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
    settings: dict,
) -> dict:
    """Shallow-merge ``settings`` into ``switchboard.connector_registry.settings``.

    Upserts a row for (connector_type, endpoint_identity) if one does not exist,
    then merges the provided ``settings`` dict at the top level (JSONB ``||``
    operator — existing keys not present in ``settings`` are preserved).

    Returns the merged settings dict after the update.
    """
    import json as _json

    # Sanitize to JSON-safe primitives (e.g. tuples -> lists, non-primitive
    # values -> str via default=str), then bind the resulting dict directly.
    # Every asyncpg pool in this codebase registers register_jsonb_codec()
    # (src/butlers/db.py), whose encoder already calls json.dumps() on the
    # bound value. Pre-serializing here (or casting the parameter via
    # ``::jsonb``) would double-encode the value into a jsonb-typed STRING
    # instead of an OBJECT (bu-dycxq — same anti-pattern as bu-cymc4/bu-x92jw).
    sanitized_settings = _json.loads(_json.dumps(settings, default=str))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _UPSERT_SETTINGS_SQL,
            connector_type,
            endpoint_identity,
            sanitized_settings,
        )
    if row is None or row["settings"] is None:
        return {}
    return row["settings"]


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

    from butlers.connectors.db_role import connector_setup_role
    from butlers.db import register_jsonb_codec, should_retry_with_ssl_disable

    pool_kwargs: dict = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "min_size": min_size,
        "max_size": max_size,
        "command_timeout": 5,
        "setup": connector_setup_role,
        "init": register_jsonb_codec,
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
