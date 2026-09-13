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

import hashlib
import json
import logging
import time
from dataclasses import dataclass

import asyncpg

from butlers.config import RuntimeSeedConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeConfig:
    """Effective runtime configuration from the DB ``runtime_config`` table.

    Operational fields remain DB-backed, but ``core_groups`` is bounded by
    ``RuntimeSeedConfig`` (the Git declaration): the DB may retain only an
    explicitly reasoned strict subset.
    """

    butler_name: str
    core_groups: tuple[str, ...] | None = None
    core_groups_narrowing_reason: str | None = None
    catalog_read_sensitivity: str = "normal"
    max_concurrent: int = 3
    max_queued: int = 10
    tool_exposure_policy: str = "eager_filtered"
    blind_spot_preamble_enabled: bool = True
    seeded_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class CoreGroupResolution:
    """Decision record for Git-vs-runtime core-tool group authority."""

    declared: tuple[str, ...] | None
    runtime: tuple[str, ...] | None
    effective: tuple[str, ...] | None
    source: str
    narrowing_reason: str | None
    requires_reconciliation: bool


def resolve_effective_core_groups(
    declared: tuple[str, ...] | None,
    runtime: tuple[str, ...] | None,
    *,
    narrowing_reason: str | None,
) -> CoreGroupResolution:
    """Resolve core groups without allowing runtime state to broaden Git authority.

    ``None`` means every group. Runtime configuration may override the Git
    declaration only when it is a true narrowing and carries a non-blank
    operator reason. Any other mismatch is stale or invalid runtime state and
    is reconciled back to the Git declaration.
    """
    reason = narrowing_reason.strip() if narrowing_reason else None
    declared_set = None if declared is None else frozenset(declared)
    runtime_set = None if runtime is None else frozenset(runtime)

    if declared_set == runtime_set:
        return CoreGroupResolution(declared, runtime, declared, "git", reason, False)

    is_true_narrowing = runtime_set is not None and (
        declared_set is None or runtime_set < declared_set
    )
    if reason and is_true_narrowing:
        return CoreGroupResolution(declared, runtime, runtime, "runtime_narrowing", reason, False)

    return CoreGroupResolution(declared, runtime, declared, "git", reason, True)


def _row_to_config(row: asyncpg.Record) -> RuntimeConfig:
    """Convert an asyncpg Record to a RuntimeConfig dataclass."""
    raw_core_groups = row["core_groups"]
    core_groups = tuple(raw_core_groups) if raw_core_groups is not None else None
    try:
        core_groups_narrowing_reason = row["core_groups_narrowing_reason"]
    except (KeyError, IndexError):
        core_groups_narrowing_reason = None
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
        # Rolling startup can briefly project a pre-core_228 row shape. Default
        # to the preamble being active — matching the migration's own column
        # default — never silently opting a butler out by omission.
        blind_spot_preamble_enabled = True

    return RuntimeConfig(
        butler_name=row["butler_name"],
        core_groups=core_groups,
        core_groups_narrowing_reason=core_groups_narrowing_reason,
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
        """Seed an empty row and reconcile core-group authority to Git.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` for race safety when
        multiple daemon instances start concurrently.

        Existing core groups survive only as an explicitly reasoned strict
        subset of the Git declaration. Every other non-empty diff is updated
        and audited atomically.
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

        # Read the existing/new row, then reconcile stale frozen seeds to Git.
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._schema}.runtime_config WHERE butler_name = $1",
            butler_name,
        )
        if row is None:
            raise RuntimeError(
                f"Failed to read runtime_config after seed for {butler_name} in {self._schema}"
            )

        runtime = _row_to_config(row)
        resolution = resolve_effective_core_groups(
            seed.core_groups,
            runtime.core_groups,
            narrowing_reason=runtime.core_groups_narrowing_reason,
        )
        digest_payload = None if seed.core_groups is None else list(seed.core_groups)
        toml_digest = hashlib.sha256(
            json.dumps(digest_payload, separators=(",", ":")).encode()
        ).hexdigest()
        audit_metadata = json.dumps(
            {
                "toml_digest": toml_digest,
                "toml_core_groups": digest_payload,
                "runtime_core_groups": (
                    None if runtime.core_groups is None else list(runtime.core_groups)
                ),
                "effective_core_groups": digest_payload,
                "runtime_narrowing_reason": runtime.core_groups_narrowing_reason,
            },
            separators=(",", ":"),
        )
        audited = await self._pool.fetchrow(
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT core_groups AS previous_core_groups
                FROM {self._schema}.runtime_config
                WHERE butler_name = $1
                  AND core_groups IS DISTINCT FROM $2::text[]
                  AND $3::boolean
                FOR UPDATE
            ), reconciled AS (
                UPDATE {self._schema}.runtime_config AS runtime_config
                SET core_groups = $2::text[],
                    core_groups_narrowing_reason = NULL,
                    updated_at = now()
                FROM candidate
                WHERE runtime_config.butler_name = $1
                  AND runtime_config.core_groups IS DISTINCT FROM $2::text[]
                RETURNING candidate.previous_core_groups
            )
            INSERT INTO public.audit_log (actor, action, target, note, metadata, result)
            SELECT $1, 'core_groups_reconciled', $1,
                   'Runtime core groups reconciled to Git authority', $4::jsonb, 'success'
            FROM reconciled
            ON CONFLICT DO NOTHING
            RETURNING 1 AS audited
            """,
            butler_name,
            core_groups_val,
            resolution.requires_reconciliation,
            audit_metadata,
        )
        if audited is not None:
            logger.warning(
                "Reconciled runtime core_groups to Git authority for butler=%s toml_digest=%s",
                butler_name,
                toml_digest,
            )

        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._schema}.runtime_config WHERE butler_name = $1",
            butler_name,
        )
        if row is None:
            raise RuntimeError(
                f"Failed to read runtime_config after reconciliation for {butler_name} "
                f"in {self._schema}"
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
