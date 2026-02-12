"""Multi-database connection manager for the dashboard API.

Each butler owns a dedicated PostgreSQL database. The dashboard API needs
concurrent access to all butler databases. DatabaseManager maintains one
asyncpg pool per butler and provides utilities for cross-butler queries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from butlers.db import should_retry_with_ssl_disable

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages asyncpg connection pools for multiple butler databases.

    Usage::

        mgr = DatabaseManager(host="localhost", port=5432, user="postgres", password="postgres")
        await mgr.add_butler("switchboard")
        await mgr.add_butler("atlas")

        pool = mgr.pool("switchboard")
        results = await mgr.fan_out("SELECT count(*) FROM sessions")
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._ssl = ssl
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._pools: dict[str, asyncpg.Pool] = {}

    async def add_butler(self, butler_name: str, db_name: str | None = None) -> None:
        """Add a butler database connection pool.

        Parameters
        ----------
        butler_name:
            The butler's name (used as key for pool lookup).
        db_name:
            The database name. Defaults to butler_name if not provided.
        """
        if butler_name in self._pools:
            logger.warning("Butler %s already has a pool; skipping", butler_name)
            return

        effective_db = db_name or butler_name
        pool_kwargs: dict[str, Any] = {
            "host": self._host,
            "port": self._port,
            "user": self._user,
            "password": self._password,
            "database": effective_db,
            "min_size": self._min_pool_size,
            "max_size": self._max_pool_size,
        }
        if self._ssl is not None:
            pool_kwargs["ssl"] = self._ssl
        try:
            pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self._ssl):
                raise
            retry_kwargs = dict(pool_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info(
                "Retrying DB pool creation with ssl=disable for butler %s after SSL upgrade loss",
                butler_name,
            )
            pool = await asyncpg.create_pool(**retry_kwargs)
        self._pools[butler_name] = pool
        logger.info("Added pool for butler: %s (db=%s)", butler_name, effective_db)

    def pool(self, butler_name: str) -> asyncpg.Pool:
        """Get the connection pool for a specific butler.

        Raises KeyError if the butler hasn't been added.
        """
        if butler_name not in self._pools:
            raise KeyError(f"No pool for butler: {butler_name}")
        return self._pools[butler_name]

    @property
    def butler_names(self) -> list[str]:
        """Return list of all registered butler names."""
        return list(self._pools.keys())

    async def fan_out(
        self,
        query: str,
        args: tuple[Any, ...] = (),
        butler_names: list[str] | None = None,
    ) -> dict[str, list[asyncpg.Record]]:
        """Execute a query concurrently across multiple butler databases.

        Parameters
        ----------
        query:
            The SQL query to execute.
        args:
            Query arguments (positional).
        butler_names:
            Subset of butlers to query. Defaults to all registered butlers.

        Returns
        -------
        dict[str, list[asyncpg.Record]]
            Mapping of butler_name -> query results. If a query fails for a
            specific butler, that butler's entry will be an empty list and the
            error is logged.
        """
        targets = butler_names or self.butler_names

        async def _query_one(name: str) -> tuple[str, list[asyncpg.Record]]:
            try:
                p = self._pools[name]
                rows = await p.fetch(query, *args)
                return (name, rows)
            except Exception:
                logger.warning("fan_out query failed for butler %s", name, exc_info=True)
                return (name, [])

        results = await asyncio.gather(*[_query_one(n) for n in targets])
        return dict(results)

    async def close(self) -> None:
        """Close all connection pools."""
        for name, p in self._pools.items():
            try:
                await p.close()
                logger.info("Closed pool for butler: %s", name)
            except Exception:
                logger.warning("Error closing pool for butler: %s", name, exc_info=True)
        self._pools.clear()
