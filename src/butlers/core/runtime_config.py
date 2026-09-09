"""RuntimeConfigAccessor — TTL-cached read/write accessor for per-butler runtime config.

The ``runtime_config`` table stores operational tuning knobs (concurrency limits,
core tool groups, and catalog read authority) in each butler's schema.

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
    (the toml seed in :mod:`butlers.config`). The Spawner prefers this
    DB-backed value and falls back to the seed when no accessor is wired.
    """

    butler_name: str
    core_groups: tuple[str, ...] | None = None
    catalog_read_sensitivity: str = "normal"
    max_concurrent: int = 3
    max_queued: int = 10
    tool_exposure_policy: str = "eager_filtered"
    blind_spot_preamble_enabled: bool = True
    seeded_at: str | None = None
    updated_at: str | None = None


def _row_to_config(row: asyncpg.Record) -> RuntimeConfig:
    """Convert an asyncpg Record to a RuntimeConfig dataclass."""
    raw_core_groups = row["core_groups"]
    core_groups = tuple(raw_core_groups) if raw_core_groups is not None else None
    try:
        catalog_read_sensitivity = row["catalog_read_sensitivity"]
    except (KeyError, IndexError):
        # Rolling startup can briefly project a pre-core_209 row shape. Missing
        # authority must remain compatible without ever granting more access.
        catalog_read_sensitivity = "normal"
    try:
        tool_exposure_policy = row["tool_exposure_policy"]
    except (KeyError, IndexError):
        # Rolling startup can briefly project a pre-core_224 row shape. Missing
        # evidence must preserve the conservative eager behavior, never opt a
        # butler into native discovery by omission.
        tool_exposure_policy = "eager_filtered"
    try:
        blind_spot_preamble_enabled = bool(row["blind_spot_preamble_enabled"])
    except (KeyError, IndexError):
        # Rolling startup can briefly project a pre-core_225 row shape. Default
        # to the preamble being active — matching the migration's own column
        # default — never silently opting a butler out by omission.
        blind_spot_preamble_enabled = True

    return RuntimeConfig(
        butler_name=row["butler_name"],
        core_groups=core_groups,
        catalog_read_sensitivity=catalog_read_sensitivity,
        max_concurrent=row["max_concurrent"],
        max_queued=row["max_queued"],
        tool_exposure_policy=tool_exposure_policy,
        blind_spot_preamble_enabled=blind_spot_preamble_enabled,
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
                (butler_name, core_groups, catalog_read_sensitivity, max_concurrent, max_queued,
                 tool_exposure_policy)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (butler_name) DO NOTHING
            """,
            butler_name,
            core_groups_val,
            seed.catalog_read_sensitivity,
            seed.max_concurrent_sessions,
            seed.max_queued_sessions,
            seed.tool_exposure_policy,
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

    async def get_tool_exposure_policy(self) -> str:
        """Return the current ``tool_exposure_policy``, bypassing the TTL cache.

        This field is hot: the dashboard API writes it directly to the DB in
        a process that may be separate from the daemon holding this accessor.
        A cached :meth:`get` result can be up to ``ttl_s`` stale, which is
        unacceptable for "the first session planned after a committed PATCH
        must see the new policy". Every per-attempt caller MUST use this
        method (not :meth:`get`) to resolve the effective exposure policy, so
        correctness does not depend on any cross-process cache-invalidation
        signal.

        On DB failure: returns the stale cached policy if a prior ``get()``
        or ``seed_if_empty()`` populated the cache, otherwise raises.
        """
        try:
            row = await self._pool.fetchrow(
                f"SELECT tool_exposure_policy FROM {self._schema}.runtime_config LIMIT 1"
            )
        except Exception:
            if self._cache is not None:
                logger.warning(
                    "DB query failed for %s.runtime_config.tool_exposure_policy; "
                    "returning stale cache",
                    self._schema,
                    exc_info=True,
                )
                return self._cache.tool_exposure_policy
            raise

        if row is None:
            if self._cache is not None:
                logger.warning(
                    "runtime_config table empty for schema=%s; returning stale cache",
                    self._schema,
                )
                return self._cache.tool_exposure_policy
            raise RuntimeError(
                f"No runtime_config row found in schema {self._schema} and no prior cache"
            )

        try:
            return row["tool_exposure_policy"]
        except (KeyError, IndexError):
            return "eager_filtered"
