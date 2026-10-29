"""Connector role enforcement for SET ROLE connector_writer."""

from __future__ import annotations

import asyncpg


async def connector_setup_role(conn: asyncpg.Connection) -> None:
    """SET ROLE connector_writer on every connection acquire.

    Used as asyncpg pool `setup` callback.
    """
    await conn.execute('SET ROLE "connector_writer"')
