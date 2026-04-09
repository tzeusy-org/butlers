"""Connector role enforcement for SET ROLE connector_writer."""

from __future__ import annotations

import asyncpg


async def connector_setup_role(conn: asyncpg.Connection) -> None:
    """SET ROLE connector_writer on every connection acquire.

    Used as asyncpg pool `setup` callback.
    """
    await conn.execute('SET ROLE "connector_writer"')


async def verify_connector_role(pool: asyncpg.Pool) -> bool:
    """Check if connector_writer role exists in pg_roles."""
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_writer')"
            )
        )
