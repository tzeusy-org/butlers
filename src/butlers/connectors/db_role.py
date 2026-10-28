"""Connector role enforcement for SET ROLE connector_writer."""

from __future__ import annotations

import asyncpg

from butlers.db_authority.catalog import setup_purpose
from butlers.db_authority.roles import AuthorityError, purpose_profile


async def connector_setup_role(conn: asyncpg.Connection) -> None:
    """SET ROLE connector_writer on every connection acquire.

    Used as asyncpg pool `setup` callback.
    """
    database = await conn.fetchval("SELECT current_database()")
    if not isinstance(database, str):
        raise AuthorityError("Connector database identity is unavailable")
    await setup_purpose(conn, purpose_profile(database, "connector"))
