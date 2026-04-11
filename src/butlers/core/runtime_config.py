"""RuntimeConfigAccessor — TTL-cached read/write accessor for per-butler runtime config.

The ``runtime_config`` table stores operational tuning knobs (concurrency limits
and core tool groups) in each butler's schema.

The accessor is created during daemon startup (phase 9b) and shared between:
- The daemon (for ``core_groups`` at tool registration time)
- The spawner constructor (for concurrency limits cached at startup)

Cache behavior:
- ``get()`` returns the cached row if within TTL, otherwise queries the DB.
- On DB failure: returns stale cache if available, raises if no prior cache.
- ``seed_if_empty()`` uses INSERT ... ON CONFLICT DO NOTHING for race safety.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import asyncpg

from butlers.config import RuntimeSeedConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeConfig:
    """Effective runtime configuration from the DB ``runtime_config`` table.

    This is the runtime source of truth, distinct from ``RuntimeSeedConfig``
    (the toml seed) and ``butlers.config.RuntimeConfig`` (the legacy static config).
    """

    butler_name: str
    core_groups: tuple[str, ...] | None = None
    max_concurrent: int = 3
    max_queued: int = 10
    seeded_at: str | None = None
    updated_at: str | None = None


def _row_to_config(row: asyncpg.Record) -> RuntimeConfig:
    """Convert an asyncpg Record to a RuntimeConfig dataclass."""
    raw_core_groups = row["core_groups"]
    core_groups = tuple(raw_core_groups) if raw_core_groups is not None else None

    return RuntimeConfig(
        butler_name=row["butler_name"],
        core_groups=core_groups,
        max_concurrent=row["max_concurrent"],
        max_queued=row["max_queued"],
        seeded_at=str(row["seeded_at"]) if row["seeded_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


class RuntimeConfigAccessor:
    """TTL-cached accessor for the per-schema ``runtime_config`` table.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's database.
    schema:
        The butler's DB schema name (e.g. ``"finance"``).
    ttl_s:
        Cache time-to-live in seconds. Default 30.0.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        ttl_s: float = 30.0,
    ) -> None:
        self._pool = pool
        self._schema = schema
        self._ttl_s = ttl_s
        self._cache: RuntimeConfig | None = None
        self._cache_time: float = 0.0

    async def get(self) -> RuntimeConfig:
        """Return the current runtime config, using cache if within TTL.

        On DB failure: returns stale cache if available, raises if no prior cache.
        """
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_time) < self._ttl_s:
            return self._cache

        try:
            row = await self._pool.fetchrow(f"SELECT * FROM {self._schema}.runtime_config LIMIT 1")
            if row is None:
                if self._cache is not None:
                    logger.warning(
                        "runtime_config table empty for schema=%s; returning stale cache",
                        self._schema,
                    )
                    return self._cache
                raise RuntimeError(
                    f"No runtime_config row found in schema {self._schema} and no prior cache"
                )
            config = _row_to_config(row)
            self._cache = config
            self._cache_time = time.monotonic()
            return config
        except Exception:
            if self._cache is not None:
                logger.warning(
                    "DB query failed for %s.runtime_config; returning stale cache",
                    self._schema,
                    exc_info=True,
                )
                return self._cache
            raise

    async def seed_if_empty(self, seed: RuntimeSeedConfig, butler_name: str) -> RuntimeConfig:
        """Insert a row from seed values if the table is empty.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` for race safety when
        multiple daemon instances start concurrently.

        Returns the effective runtime config (existing or newly seeded).
        """
        core_groups_val = list(seed.core_groups) if seed.core_groups is not None else None

        await self._pool.execute(
            f"""
            INSERT INTO {self._schema}.runtime_config
                (butler_name, core_groups, max_concurrent, max_queued)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (butler_name) DO NOTHING
            """,
            butler_name,
            core_groups_val,
            seed.max_concurrent_sessions,
            seed.max_queued_sessions,
        )

        # Always read back the effective row (may be pre-existing)
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._schema}.runtime_config WHERE butler_name = $1",
            butler_name,
        )
        if row is None:
            raise RuntimeError(
                f"Failed to read runtime_config after seed for {butler_name} in {self._schema}"
            )

        config = _row_to_config(row)
        self._cache = config
        self._cache_time = time.monotonic()
        return config

    def invalidate_cache(self) -> None:
        """Force the next ``get()`` call to query the database."""
        self._cache_time = float("-inf")
